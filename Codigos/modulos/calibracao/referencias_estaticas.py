"""Referencias temporais paradas e diferencas A-B-A para calibracao angular.

Nao depende de camera/mount: callbacks fornecem captura, posicao e relogio.
Os blocos reduzem a influencia de frames isolados, sem assumir independencia
estatistica entre imagens consecutivas.
"""

from collections import Counter, deque
import numpy as np


REFERENCE_WINDOW_S = 2.0
REFERENCE_TARGET_FRAMES = 160
REFERENCE_MAX_WINDOW_S = 6.0
REFERENCE_TIMEOUT_S = 12.0
REFERENCE_SETTLE_S = 0.8
REFERENCE_BLOCK_S = 0.4
REFERENCE_MIN_FRAMES = 60
REFERENCE_MIN_VALID_FRACTION = 0.6
REFERENCE_MAX_BLOCK_SPREAD_PX = 5.0
REFERENCE_STORED_FPS = 80.0
REFERENCE_MAX_HALF_SHIFT_PX = 4.0


def collect_reference(capture, position, centroid, *, clock, quality, audit,
                      expected_angle=None, angle_tolerance=0.0005, final_measurement=None):
    """Espera um trecho recente valido; nao acumula frames atraves de oclusoes.

    `capture`: frame, cm, candidato. `position`: offsets angulares desde a origem.
    `audit` recebe motivos por frame e resultado mesmo quando a coleta falha.
    """
    started = clock()
    recent = deque()
    reasons = Counter()
    last_evaluation = -float("inf")
    audit.update(status="coletando", records=[])
    while clock() - started < REFERENCE_TIMEOUT_S:
        before = clock()
        frame, cm, selected = capture()
        now = clock()
        weight = quality(selected)
        reason = ("sem_candidato" if cm is None else "borda" if cm[3]
                  else "assinatura_rejeitada" if weight <= 0 else "valido")
        if cm is None and selected.get("candidate_count", 0) > 0:
            reason = "candidatos_rejeitados_pelo_detector"
        if reason == "valido" and not np.all(np.isfinite(cm[:2])):
            reason = "centro_nao_finito"
        # Bloqueia tentativa de referenciar um alvo encostado no recorte.
        if reason == "borda":
            audit.update(status="erro", error="Alvo tocou a borda na referencia parada.")
            raise RuntimeError(audit["error"])
        angle = position() if reason == "valido" else None
        mid = (before + now) / 2
        reasons[reason] += 1
        retained = not recent or mid - recent[-1][0] >= 1 / REFERENCE_STORED_FPS
        audit["records"].append(dict(t=mid, reason=reason,
                                     center=None if cm is None else list(map(float, cm[:2])),
                                     angle=angle, candidate_count=selected.get("candidate_count", 0),
                                     retained=retained))
        audit["rejections"] = dict(reasons)
        if not retained:
            continue  # Mantem cobertura temporal sem armazenar centenas de MB a FPS alto.
        # Janela deslizante com limite de memoria, inclusive se houver FPS alto.
        recent.append((mid, np.asarray(frame, dtype=np.uint8).copy() if reason == "valido" else None,
                       cm, weight, angle))
        retained_valid = sum(r[1] is not None for r in recent)
        while recent and (mid - recent[0][0] > REFERENCE_MAX_WINDOW_S + REFERENCE_BLOCK_S
                          or len(recent) > 600 or retained_valid > 200):
            retained_valid -= recent.popleft()[1] is not None
        valid = [r for r in recent if r[1] is not None]
        span = valid[-1][0] - valid[0][0] if valid else 0.0
        if (len(valid) < REFERENCE_MIN_FRAMES or span < REFERENCE_WINDOW_S
                or len(valid) / len(recent) < REFERENCE_MIN_VALID_FRACTION):
            continue
        if len(valid) < REFERENCE_TARGET_FRAMES and span < REFERENCE_MAX_WINDOW_S:
            continue
        if now - last_evaluation < REFERENCE_BLOCK_S:
            continue
        last_evaluation = now
        # Blocos apenas diagnosticam oscilacao; a posicao final usa TODOS os frames.
        ids = np.floor((np.array([r[0] for r in valid]) - valid[0][0]) / REFERENCE_BLOCK_S).astype(int)
        blocks = []
        for index in np.unique(ids):
            group = [r for r, group_id in zip(valid, ids) if group_id == index]
            if len(group) < 3 or group[-1][0] - group[0][0] < REFERENCE_BLOCK_S * 0.5:
                continue
            try:
                xy = centroid([r[1] for r in group], np.array([r[2][:2] for r in group]),
                              np.array([r[3] for r in group]))
            except RuntimeError:
                continue  # Um bloco curto ruim nao substitui a integracao completa.
            blocks.append(dict(t=float(np.median([r[0] for r in group])), center=list(xy),
                               angle=np.median([r[4] for r in group], axis=0).tolist()))
        if len(blocks) < 4:
            continue
        centers = np.array([b["center"] for b in blocks])
        center = np.median(centers, axis=0)
        spread = float(np.percentile(np.linalg.norm(centers - center, axis=1), 90))
        audit["last_block_spread_px"] = spread
        first, second = valid[:len(valid)//2], valid[len(valid)//2:]
        def center_of(group):
            return np.asarray(centroid([r[1] for r in group], np.array([r[2][:2] for r in group]),
                                       np.ones(len(group))), dtype=float)
        try:
            half_shift = float(np.linalg.norm(center_of(first)-center_of(second)))
        except RuntimeError as exc:
            audit['integration_rejection'] = str(exc)
            continue
        audit['last_half_shift_px'] = half_shift
        if not np.isfinite(half_shift) or half_shift > REFERENCE_MAX_HALF_SHIFT_PX:
            continue
        angle_span = np.ptp([b["angle"] for b in blocks], axis=0)
        audit["last_angle_span_deg"] = angle_span.tolist()
        if not np.all(np.isfinite(angle_span)) or np.max(angle_span) > 0.00056:
            continue  # Posicao informada ainda nao estabilizou (limite ~2 arcsec).
        if expected_angle is not None:
            errors = np.array([r[4] for r in valid]) - np.asarray(expected_angle)
            maximum = float(np.max(np.abs(errors)))
            audit["last_target_error_deg"] = maximum
            if not np.isfinite(maximum) or maximum > angle_tolerance + 1e-10:
                continue  # Estar estavel fora do alvo angular nao confirma retorno.
        try:
            center = center_of(valid)
            optical = None
            if final_measurement is not None:
                optical = final_measurement([r[1] for r in valid], np.array([r[2][:2] for r in valid]))
                center = np.asarray(optical['center'])
        except RuntimeError as exc:
            audit['integration_rejection'] = str(exc)
            continue
        result = dict(t=float(np.mean([r[0] for r in valid])), center=center.tolist(),
                      angle=np.median([b["angle"] for b in blocks], axis=0).tolist(),
                      frame_count=len(valid), span_s=span, valid_fraction=len(valid) / len(recent),
                      captured_count=sum(reasons.values()), stored_fps_limit=REFERENCE_STORED_FPS,
                      block_spread_px=spread, blocks=blocks,
                      integration_stability_px=max(.5, half_shift), half_shift_px=half_shift,
                      position_method='centroid_of_full_mean_image',
                      target_frames=REFERENCE_TARGET_FRAMES, integrated_image_metrics=optical)
        audit.update(status="ok", result=result)
        return result
    error = (f"Referencia parada insuficiente em {REFERENCE_TIMEOUT_S:.0f}s; motivos={dict(reasons)}; "
             f"dispersao_blocos_px={audit.get('last_block_spread_px')}; "
             f"variacao_angular_deg={audit.get('last_angle_span_deg')}; "
             f"erro_alvo_angular_deg={audit.get('last_target_error_deg')}; "
             f"diferenca_metades_px={audit.get('last_half_shift_px')}; "
             f"imagem_media={audit.get('integration_rejection')}")
    audit.update(status="erro", error=error)
    raise RuntimeError(error)
