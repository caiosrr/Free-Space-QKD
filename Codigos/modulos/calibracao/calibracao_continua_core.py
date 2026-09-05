"""Calibracao angular-pixel por varredura continua e validacao independente."""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import cv2

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import (
    TEMPORAL_APERTURE_RADIUS_PX,
    TEMPORAL_MEAN_THRESHOLD_PERCENT,
    TRACKER_MAX_SPOT_JUMP_PX,
    roi_size_for_backend,
)
from modulos.controle.alvo_alinhamento import TARGET_FILENAME, roi_incluindo_alvo, salvar_alvo
from modulos.controle.cameras.backend import backend_name, connect_camera, direct_camera, disconnect_camera, set_gain
from modulos.controle.mount_control import (
    TOLERANCIA_GRAUS, VEL_MIN_LIMITE, calc_error, ensure_connected, ensure_not_tracking,
    ensure_unparked, move_axes_pid_2d, move_axis, read_altaz, stop_axes_safely,
)
from modulos.controle.mapa_jacobianas import registrar_no
from modulos.visao import detector_ilhas as foco
from modulos.calibracao.referencias_estaticas import (
    REFERENCE_WINDOW_S, REFERENCE_SETTLE_S, REFERENCE_TIMEOUT_S, REFERENCE_MIN_FRAMES,
    REFERENCE_TARGET_FRAMES, REFERENCE_MAX_WINDOW_S,
    collect_reference,
)
from modulos.calibracao.imagem_integrada import integrated_centroid, measure_integrated_beacon
from modulos.calibracao.resposta_local import (
    LOCAL_STEP_DEG, LOCAL_REPETITIONS, measure_step, matrix_step_usable, timed_pulse,
)


LOCAL_HALF_RANGE_DEG = 0.008
WIDE_HALF_RANGE_DEG = 0.014
SWEEP_RATE_DEG_S = 0.002
SWEEP_HALF_RANGE_DEG = LOCAL_HALF_RANGE_DEG
OTHER_AXIS_LIMIT_DEG = 0.004
RETURN_MAX_RATE_DEG_S = 0.02
RETURN_ATTEMPTS = 2
RETURN_STABLE_SECONDS = 1.5
RETURN_VERIFY_TIMEOUT_S = 4.0
RETURN_POLL_SECONDS = 0.2
RETURN_STABLE_SPAN_DEG = 1.0 / 3600.0
BASELINE_VALID_FRAMES = 20
BASELINE_WINDOW_SECONDS = 0.5
SIGNAL_LOSS_TIMEOUT_S = 1.5
MIN_VALID_SWEEP_SAMPLES = 20
ANGLE_BIN_WIDTH_DEG = 0.00025
MIN_FRAMES_PER_ANGLE_BIN = 3
# O ASCOM pode repetir a mesma coordenada por varios frames e atualizar a
# posicao em degraus. Oito bins preservam apenas estados angulares independentes
# sem confundir taxa da camera com taxa de telemetria do mount.
MIN_VALID_SWEEP_BINS = 8
MAX_MEDIAN_BIN_SPREAD_PX = 5.0
MAX_P90_BIN_SPREAD_PX = 10.0
MIN_CALIBRATION_SIMILARITY = 0.25
# Independente da ROI enxuta do tracker: os testes amplos precisam de margem.
CALIBRATION_ROI_SIZE_PX = 512
# Limites iniciais de repetibilidade, nao uma garantia de precisao subpixel.
MAX_DIRECTION_SCALE_RATIO = 1.35
MIN_DIRECTION_COSINE = 0.98
MAX_HOLDOUT_RELATIVE_RMS = 0.25
HOLDOUT_NOISE_FLOOR_PX = 3.0
HUBER_K = 1.5
ROBUST_ITERS = 10
STATIONARY_FRACTIONS = (0.25, 0.50, 0.75, 1.0)


@dataclass(frozen=True)
class SweepSpec:
    name: str
    axis: int
    command_sign: int
    half_range_deg: float
    role: str


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    description: str
    specs: tuple[SweepSpec, ...]
    requires_holdout: bool


@dataclass
class SweepSample:
    run: str
    axis: int
    command_sign: int
    elapsed_s: float
    az_deg: float
    alt_deg: float
    delta_az_deg: float
    delta_alt_deg: float
    x_px: float
    y_px: float
    role: str = "fit"
    half_range_deg: float = LOCAL_HALF_RANGE_DEG
    capture_duration_s: float = 0.0
    capture_mid_epoch: float = 0.0
    frames_combined: int = 1
    centroid_spread_px: float = 0.0
    quality_weight: float = 1.0
    raw_centroid_spread_px: float = 0.0
    bin_duration_s: float = 0.0
    trend_x_px_s: float = 0.0
    trend_y_px_s: float = 0.0
    sample_kind: str = "moving_frame"


def _four_sweeps(role: str, amplitude: float, order: tuple[tuple[int, int], ...]):
    labels = {0: "az", 1: "alt"}
    return tuple(
        SweepSpec(
            f"{role}_{labels[axis]}_{'pos' if sign > 0 else 'neg'}",
            axis, sign, amplitude, role,
        )
        for axis, sign in order
    )


def calibration_profile(name: str) -> CalibrationProfile:
    normalized = str(name).strip().lower()
    if normalized in {"rapido", "quick"}:
        return CalibrationProfile(
            "rapido",
            "4 sequencias locais repetidas com controles sem movimento; cerca de 3-4 min",
            _four_sweeps("fit", LOCAL_HALF_RANGE_DEG, ((0, +1), (1, -1), (0, -1), (1, +1))),
            False,
        )
    if normalized not in {"robusto", "robust", ""}:
        raise ValueError("Perfil invalido. Use 'rapido' ou 'robusto'.")
    fit = _four_sweeps("fit", LOCAL_HALF_RANGE_DEG, ((0, +1), (1, -1), (0, -1), (1, +1)))
    holdout = _four_sweeps("holdout_local", LOCAL_HALF_RANGE_DEG, ((1, +1), (0, -1), (1, -1), (0, +1)))
    return CalibrationProfile(
        "robusto",
        "4 ajustes + 4 validacoes locais; medias de 160 frames e diagnostico de micropulsos; cerca de 8-12 min",
        fit + holdout,
        True,
    )


def _offsets_from_start(initial_az: float, initial_alt: float, az: float, alt: float):
    return float(calc_error(0, az, initial_az)), float(alt - initial_alt)


def _confirm_stable_return(initial_az, initial_alt, result):
    """Exige permanencia dentro da tolerancia, nao uma passagem pelo alvo."""
    started = time.perf_counter()
    stable = []
    while time.perf_counter() - started < RETURN_VERIFY_TIMEOUT_S:
        az, alt = read_altaz()
        now = time.perf_counter()
        errors = (float(calc_error(0, initial_az, az)), float(initial_alt - alt))
        if not np.all(np.isfinite([az, alt, *errors])):
            raise RuntimeError("Posicao nao finita durante verificacao do retorno.")
        result.update(final_az_deg=az, final_alt_deg=alt,
                      error_az_deg=errors[0], error_alt_deg=errors[1])
        result["readings"].append(dict(t=now, attempt=result["attempts"],
                                       phase="confirmacao_parado", az_deg=az, alt_deg=alt,
                                       error_az_deg=errors[0], error_alt_deg=errors[1]))
        if max(map(abs, errors)) <= TOLERANCIA_GRAUS:
            stable.append((now, errors))
            while len(stable) > 1 and np.max(np.ptp([r[1] for r in stable], axis=0)) > RETURN_STABLE_SPAN_DEG + 1e-10:
                stable.pop(0)
            if len(stable) >= 5 and now - stable[0][0] >= RETURN_STABLE_SECONDS:
                result.update(stable_seconds=now-stable[0][0], stable_readings=len(stable))
                return True
        else:
            stable.clear()
        time.sleep(RETURN_POLL_SECONDS)
    return False


def _return_to_absolute_start(initial_az: float, initial_alt: float, *, audit_path=None) -> dict:
    result = {"success": False, "attempts": 0, "error": None, "readings": [], "commands": [],
              "target_az_deg": initial_az, "target_alt_deg": initial_alt,
              "required_stable_seconds": RETURN_STABLE_SECONDS, "tolerance_deg": TOLERANCIA_GRAUS,
              "stable_span_limit_deg": RETURN_STABLE_SPAN_DEG,
              "verification_timeout_seconds": RETURN_VERIFY_TIMEOUT_S}
    try:
        if not stop_axes_safely():
            raise RuntimeError("Parada dos eixos nao confirmada antes do retorno.")
        for attempt in range(1, RETURN_ATTEMPTS + 1):
            az, alt = read_altaz()
            delta_az = float(calc_error(0, initial_az, az))
            delta_alt = float(initial_alt - alt)
            result["attempts"] = attempt
            if not np.all(np.isfinite([az, alt, delta_az, delta_alt])):
                raise RuntimeError("Posicao nao finita antes do retorno.")
            result["readings"].append(dict(t=time.perf_counter(), attempt=attempt, phase="antes_comando",
                                           az_deg=az, alt_deg=alt, error_az_deg=delta_az, error_alt_deg=delta_alt))
            if max(abs(delta_az), abs(delta_alt)) > 0.05:
                raise RuntimeError("Retorno automatico recusado: deslocamento maior que 0.05 deg.")
            if max(abs(delta_az), abs(delta_alt)) > TOLERANCIA_GRAUS:
                print(f"Retorno {attempt}/{RETURN_ATTEMPTS}: dAz={delta_az:+.5f} dAlt={delta_alt:+.5f} deg")
                command = dict(started_t=time.perf_counter(), delta_az_deg=delta_az,
                               delta_alt_deg=delta_alt, max_rate_deg_s=RETURN_MAX_RATE_DEG_S)
                result["commands"].append(command)
                move_axes_pid_2d(
                    True, delta_az, delta_alt, max_velocity_deg_s=RETURN_MAX_RATE_DEG_S,
                    absolute_target=(initial_az, initial_alt),
                    telemetry_callback=lambda row: result["readings"].append(
                        {"attempt": attempt, "phase": "movimento_pid", **row}),
                )
                command["finished_t"] = time.perf_counter()
            if not stop_axes_safely():
                raise RuntimeError("Parada dos eixos nao confirmada apos retorno.")
            if _confirm_stable_return(initial_az, initial_alt, result):
                result["success"] = True
                break
        if not result["success"]:
            result["error"] = "Retorno nao permaneceu estavel dentro da tolerancia."
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if not stop_axes_safely():
            result.update(success=False, error="Parada final dos eixos nao confirmada.")
        if audit_path is not None:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _capture_valid_cm():
    frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
    cm = foco.centro_massa(frame)
    debug = foco.get_focus_debug()
    selected = (debug.get("selected") or {}).copy()
    selected["candidate_count"] = int(debug.get("candidate_count", 0))
    return frame, cm, selected


def _frame_quality(selected: dict) -> float:
    """Peso da identidade da ilha; zero descarta um candidato duvidoso."""
    similarity = selected.get("similarity_primary")
    if similarity is None:
        return 1.0
    try:
        similarity = float(similarity)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(similarity) or similarity < MIN_CALIBRATION_SIMILARITY:
        return 0.0
    return float(np.clip(similarity, MIN_CALIBRATION_SIMILARITY, 1.0))


def _centroid_from_stacked_frames(
    frames: list[np.ndarray],
    centers: np.ndarray,
    frame_weights: np.ndarray | None = None,
) -> tuple[float, float]:
    """Mede o CM da soma curta sem misturar regioes distantes da ROI."""
    if not frames or centers.shape != (len(frames), 2):
        raise ValueError("Frames e centros invalidos para integracao curta.")
    shape = frames[0].shape
    if len(shape) != 2 or any(frame.shape != shape for frame in frames):
        raise ValueError("Todos os frames combinados precisam ter o mesmo tamanho.")

    if frame_weights is None:
        frame_weights = np.ones(len(frames), dtype=float)
    frame_weights = np.asarray(frame_weights, dtype=float)
    if frame_weights.shape != (len(frames),) or not np.all(np.isfinite(frame_weights)):
        raise ValueError("Pesos invalidos para integracao curta.")
    frame_weights = np.clip(frame_weights, 0.05, 1.0)

    stacked = np.zeros(shape, dtype=np.float32)
    for frame, weight in zip(frames, frame_weights):
        stacked += np.asarray(frame, dtype=np.float32) * float(weight)
    stacked /= float(np.sum(frame_weights))

    expected_x, expected_y = np.median(centers, axis=0)
    height, width = shape
    radius = int(TEMPORAL_APERTURE_RADIUS_PX)
    x0 = max(0, int(np.floor(expected_x - radius)))
    x1 = min(width, int(np.ceil(expected_x + radius + 1)))
    y0 = max(0, int(np.floor(expected_y - radius)))
    y1 = min(height, int(np.ceil(expected_y + radius + 1)))
    local = stacked[y0:y1, x0:x1].copy()
    if local.size == 0:
        raise RuntimeError("Integracao curta produziu uma janela vazia.")

    yy, xx = np.indices(local.shape, dtype=np.float32)
    local_x = expected_x - x0
    local_y = expected_y - y0
    aperture = ((xx - local_x) ** 2 + (yy - local_y) ** 2) <= radius**2
    pedestal = float(np.median(local[aperture])) if np.any(aperture) else 0.0
    weights = np.clip(local - pedestal, 0.0, None)
    weights[~aperture] = 0.0
    peak = float(weights.max())
    if peak <= 0.0:
        raise RuntimeError("Sinal insuficiente na integracao curta.")
    weights[weights < peak * TEMPORAL_MEAN_THRESHOLD_PERCENT] = 0.0
    total = float(weights.sum())
    if total <= 0.0:
        raise RuntimeError("Centro de massa vazio na integracao curta.")
    return (
        float(x0 + (xx * weights).sum() / total),
        float(y0 + (yy * weights).sum() / total),
    )


def _baseline_anchor(signature: dict, expected_x: float, expected_y: float):
    if not foco.initialize_focus_lock(
        signature, expected_x, expected_y, freeze_reference=True,
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    ):
        raise RuntimeError("Nao consegui inicializar a assinatura da luz.")
    frames, centers, weights = [], [], []
    first_valid_t = None
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline:
        frame, cm, selected = _capture_valid_cm()
        quality = _frame_quality(selected)
        if cm is not None and not cm[3] and quality > 0.0:
            now = time.perf_counter()
            first_valid_t = now if first_valid_t is None else first_valid_t
            frames.append(np.ascontiguousarray(frame, dtype=np.uint8).copy())
            centers.append((float(cm[0]), float(cm[1])))
            weights.append(quality)
            if (
                len(frames) >= BASELINE_VALID_FRAMES
                and now - first_valid_t >= BASELINE_WINDOW_SECONDS
            ):
                break
    if len(frames) < BASELINE_VALID_FRAMES:
        raise RuntimeError(
            f"Baseline insuficiente: {len(frames)}/{BASELINE_VALID_FRAMES} "
            "frames validos."
        )
    anchor = _centroid_from_stacked_frames(
        frames,
        np.asarray(centers, dtype=float),
        np.asarray(weights, dtype=float),
    )
    foco.set_focus_expected_position(*anchor, max_jump_px=TRACKER_MAX_SPOT_JUMP_PX)
    return anchor


def _sweep_motion_trend(times: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Velocidade visual robusta da varredura inteira, apenas para diagnostico.

    Nao ajustamos uma reta livre em cada bin (poucos frames esconderiam ruido).
    A tendencia pode incluir deriva lenta: o residuo NAO mede so turbulencia.
    """
    if len(times) < 3 or float(np.ptp(times)) < 1e-6:
        return np.zeros(2)
    design = np.column_stack((np.ones(len(times)), times - np.median(times)))
    weights = np.ones(len(times))
    for _ in range(ROBUST_ITERS):
        root_w = np.sqrt(weights)[:, None]
        beta = np.linalg.lstsq(design * root_w, centers * root_w, rcond=None)[0]
        residual = np.linalg.norm(centers - design @ beta, axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        weights = np.minimum(1.0, HUBER_K * scale / np.maximum(residual, 1e-9))
    return beta[1]


def _aggregate_sweep_frames(
    captures: list[tuple[SweepSample, np.ndarray, float]],
) -> list[SweepSample]:
    """Combina frames por angulo informado; a telemetria pode ter patamares."""
    if not captures:
        return []
    first_sample = captures[0][0]
    times = np.asarray([sample.elapsed_s for sample, _, _ in captures])
    all_centers = np.asarray([[sample.x_px, sample.y_px] for sample, _, _ in captures])
    trend = _sweep_motion_trend(times, all_centers)
    active_values = np.asarray(
        [
            sample.delta_az_deg if sample.axis == 0 else sample.delta_alt_deg
            for sample, _, _ in captures
        ],
        dtype=float,
    )
    origin = float(np.min(active_values))
    bin_ids = np.floor(
        (active_values - origin + 1e-12) / ANGLE_BIN_WIDTH_DEG
    ).astype(int)

    aggregated = []
    for bin_id in np.unique(bin_ids):
        indices = np.flatnonzero(bin_ids == bin_id)
        if len(indices) < MIN_FRAMES_PER_ANGLE_BIN:
            continue
        group = [captures[int(index)] for index in indices]
        samples = [item[0] for item in group]
        frames = [item[1] for item in group]
        frame_weights = np.asarray([item[2] for item in group], dtype=float)
        centers = np.asarray([[sample.x_px, sample.y_px] for sample in samples])
        x_px, y_px = _centroid_from_stacked_frames(
            frames,
            centers,
            frame_weights,
        )
        raw_radial = np.linalg.norm(centers - np.median(centers, axis=0), axis=1)
        # Desconta o deslocamento no intervalo do bin SOMENTE na dispersao.
        # Imagens, centroides e angulos usados no ajuste da matriz ficam intactos.
        group_times = times[indices]
        detrended = centers - (group_times - np.median(group_times))[:, None] * trend
        radial = np.linalg.norm(detrended - np.median(detrended, axis=0), axis=1)
        spread = float(np.percentile(radial, 90.0))
        frames_combined = len(samples)
        quality_weight = float(
            frames_combined / max(spread * spread, 0.25)
        )

        def median(field: str) -> float:
            return float(np.median([getattr(sample, field) for sample in samples]))

        aggregated.append(
            SweepSample(
                run=first_sample.run,
                axis=first_sample.axis,
                command_sign=first_sample.command_sign,
                elapsed_s=median("elapsed_s"),
                az_deg=median("az_deg"),
                alt_deg=median("alt_deg"),
                delta_az_deg=median("delta_az_deg"),
                delta_alt_deg=median("delta_alt_deg"),
                x_px=x_px,
                y_px=y_px,
                role=first_sample.role,
                half_range_deg=first_sample.half_range_deg,
                capture_duration_s=median("capture_duration_s"),
                capture_mid_epoch=median("capture_mid_epoch"),
                frames_combined=frames_combined,
                centroid_spread_px=spread,
                quality_weight=quality_weight,
                raw_centroid_spread_px=float(np.percentile(raw_radial, 90.0)),
                bin_duration_s=float(np.ptp(group_times)),
                trend_x_px_s=float(trend[0]),
                trend_y_px_s=float(trend[1]),
            )
        )
    return aggregated


def _validate_sweep_aggregation(samples: list[SweepSample], run_name: str) -> dict:
    if len(samples) < MIN_VALID_SWEEP_BINS:
        raise RuntimeError(
            f"{run_name}: somente {len(samples)} bins angulares validos; "
            f"minimo={MIN_VALID_SWEEP_BINS}. Verifique FPS e sinal."
        )
    spreads = np.asarray([sample.centroid_spread_px for sample in samples])
    median_spread = float(np.median(spreads))
    p90_spread = float(np.percentile(spreads, 90.0))
    if (
        median_spread > MAX_MEDIAN_BIN_SPREAD_PX
        or p90_spread > MAX_P90_BIN_SPREAD_PX
    ):
        raise RuntimeError(
            f"{run_name}: dispersao residual excessiva nos bins (apos tendencia) "
            f"(mediana={median_spread:.2f}px; p90={p90_spread:.2f}px)."
        )
    return {
        "bin_count": len(samples),
        "median_frames_per_bin": float(
            np.median([sample.frames_combined for sample in samples])
        ),
        "median_centroid_spread_px": median_spread,
        "p90_centroid_spread_px": p90_spread,
        "spread_method": "global_robust_linear_time_trend_removed",
        "median_raw_centroid_spread_px": float(np.median(
            [sample.raw_centroid_spread_px for sample in samples]
        )),
        "max_bin_duration_s": max(sample.bin_duration_s for sample in samples),
        "trend_px_s": [samples[0].trend_x_px_s, samples[0].trend_y_px_s],
    }


def _run_one_sweep(*, spec: SweepSpec, initial_az: float, initial_alt: float,
                   signature: dict, center_anchor, audit_dir: Path | None = None,
                   baseline=True, validate_dynamic=True):
    if audit_dir is not None:
        audit_dir.mkdir(parents=True, exist_ok=True)
    if baseline:
        center_anchor = _baseline_anchor(signature, *center_anchor)
    captures: list[tuple[SweepSample, np.ndarray, float]] = []
    started = time.perf_counter()
    started_epoch = time.time()
    last_valid, last_print = started, 0.0
    timeout_s = spec.half_range_deg / SWEEP_RATE_DEG_S * 3.0 + 4.0
    hard_limit = spec.half_range_deg + max(0.004, 2.0 * TOLERANCIA_GRAUS)
    print(
        f"\n{spec.name}: eixo={'Az' if spec.axis == 0 else 'Alt'} comando={spec.command_sign:+d} | "
        f"amplitude={spec.half_range_deg:.4f} deg | velocidade={SWEEP_RATE_DEG_S:.4f} deg/s"
    )
    try:
        # Patamares usam limites cumulativos; nunca iniciar um passo ja atingido.
        start_offsets = _offsets_from_start(initial_az, initial_alt, *read_altaz())
        if abs(start_offsets[spec.axis]) >= spec.half_range_deg:
            raise RuntimeError(f"{spec.name}: patamar ja atingido antes do movimento.")
        move_axis(spec.axis, spec.command_sign * SWEEP_RATE_DEG_S, True)
        while True:
            loop_t = time.perf_counter()
            if loop_t - started > timeout_s:
                raise RuntimeError(f"Tempo limite na varredura {spec.name}.")
            az_before, alt_before = read_altaz()
            capture_started = time.perf_counter()
            frame, cm, selected = _capture_valid_cm()
            capture_finished = time.perf_counter()
            az_after, alt_after = read_altaz()
            before = _offsets_from_start(initial_az, initial_alt, az_before, alt_before)
            after = _offsets_from_start(initial_az, initial_alt, az_after, alt_after)
            daz, dalt = 0.5 * (before[0] + after[0]), 0.5 * (before[1] + after[1])
            active = daz if spec.axis == 0 else dalt
            other = dalt if spec.axis == 0 else daz
            if abs(active) > hard_limit:
                raise RuntimeError(f"Watchdog: {spec.name} excedeu {hard_limit:.3f} deg.")
            if active * spec.command_sign < -0.001:
                raise RuntimeError(f"Watchdog: {spec.name} moveu no sentido angular oposto.")
            if abs(other) > OTHER_AXIS_LIMIT_DEG:
                raise RuntimeError(f"Watchdog: outro eixo derivou {other:+.4f} deg.")
            quality = _frame_quality(selected)
            if cm is not None and not cm[3] and quality > 0.0:
                last_valid = time.perf_counter()
                capture_mid = 0.5 * (capture_started + capture_finished)
                sample = SweepSample(
                    spec.name, spec.axis, spec.command_sign, capture_mid - started,
                    0.5 * (az_before + az_after), 0.5 * (alt_before + alt_after),
                    daz, dalt, float(cm[0]), float(cm[1]), spec.role,
                    spec.half_range_deg, capture_finished - capture_started,
                    started_epoch + capture_mid - started,
                )
                captures.append(
                    (
                        sample,
                        np.ascontiguousarray(frame, dtype=np.uint8).copy(),
                        quality,
                    )
                )
            elif cm is not None and cm[3]:
                raise RuntimeError(f"A luz tocou a borda durante {spec.name}.")
            if time.perf_counter() - last_valid > SIGNAL_LOSS_TIMEOUT_S:
                raise RuntimeError(f"Luz perdida por mais de {SIGNAL_LOSS_TIMEOUT_S:.1f}s.")
            if loop_t - last_print >= 0.5:
                cm_text = "sem sinal" if cm is None else f"CM=({cm[0]:.1f},{cm[1]:.1f})"
                print(f"  offset={active:+.5f} deg | {cm_text} | validos={len(captures)}")
                last_print = loop_t
            if abs(active) >= spec.half_range_deg:
                break
    finally:
        stopped = stop_axes_safely()
        # Primeiro parar os eixos; depois preservar ate uma varredura abortada.
        if audit_dir is not None:
            try:
                _write_csv(audit_dir / f"{spec.name}_frames.csv", [[item[0] for item in captures]])
            except OSError as exc:
                print(f"Aviso: nao consegui salvar frames de {spec.name}: {exc}")
        if not stopped:
            raise RuntimeError(f"Parada nao confirmada em {spec.name}.")
    # Em passos curtos estes frames sao apenas vigilancia, nunca o estimador.
    minimum = (max(MIN_VALID_SWEEP_SAMPLES, int(spec.half_range_deg / SWEEP_RATE_DEG_S * 5))
               if validate_dynamic else 3)
    if len(captures) < minimum:
        raise RuntimeError(f"{spec.name}: somente {len(captures)} amostras; minimo={minimum}.")
    raw_samples = [item[0] for item in captures]
    # O inicio transitorio continua no CSV bruto, mas fica fora do diagnostico
    # dinamico quando a matriz e estimada por referencias paradas.
    useful = captures if validate_dynamic else [item for item in captures if item[0].elapsed_s >= 1.0]
    samples = _aggregate_sweep_frames(useful)
    if audit_dir is not None:
        _write_csv(audit_dir / f"{spec.name}_bins.csv", [samples])
    try:
        aggregation = _validate_sweep_aggregation(samples, spec.name)
    except RuntimeError as exc:
        if validate_dynamic:
            raise
        aggregation = {"dynamic_warning": str(exc), "bin_count": len(samples)}
    aggregation["transient_excluded_s"] = 0.0 if validate_dynamic else 1.0
    if not validate_dynamic:
        print(f"  {len(raw_samples)} frames; referencias paradas definirao a matriz.")
        return samples, center_anchor, raw_samples, aggregation
    values = np.array([s.delta_az_deg if spec.axis == 0 else s.delta_alt_deg for s in samples])
    span = float(np.ptp(values))
    if span < max(0.003, 0.65 * spec.half_range_deg):
        raise RuntimeError(f"{spec.name}: amplitude medida insuficiente ({span:.5f} deg).")
    print(
        f"  concluida: {len(raw_samples)} frames -> {len(samples)} bins | "
        f"{aggregation['median_frames_per_bin']:.1f} frames/bin | "
        f"dispersao residual={aggregation['median_centroid_spread_px']:.2f}px "
        f"(bruta={aggregation['median_raw_centroid_spread_px']:.2f}px) | "
        f"amplitude={span:.5f} deg."
    )
    return samples, center_anchor, raw_samples, aggregation


def _take_stationary_reference(signature, expected, initial_az, initial_alt, audit_path,
                               *, expected_angle=None):
    audit = {"settle_seconds": REFERENCE_SETTLE_S, "expected_angle_deg": expected_angle}
    def final_measurement(frames, centers):
        result = measure_integrated_beacon(frames, centers)
        preview = result.pop('preview')
        preview_path = audit_path.with_suffix('.png')
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(preview_path), preview):
            raise OSError(f'Nao consegui salvar imagem media: {preview_path}')
        result['preview_file'] = preview_path.name
        result['preview_display_only_normalized'] = True
        return result
    try:
        if not stop_axes_safely():
            raise RuntimeError("Parada dos eixos nao confirmada antes da referencia.")
        time.sleep(REFERENCE_SETTLE_S)
        if not foco.initialize_focus_lock(signature, *expected, freeze_reference=True,
                                         max_jump_px=TRACKER_MAX_SPOT_JUMP_PX):
            raise RuntimeError("Nao consegui inicializar a ilha para referencia parada.")
        result = collect_reference(
            _capture_valid_cm,
            lambda: _offsets_from_start(initial_az, initial_alt, *read_altaz()),
            integrated_centroid,
            clock=time.perf_counter, quality=_frame_quality, audit=audit,
            expected_angle=expected_angle, angle_tolerance=TOLERANCIA_GRAUS,
            final_measurement=final_measurement,
        )
        foco.set_focus_expected_position(*result["center"], max_jump_px=TRACKER_MAX_SPOT_JUMP_PX)
        return result
    except Exception as exc:
        audit.update(status="erro", error=str(exc))
        raise
    finally:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_reference_sweep(*, spec, initial_az, initial_alt, signature, center_anchor, audit_dir):
    """Mede quatro patamares no mesmo sentido; retorno nao participa do ajuste.

    O trecho ate P1 acomoda a inversao. Cada escada tem intercepto proprio,
    portanto deslocamentos opticos entre retornos nao viram uma falsa escala.
    Deriva atmosferica lenta ainda e verificada nas direcoes e holdouts.
    """
    def reference(label, expected, expected_angle=None):
        print(f"  {spec.name}: referencia {label}, mount parado, media >= {REFERENCE_WINDOW_S:.1f}s")
        try:
            return _take_stationary_reference(signature, expected, initial_az, initial_alt,
                                              audit_dir / f"{spec.name}_referencia_{label}.json",
                                              expected_angle=expected_angle)
        except RuntimeError as exc:
            raise RuntimeError(f"{spec.name}/{label}: {exc}") from exc
    before = reference("A", center_anchor, (0.0, 0.0))
    samples, raw_samples, references, steps = [], [], [], []
    anchor = tuple(before["center"])
    for index, fraction in enumerate(STATIONARY_FRACTIONS, 1):
        step = SweepSpec(f"{spec.name}_P{index}", spec.axis, spec.command_sign,
                         fraction * spec.half_range_deg, spec.role)
        _, _, raw, stats = _run_one_sweep(
            spec=step, initial_az=initial_az, initial_alt=initial_alt, signature=signature,
            center_anchor=anchor, audit_dir=audit_dir, baseline=False, validate_dynamic=False,
        )
        raw_samples.extend(raw)
        steps.append(stats)
        point = reference(f"P{index}", (raw[-1].x_px, raw[-1].y_px))
        references.append(point)
        anchor = tuple(point["center"])
        daz, dalt = point["angle"]
        spread = max(0.5, point["block_spread_px"])
        samples.append(SweepSample(
            run=spec.name, axis=spec.axis, command_sign=spec.command_sign,
            elapsed_s=point["t"] - before["t"],
            az_deg=(initial_az + daz) % 360, alt_deg=initial_alt + dalt,
            delta_az_deg=daz, delta_alt_deg=dalt, x_px=anchor[0], y_px=anchor[1],
            role=spec.role, half_range_deg=spec.half_range_deg,
            frames_combined=point["frame_count"], centroid_spread_px=spread,
            quality_weight=1 / spread**2, sample_kind="stationary_monotonic_plateau",
        ))
    _write_csv(audit_dir / f"{spec.name}_patamares.csv", [samples])
    # Audita antes de validar, inclusive se os patamares forem rejeitados.
    diagnostics = {"method": "monotonic_stationary_plateaus", "return_used_for_fit": False,
                   "reference_count": len(references) + 2,
                   "valid_reference_frames": before["frame_count"] + sum(p["frame_count"] for p in references),
                   "steps": steps}
    try:
        diagnostics.update(_validate_stationary_staircase(samples, spec), status="patamares_validados")
    except RuntimeError as exc:
        diagnostics.update(status="erro", error=str(exc))
        raise
    finally:
        (audit_dir / f"{spec.name}_escada.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    returned = _return_to_absolute_start(
        (initial_az + before["angle"][0]) % 360.0, initial_alt + before["angle"][1],
        audit_path=audit_dir / f"{spec.name}_retorno.json",
    )
    if not returned["success"]:
        raise RuntimeError(f"Retorno para referencia A' falhou: {returned.get('error')}")
    after = reference("A_retorno", tuple(before["center"]), before["angle"])
    diagnostics["valid_reference_frames"] += after["frame_count"]
    diagnostics["optical_return_px"] = float(np.linalg.norm(np.array(after["center"]) - before["center"]))
    (audit_dir / f"{spec.name}_escada.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    print(f"  Patamares: {len(samples)} | retorno optico (diagnostico)={diagnostics['optical_return_px']:.2f}px")
    return samples, tuple(after["center"]), raw_samples, diagnostics


def _validate_stationary_staircase(samples, spec):
    """Exige excitacao e linearidade dentro da escada, sem usar o retorno."""
    if len(samples) != len(STATIONARY_FRACTIONS):
        raise RuntimeError(f"{spec.name}: faltam patamares independentes.")
    q = np.array([[s.delta_az_deg, s.delta_alt_deg] for s in samples])
    p = np.array([[s.x_px, s.y_px] for s in samples])
    if not np.all(np.isfinite(np.r_[q.ravel(), p.ravel()])):
        raise RuntimeError(f"{spec.name}: patamares nao finitos.")
    active = q[:, spec.axis]
    if np.any(np.diff(active) * spec.command_sign < 0.0005):
        raise RuntimeError(f"{spec.name}: passos nao monotonicos ou pequenos demais.")
    span = float(np.ptp(active))
    if span < 0.60 * spec.half_range_deg:
        raise RuntimeError(f"{spec.name}: amplitude util insuficiente apos acomodacao.")
    if np.ptp(q[:, 1-spec.axis]) > 0.0005:
        raise RuntimeError(f"{spec.name}: eixo ortogonal variou entre patamares.")
    slope = _direction_slope(samples, spec.axis)
    intercept = np.median(p - active[:, None] * slope, axis=0)
    residual = np.linalg.norm(p - (intercept + active[:, None] * slope), axis=1)
    response = float(np.linalg.norm(slope) * span)
    rms = float(np.sqrt(np.mean(residual**2)))
    noise = float(np.median([s.centroid_spread_px for s in samples]))
    if response < max(8.0, 4 * noise):
        raise RuntimeError(f"{spec.name}: resposta insuficiente frente a dispersao parada.")
    if rms > max(3.0, 0.15 * response):
        raise RuntimeError(f"{spec.name}: patamares nao lineares (RMS={rms:.2f}px).")
    return dict(useful_span_deg=span, slope_px_per_deg=slope.tolist(),
                plateau_rms_px=rms, response_px=response)


def _run_local_sequence(*, spec, initial_az, initial_alt, signature, center_anchor, audit_dir):
    """Repete passos locais e mede deriva em trechos parados antes/depois.

    A escala vem dos offsets angulares medidos. Micropulsos ficam apenas no
    diagnostico: podem ser menores que a resolucao do driver ou que o ruido.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = dict(method="paired_local_steps_with_stationary_controls", status="coletando",
                 return_used_for_fit=False, reference_count=0, valid_reference_frames=0,
                 steps=[], micropulses=[])
    anchor = center_anchor
    samples, raw_samples = [], []

    def reference(label, expected_angle=None):
        nonlocal anchor
        r = _take_stationary_reference(signature, anchor, initial_az, initial_alt,
                                       audit_dir / f"{spec.name}_{label}.json",
                                       expected_angle=expected_angle)
        anchor = tuple(r['center'])
        audit['reference_count'] += 1
        audit['valid_reference_frames'] += r['frame_count']
        return r

    def limit_check():
        offsets = _offsets_from_start(initial_az, initial_alt, *read_altaz())
        if not np.all(np.isfinite(offsets)) or max(map(abs, offsets)) > spec.half_range_deg + .002:
            raise RuntimeError('Limite local excedido; interrompendo sequencia.')

    try:
        before = [reference('antes_0', (0., 0.)), reference('antes_1')]
        origin = before[0]
        for index in range(LOCAL_REPETITIONS):
            limit_check()
            step = SweepSpec(f"{spec.name}_passo_{index+1}", spec.axis, spec.command_sign,
                             LOCAL_STEP_DEG, spec.role)
            # O destino e incremental a partir do angulo atualmente MEDIDO.
            step_az, step_alt = read_altaz()
            _, _, raw, _ = _run_one_sweep(
                spec=step, initial_az=step_az, initial_alt=step_alt, signature=signature,
                center_anchor=anchor, audit_dir=audit_dir, baseline=False, validate_dynamic=False)
            raw_samples.extend(raw)
            anchor = (raw[-1].x_px, raw[-1].y_px)
            after = [reference(f'passo_{index+1}_depois_0'), reference(f'passo_{index+1}_depois_1')]
            result = measure_step(before + after)
            usable, reason = matrix_step_usable(result, spec.axis, spec.command_sign)
            audit['steps'].append(dict(index=index+1, usable=usable, reason=reason, **result))
            if usable:
                # Pares simetricos mantem origem zero sem um intercepto espurio.
                for factor in (-.5, .5):
                    q = factor*np.array(result['delta_deg'])
                    p = factor*np.array(result['displacement_px'])
                    samples.append(SweepSample(
                        spec.name, spec.axis, spec.command_sign, after[0]['t']-origin['t'],
                        *q, *q, *p, role=spec.role, half_range_deg=LOCAL_STEP_DEG,
                        frames_combined=result['frame_count'],
                        centroid_spread_px=result['reference_noise_px'],
                        quality_weight=1/result['reference_noise_px']**2,
                        sample_kind='paired_local_step_difference'))
            print(f"  {spec.name}/{index+1}: {reason}; resposta={np.linalg.norm(result['displacement_px']):.2f}px; "
                  f"variacao de referencia={result['reference_noise_px']:.2f}px")
            before = after  # Mesmo patamar: reusa dados, sem contar como novas amostras independentes.
        limit_check()
        if len(samples) < 4:
            raise RuntimeError(f'{spec.name}: menos de dois passos distinguiveis da variacao sem comando.')
        if spec.role == 'fit':
            # Inversao seguida de repeticao no mesmo sentido: nao altera matriz/gains.
            for index in range(2):
                limit_check()
                pulse = timed_pulse(spec.axis, -spec.command_sign, VEL_MIN_LIMITE,
                                    move=move_axis, stop=stop_axes_safely,
                                    clock=time.perf_counter, sleep=time.sleep)
                after = [reference(f'micropulso_{index+1}_depois_0'), reference(f'micropulso_{index+1}_depois_1')]
                result = measure_step(before + after)
                audit['micropulses'].append(dict(
                    kind='primeiro_apos_inversao' if index == 0 else 'repetido_mesmo_sentido',
                    timing=pulse, optically_resolved=bool(np.linalg.norm(result['displacement_px']) > 2*result['reference_noise_px']),
                    used_for_matrix=False, **result))
                before = after
        limit_check()
        returned = _return_to_absolute_start(initial_az, initial_alt,
                                            audit_path=audit_dir / f'{spec.name}_retorno.json')
        if not returned['success']:
            raise RuntimeError(f"Retorno local falhou: {returned.get('error')}")
        anchor = tuple(origin['center'])
        after_return = reference('retorno', (0., 0.))
        audit.update(status='coletada', optical_return_px=float(np.linalg.norm(
            np.array(after_return['center'])-origin['center'])))
        return samples, anchor, raw_samples, audit
    except Exception as exc:
        audit.update(status='erro', error=str(exc))
        raise
    finally:
        # Tambem deixa os dados de tentativas incompletas para diagnostico.
        stopped = stop_axes_safely()
        if not stopped:
            audit.update(status='erro', error='Parada local nao confirmada.')
        _write_csv(audit_dir / f'{spec.name}_diferencas.csv', [samples])
        (audit_dir / f'{spec.name}_resposta_local.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        if not stopped:
            raise RuntimeError('Parada local nao confirmada.')


def _center_runs(runs: list[list[SweepSample]], *, include_weights: bool = False):
    design_parts, pixel_parts, weight_parts = [], [], []
    for samples in runs:
        if not samples:
            continue
        design = np.array([[s.delta_az_deg, s.delta_alt_deg] for s in samples], dtype=float)
        pixels = np.array([[s.x_px, s.y_px] for s in samples], dtype=float)
        design_parts.append(design - np.median(design, axis=0))
        pixel_parts.append(pixels - np.median(pixels, axis=0))
        weight_parts.append(
            np.asarray([max(float(s.quality_weight), 1e-6) for s in samples])
        )
    if not design_parts:
        raise RuntimeError("Nenhuma varredura valida para o ajuste.")
    result = np.vstack(design_parts), np.vstack(pixel_parts)
    if include_weights:
        return *result, np.concatenate(weight_parts)
    return result


def _robust_fit(
    design: np.ndarray,
    pixels: np.ndarray,
    base_weights: np.ndarray | None = None,
) -> dict:
    if design.ndim != 2 or design.shape[1] != 2 or pixels.shape != design.shape:
        raise ValueError("Dados do ajuste precisam ter formato Nx2.")
    if base_weights is None:
        base_weights = np.ones(design.shape[0])
    base_weights = np.asarray(base_weights, dtype=float)
    if base_weights.shape != (design.shape[0],) or not np.all(np.isfinite(base_weights)):
        raise ValueError("Pesos de qualidade invalidos para o ajuste.")
    positive = base_weights[base_weights > 0]
    if positive.size == 0:
        raise ValueError("O ajuste nao recebeu pesos de qualidade positivos.")
    base_weights = base_weights / float(np.median(positive))
    base_weights = np.clip(base_weights, 0.1, 10.0)
    weights = base_weights.copy()
    root_w = np.sqrt(weights)[:, None]
    beta = np.linalg.lstsq(design * root_w, pixels * root_w, rcond=None)[0]
    for _ in range(ROBUST_ITERS):
        root_w = np.sqrt(np.clip(weights, 1e-6, None))[:, None]
        beta = np.linalg.lstsq(design * root_w, pixels * root_w, rcond=None)[0]
        residual = np.linalg.norm(pixels - design @ beta, axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        cutoff = HUBER_K * scale
        robust_weights = np.where(
            residual <= cutoff,
            1.0,
            cutoff / np.maximum(residual, 1e-9),
        )
        weights = base_weights * robust_weights
    residual = np.linalg.norm(pixels - design @ beta, axis=1)
    A = beta.T
    condition = float(np.linalg.cond(A))
    if not np.all(np.isfinite(A)) or condition > 100.0:
        raise RuntimeError(f"Matriz continua mal condicionada: cond={condition:.2f}.")
    return {
        "A": A, "A_inv": np.linalg.inv(A), "weights": weights,
        "rms_residual_px": float(np.sqrt(np.mean(residual**2))),
        "median_residual_px": float(np.median(residual)),
        "max_residual_px": float(np.max(residual)), "condition_number": condition,
    }


def _direction_slope(samples: list[SweepSample], axis: int) -> np.ndarray:
    d = np.array([s.delta_az_deg if axis == 0 else s.delta_alt_deg for s in samples])
    p = np.array([[s.x_px, s.y_px] for s in samples])
    d, p = d - np.median(d), p - np.median(p, axis=0)
    denom = float(d @ d)
    if denom <= 1e-10:
        raise RuntimeError("Trajetoria sem variacao angular suficiente.")
    base_weights = np.asarray(
        [max(float(sample.quality_weight), 1e-6) for sample in samples]
    )
    base_weights /= float(np.median(base_weights))
    base_weights = np.clip(base_weights, 0.1, 10.0)
    slope = np.sum((base_weights * d)[:, None] * p, axis=0) / float(
        np.sum(base_weights * d * d)
    )
    for _ in range(6):
        residual = np.linalg.norm(p - d[:, None] * slope, axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        cutoff = HUBER_K * scale
        weights = base_weights * np.where(
            residual <= cutoff,
            1.0,
            cutoff / np.maximum(residual, 1e-9),
        )
        slope = np.sum((weights * d)[:, None] * p, axis=0) / float(np.sum(weights * d * d))
    return slope


def _validate_fit(runs: list[list[SweepSample]], fit: dict, half_range_deg=LOCAL_HALF_RANGE_DEG) -> dict:
    checks, failures = {}, []
    for axis, label in ((0, "az"), (1, "alt")):
        axis_runs = [r for r in runs if r and r[0].axis == axis]
        if len(axis_runs) != 2:
            failures.append(f"{label}: faltam duas direcoes")
            continue
        vectors = [_direction_slope(r, axis) for r in axis_runs]
        norms = [float(np.linalg.norm(v)) for v in vectors]
        cosine = float(np.dot(*vectors) / max(norms[0] * norms[1], 1e-12))
        ratio = max(norms) / max(min(norms), 1e-12)
        checks[label] = {"slopes_px_per_deg": [v.tolist() for v in vectors], "cosine": cosine, "magnitude_ratio": ratio}
        if cosine < MIN_DIRECTION_COSINE:
            failures.append(f"{label}: ida/volta discordam (cosseno={cosine:.2f})")
        if ratio > MAX_DIRECTION_SCALE_RATIO:
            failures.append(f"{label}: escalas ida/volta diferem {ratio:.1f}x")
    response = {}
    rms = float(fit["rms_residual_px"])
    for axis, label in ((0, "az"), (1, "alt")):
        response[label] = float(np.linalg.norm(fit["A"][:, axis]) * half_range_deg)
        if response[label] < max(8.0, 1.5 * rms):
            failures.append(f"{label}: resposta {response[label]:.1f}px insuficiente para residuo {rms:.1f}px")
    return {"ok": not failures, "failures": failures, "direction_checks": checks,
            "predicted_response_at_half_range_px": response, "half_range_deg": half_range_deg}


def _validate_holdout(runs: list[list[SweepSample]], fit: dict, *, label: str) -> dict:
    if len(runs) != 4:
        return {"ok": False, "failures": [f"{label}: esperava 4 varreduras, recebeu {len(runs)}"], "run_count": len(runs)}
    design, pixels = _center_runs(runs)
    # Pares +/-delta/2 sao representacao algebrica, nao duas observacoes.
    # Reporta/valida o erro do deslocamento COMPLETO, sem dividi-lo por dois.
    factor = 2.0 if all(r[0].sample_kind == 'paired_local_step_difference' for r in runs) else 1.0
    residual = factor * np.linalg.norm(pixels - design @ fit["A"].T, axis=1)
    rms = float(np.sqrt(np.mean(residual**2)))
    failures, directions = [], {}
    for axis in (0, 1):
        reference = fit["A"][:, axis]
        for samples in [r for r in runs if r[0].axis == axis]:
            slope = _direction_slope(samples, axis)
            nr, ns = float(np.linalg.norm(reference)), float(np.linalg.norm(slope))
            cosine = float(np.dot(reference, slope) / max(nr * ns, 1e-12))
            ratio = max(nr, ns) / max(min(nr, ns), 1e-12)
            directions[samples[0].run] = {"cosine_with_fit": cosine, "magnitude_ratio_with_fit": ratio}
            if cosine < MIN_DIRECTION_COSINE:
                failures.append(f"{samples[0].run}: direcao diverge (cosseno={cosine:.2f})")
            if ratio > MAX_DIRECTION_SCALE_RATIO:
                failures.append(f"{samples[0].run}: escala diverge ({ratio:.1f}x)")
    predicted = factor * float(np.median(np.linalg.norm(design @ fit["A"].T, axis=1)))
    relative = rms / max(predicted, 1e-9)
    # O ruido alto do proprio ajuste nao deve aumentar a tolerancia do holdout.
    if rms > HOLDOUT_NOISE_FLOOR_PX and relative > MAX_HOLDOUT_RELATIVE_RMS:
        failures.append(f"{label}: residuo alto ({rms:.1f}px; relativo={relative:.2f})")
    # Cada sentido pode estar perto da matriz e ainda divergir muito do outro.
    # A validacao independente tambem precisa ser internamente repetivel.
    repeatability = _validate_fit(runs, fit, half_range_deg=LOCAL_STEP_DEG)
    failures.extend(f"{label}: {failure}" for failure in repeatability['failures'])
    return {"ok": not failures, "failures": failures, "run_count": len(runs),
            "sample_count": sum(map(len, runs)), "rms_residual_px": rms,
            "median_residual_px": float(np.median(residual)), "max_residual_px": float(np.max(residual)),
            "relative_rms": relative, "directions": directions, "virtual_pair_residual_factor": factor,
            "independent_direction_checks": repeatability['direction_checks']}


def _write_csv(path: Path, runs: list[list[SweepSample]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(SweepSample.__dataclass_fields__))
        writer.writeheader()
        for samples in runs:
            writer.writerows(asdict(sample) for sample in samples)


def _backup_active_matrices(matrix_dir: Path, backup_dir: Path, prefix: str) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for regime in ("fine", "coarse"):
        for kind in ("A", "A_inv"):
            source = matrix_dir / f"{prefix}_{kind}_{regime}.npy"
            if source.exists():
                destination = backup_dir / source.name
                shutil.copy2(source, destination)
                copied.append(str(destination))
    return copied


def _atomic_save_matrix(path: Path, matrix: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".novo.npy")
    np.save(temporary, matrix)
    temporary.replace(path)


def _promote_matrices(matrix_dir: Path, backup_dir: Path, prefix: str, A, A_inv) -> list[str]:
    copied = _backup_active_matrices(matrix_dir, backup_dir, prefix)
    for regime in ("fine", "coarse"):
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_{regime}.npy", A)
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_inv_{regime}.npy", A_inv)
    return copied


def _matrix_prefix() -> str:
    return foco.IDS_MATRIX_PREFIX if backend_name() == "ids" else "foco_temp"


def _output_dirs():
    results = Path(__file__).resolve().parents[1] / "resultados"
    return (
        Path(os.environ.get("QKD_CALIBRATION_OUTPUT_DIR", results / "calibracao")),
        Path(os.environ.get("QKD_CALIBRATION_METADATA_DIR", results / "json")),
        Path(os.environ.get("QKD_CALIBRATION_MATRIX_DIR", results / "matrizes")),
    )


def _raw_target_from_display(sensor_w, sensor_h, x, y):
    return (sensor_w - 1 - x, sensor_h - 1 - y) if foco.ROTATE_IMAGE_180 else (x, y)


def _local_target_from_raw(w, h, ox, oy, raw_x, raw_y):
    x, y = raw_x - ox, raw_y - oy
    return (float(w - 1 - x), float(h - 1 - y)) if foco.ROTATE_IMAGE_180 else (float(x), float(y))


def main(profile_name: str | None = None) -> None:
    if backend_name() not in {"ids", "zwo_sdk"}:
        raise RuntimeError("A calibracao continua requer backend 'ids' ou 'zwo_sdk'.")
    profile = calibration_profile(profile_name or os.environ.get("QKD_CALIBRATION_PROFILE", "robusto"))
    calibration_dir, metadata_dir, matrix_dir = _output_dirs()
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = calibration_dir / "continua" / f"calibracao_{timestamp}_{profile.name}"
    backup_dir = calibration_dir / "backups" / f"antes_continua_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    matrix_dir.mkdir(parents=True, exist_ok=True)
    initial_position = None
    connected = promoted = False
    all_runs, all_frame_runs, aggregation_stats, failed_optional = [], [], [], []
    summary = {"started_epoch": time.time(), "status": "iniciado", "run_dir": display_path(run_dir),
               "backend": backend_name(), "profile": profile.name,
               "profile_description": profile.description, "trajectory": [asdict(s) for s in profile.specs],
               "spread_method": "global_robust_linear_time_trend_removed",
               "estimation_method": "paired_local_steps_with_stationary_controls",
               "local_step_deg": LOCAL_STEP_DEG,
               "repetitions_per_direction": LOCAL_REPETITIONS,
               "return_used_for_fit": False,
               "reference_window_seconds": REFERENCE_WINDOW_S,
               "reference_target_frames": REFERENCE_TARGET_FRAMES,
               "reference_max_window_seconds": REFERENCE_MAX_WINDOW_S,
               "reference_position_method": "centroid_of_full_mean_image",
               "reference_timeout_seconds": REFERENCE_TIMEOUT_S,
               "sweep_audit_subdir": "varreduras",
               "validation_limits": {
                   "maximum_direction_scale_ratio": MAX_DIRECTION_SCALE_RATIO,
                   "minimum_direction_cosine": MIN_DIRECTION_COSINE,
                   "maximum_holdout_relative_rms": MAX_HOLDOUT_RELATIVE_RMS,
                   "holdout_noise_floor_px": HOLDOUT_NOISE_FLOOR_PX,
               }}
    try:
        ensure_connected(); ensure_unparked(); ensure_not_tracking()
        connect_camera(); connected = True
        set_gain(foco.CAMERA_GAIN); foco.set_focus_mode("dual")
        camera = direct_camera()
        camera.reset_roi()
        full_frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
        selection = foco.escolher_ilha_manualmente(full_frame, max_jump_px=TRACKER_MAX_SPOT_JUMP_PX)
        sensor_h, sensor_w = full_frame.shape[:2]
        roi_size = max(CALIBRATION_ROI_SIZE_PX, roi_size_for_backend(backend_name()))
        raw_x, raw_y = _raw_target_from_display(sensor_w, sensor_h, selection["x_px"], selection["y_px"])
        start_x, start_y, _, _ = roi_incluindo_alvo(sensor_w, sensor_h, roi_size, roi_size, raw_x, raw_y)
        actual_w, actual_h, actual_x, actual_y = camera.set_roi(roi_size, roi_size, start_x, start_y)
        target_local = _local_target_from_raw(actual_w, actual_h, actual_x, actual_y, raw_x, raw_y)
        if not (0 <= target_local[0] < actual_w and 0 <= target_local[1] < actual_h):
            raise RuntimeError("O alinhamento da ROI deixou a luz fora do recorte.")
        initial_position = read_altaz()
        initial_az, initial_alt = initial_position
        summary.update(initial_az_deg=initial_az, initial_alt_deg=initial_alt,
                       roi=[actual_w, actual_h, actual_x, actual_y],
                       target_full_px=[selection["x_px"], selection["y_px"]],
                       target_local_px=list(target_local), sweep_rate_deg_s=SWEEP_RATE_DEG_S,
                       local_half_range_deg=LOCAL_HALF_RANGE_DEG, wide_half_range_deg=WIDE_HALF_RANGE_DEG,
                       exposure_seconds=foco.EXPOSURE_SECONDS, gain=foco.CAMERA_GAIN,
                       exposure_strategy="fixed_during_calibration",
                       baseline_valid_frames=REFERENCE_MIN_FRAMES,
                       baseline_window_seconds=REFERENCE_WINDOW_S)
        print(f"\nCalibracao continua {profile.name} | camera={backend_name()} | ROI={actual_w}x{actual_h}")
        print(profile.description)
        print("Ctrl+C para parar os eixos e retornar a posicao absoluta inicial.")
        center_anchor = target_local
        for spec in profile.specs:
            returned = _return_to_absolute_start(initial_az, initial_alt,
                                                 audit_path=run_dir / "varreduras" / f"antes_{spec.name}_retorno.json")
            if not returned["success"]:
                raise RuntimeError(f"Retorno antes de {spec.name} falhou: {returned.get('error')}")
            try:
                samples, center_anchor, raw_samples, aggregation = _run_local_sequence(
                    spec=spec, initial_az=initial_az, initial_alt=initial_alt,
                    signature=selection["signature"], center_anchor=center_anchor,
                    audit_dir=run_dir / "varreduras",
                )
                all_runs.append(samples)
                all_frame_runs.append(raw_samples)
                aggregation_stats.append({"run": spec.name, **aggregation})
            except Exception as exc:
                if spec.role != "holdout_amplo":
                    raise
                failed_optional.append({"run": spec.name, "error": str(exc)})
                print(f"Aviso: teste amplo indisponivel: {exc}")
        returned = _return_to_absolute_start(initial_az, initial_alt,
                                             audit_path=run_dir / "retorno_final.json")
        if not returned["success"]:
            raise RuntimeError(f"Retorno final nao confirmado: {returned.get('error')}")

        fit_runs = [r for r in all_runs if r[0].role == "fit"]
        local_runs = [r for r in all_runs if r[0].role == "holdout_local"]
        wide_runs = [r for r in all_runs if r[0].role == "holdout_amplo"]
        fit = _robust_fit(*_center_runs(fit_runs, include_weights=True))
        for key in ('rms_residual_px', 'median_residual_px', 'max_residual_px'):
            fit[key] *= 2.0  # Erro do deslocamento completo, nao das duas metades virtuais.
        fit_validation = _validate_fit(fit_runs, fit, half_range_deg=LOCAL_STEP_DEG)
        local_validation = (_validate_holdout(local_runs, fit, label="holdout_local")
                            if profile.requires_holdout else {"ok": True, "not_independent": True})
        wide_validation = {"ok": False, "not_tested": True, "reason": "calibracao_local_para_tracker"}
        activation_ok = bool(fit_validation["ok"] and local_validation["ok"])
        capture_times = np.array(
            [sample.capture_duration_s for run in all_frame_runs for sample in run],
            dtype=float,
        )
        frame_rates = []
        for run in all_frame_runs:
            if len(run) >= 2:
                duration = run[-1].capture_mid_epoch - run[0].capture_mid_epoch
                if duration > 0:
                    frame_rates.append((len(run) - 1) / duration)
        spreads = np.asarray(
            [sample.centroid_spread_px for run in all_runs for sample in run],
            dtype=float,
        )
        frames_per_bin = np.asarray(
            [sample.frames_combined for run in all_runs for sample in run],
            dtype=float,
        )
        _write_csv(run_dir / "amostras_frames.csv", all_frame_runs)
        _write_csv(run_dir / "amostras.csv", all_runs)
        np.save(run_dir / "A_continua.npy", fit["A"])
        np.save(run_dir / "A_inv_continua.npy", fit["A_inv"])
        pulse_diagnostics = []
        for aggregation in aggregation_stats:
            for pulse in aggregation['micropulses']:
                timing = pulse['timing']
                predicted = (fit['A'][:, timing['axis']] * timing['sign'] *
                             timing['rate_deg_s'] * timing['requested_seconds'])
                pulse_diagnostics.append(dict(run=aggregation['run'], **pulse,
                    predicted_from_nominal_command_px=predicted.tolist(),
                    duration_is_software_timing_not_mechanical_measurement=True))
        summary.update(status="validada" if activation_ok else "rejeitada", finished_epoch=time.time(),
                       sample_count=sum(map(len, all_runs)), fit_sample_count=sum(map(len, fit_runs)),
                       raw_frame_sample_count=sum(map(len, all_frame_runs)),
                       residual_basis="full_local_step_displacement",
                       stationary_aggregation={
                           "method": "paired steps; drift measured before and after commands",
                           "sequence_count": len(all_runs),
                           "reference_count": sum(a["reference_count"] for a in aggregation_stats),
                           "valid_reference_frames": sum(a["valid_reference_frames"] for a in aggregation_stats),
                           "median_reference_spread_px": float(np.median(spreads)),
                           "median_frames_per_difference": float(np.median(frames_per_bin)),
                           "shared_references_between_consecutive_steps": True,
                       },
                       angular_aggregation={
                           "used_for_matrix": False,
                           "bin_width_deg": ANGLE_BIN_WIDTH_DEG,
                           "minimum_frames_per_bin": MIN_FRAMES_PER_ANGLE_BIN,
                           "minimum_valid_bins_per_sweep": MIN_VALID_SWEEP_BINS,
                           "sweeps": aggregation_stats,
                       },
                       A=fit["A"].tolist(), A_inv=fit["A_inv"].tolist(),
                       rms_residual_px=fit["rms_residual_px"], median_residual_px=fit["median_residual_px"],
                       max_residual_px=fit["max_residual_px"], condition_number=fit["condition_number"],
                       fit_validation=fit_validation, local_holdout_validation=local_validation,
                       wide_validation=wide_validation, failed_optional_runs=failed_optional,
                       capture_mean_ms=float(1000.0 * np.mean(capture_times)),
                       capture_p95_ms=float(1000.0 * np.percentile(capture_times, 95)),
                       capture_rate_hz=float(1.0 / max(np.mean(capture_times), 1e-9)),
                       sweep_sample_rate_hz=float(np.median(frame_rates)),
                       micropulse_diagnostics=pulse_diagnostics,
                       validated_half_range_deg=LOCAL_STEP_DEG,
                       return_to_start=returned)
        print(f"\nA =\n{fit['A']}")
        print(f"Micropulsos: {sum(p['optically_resolved'] for p in pulse_diagnostics)}/{len(pulse_diagnostics)} "
              "com resposta distinguivel do ruido; diagnostico apenas, tracker inalterado.")
        print(
            f"RMS ajuste={fit['rms_residual_px']:.2f}px | "
            f"cond={fit['condition_number']:.2f} | "
            f"frames={summary['raw_frame_sample_count']} -> "
            f"pares de diferencas locais={summary['sample_count']//2}"
        )
        print(
            f"Frames de vigilancia/tempo total={summary['sweep_sample_rate_hz']:.1f} Hz | "
            f"captura isolada={summary['capture_rate_hz']:.1f} Hz | "
            f"media={summary['capture_mean_ms']:.1f} ms | p95={summary['capture_p95_ms']:.1f} ms"
        )
        if profile.requires_holdout:
            print(f"Holdout local: {'OK' if local_validation['ok'] else 'FALHOU'}")
            print(f"Validacao limitada a passos locais de {LOCAL_STEP_DEG:.3f} deg; amplitudes grandes nao testadas.")
        if not activation_ok:
            print("REJEITADA; matrizes atuais preservadas:")
            for failure in fit_validation["failures"] + local_validation.get("failures", []):
                print(f"  - {failure}")
        else:
            registrar = input(
                "Validacao local aprovada. Registrar este ponto no mapa de Jacobianas? [S/n]: "
            ).strip().lower()
            if registrar not in {"n", "nao", "não"}:
                prefix = _matrix_prefix()
                mapa_path = matrix_dir / f"{prefix}_mapa_jacobianas.json"
                no_nome = f"{timestamp}_{initial_az:.6f}_{initial_alt:.6f}"
                registrar_no(
                    mapa_path,
                    nome=no_nome,
                    azimute_deg=initial_az,
                    altitude_deg=initial_alt,
                    A=fit["A"],
                    A_inv=fit["A_inv"],
                    rms_residual_px=fit["rms_residual_px"],
                    raio_validado_deg=summary["validated_half_range_deg"],
                    metadata={
                        "backend": backend_name(),
                        "profile": profile.name,
                        "run_dir": display_path(run_dir),
                        "rotate_image_180": foco.ROTATE_IMAGE_180,
                        "fit_validation": fit_validation,
                        "local_holdout_validation": local_validation,
                        "wide_validation": wide_validation,
                        "angular_aggregation": summary["angular_aggregation"],
                    },
                )
                summary.update(
                    jacobian_map_path=display_path(mapa_path),
                    jacobian_node_name=no_nome,
                )
                print(f"No local registrado em: {display_path(mapa_path)}")

        if activation_ok and input("Ativar esta matriz no tracker? (s/N): ").strip().lower() in {"s", "sim"}:
            prefix = _matrix_prefix()
            previous_target = metadata_dir / TARGET_FILENAME
            if previous_target.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(previous_target, backup_dir / previous_target.name)
            backups = _promote_matrices(matrix_dir, backup_dir, prefix, fit["A"], fit["A_inv"])
            target_path = salvar_alvo(selection["x_px"], selection["y_px"],
                                      source="continuous_sweep_calibration", frame_shape=full_frame.shape,
                                      samples=sum(map(len, all_runs)), focus_mode="dual",
                                      focus_signature=selection["signature"])
            promoted = True
            summary.update(promoted_to_tracker=True, matrix_prefix=prefix,
                           backup_files=backups, saved_target_path=display_path(target_path))
            print(f"Matriz ativada. Backup anterior: {display_path(backup_dir)}")
        elif activation_ok:
            summary["promoted_to_tracker"] = False
            print("Resultado salvo na auditoria; tracker nao foi alterado.")
    except KeyboardInterrupt:
        summary.update(status="interrompida", finished_epoch=time.time())
        print("\nCalibracao continua interrompida pelo usuario.")
    except Exception as exc:
        summary.update(status="erro", error=str(exc), finished_epoch=time.time())
        print(f"\nErro na calibracao continua: {exc}")
    finally:
        stop_axes_safely()
        if initial_position is not None:
            returned = _return_to_absolute_start(*initial_position, audit_path=run_dir / "retorno_encerramento.json")
            summary["return_to_start_finally"] = returned
            print("Posicao absoluta inicial restaurada." if returned["success"] else f"ALERTA: retorno nao confirmado: {returned}")
        try:
            if connected:
                direct_camera().reset_roi()
                disconnect_camera()
        except Exception as exc:
            summary["camera_close_error"] = str(exc)
        summary["promoted"] = promoted
        summary.setdefault("finished_epoch", time.time())
        (run_dir / "resumo.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        metadata = metadata_dir / f"{_matrix_prefix()}_calibracao_continua_meta.json"
        metadata.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Auditoria salva em: {display_path(run_dir)}")
