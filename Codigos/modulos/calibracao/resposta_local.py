"""Resposta incremental separada da deriva medida com os eixos parados.

Quatro referencias: duas antes e duas depois da atuacao. O ajuste conjunto
pixel = origem + deriva*t + resposta*degrau distingue tempo de movimento.
Nao presume que blocos consecutivos sejam estatisticamente independentes.
"""
import numpy as np

LOCAL_STEP_DEG = 0.002
LOCAL_REPETITIONS = 3
MICROPULSE_SECONDS = 0.12


def measure_step(references):
    """Retorna resposta e indicador conservador de ruido, sem aprovar matriz."""
    if len(references) != 4:
        raise ValueError('A resposta exige duas referencias antes e duas depois.')
    t = np.array([r['t'] for r in references], dtype=float)
    p = np.array([r['center'] for r in references], dtype=float)
    q = np.array([r['angle'] for r in references], dtype=float)
    spread = np.array([max(.5, r['block_spread_px']) for r in references])
    if (not np.all(np.isfinite(np.r_[t, p.ravel(), q.ravel(), spread]))
            or np.any(np.diff(t) <= 0)):
        raise ValueError('Referencias nao finitas ou tempos fora de ordem.')
    # Centralizar/escalar o tempo evita condicionar o ajuste ao uptime do PC.
    u = (t-t.mean()) / np.ptp(t)
    design = np.column_stack((np.ones(4), u, [0., 0., 1., 1.]))
    root_w = 1 / spread
    weighted = design * root_w[:, None]
    if np.linalg.cond(weighted) > 100:
        raise ValueError('Tempos insuficientes para separar deriva e atuacao.')
    operator = np.linalg.pinv(weighted) * root_w[None, :]
    beta = operator @ p
    angular = operator @ q
    before_rate = (p[1]-p[0])/(t[1]-t[0])
    after_rate = (p[3]-p[2])/(t[3]-t[2])
    gap = t[2]-t[1]
    # Nao e erro padrao: preserva ruido dos blocos e discordancia da deriva.
    noise = max(.5, float(np.sqrt(np.sum((operator[2]*spread)**2))),
                float(np.linalg.norm(after_rate-before_rate)*gap/2))
    return dict(displacement_px=beta[2].tolist(), delta_deg=angular[2].tolist(),
                raw_displacement_px=(p[2]-p[1]).tolist(),
                drift_px_s=(beta[1]/np.ptp(t)).tolist(),
                stationary_before_px_s=before_rate.tolist(),
                stationary_after_px_s=after_rate.tolist(),
                drift_disagreement_px_s=float(np.linalg.norm(after_rate-before_rate)),
                reference_noise_px=noise,
                reference_model_rms_px=float(np.sqrt(np.mean(np.sum((p-design@beta)**2, axis=1)))),
                stationary_angular_span_deg=np.maximum(np.abs(q[1]-q[0]), np.abs(q[3]-q[2])).tolist(),
                frame_count=sum(r['frame_count'] for r in references))


def matrix_step_usable(result, axis, sign):
    """Nao confunde pulsos abaixo da resolucao angular com calibracao geometrica."""
    q = np.asarray(result['delta_deg'])
    response = np.linalg.norm(result['displacement_px'])
    if max(result['stationary_angular_span_deg']) > .00056:
        return False, 'angulo_variou_enquanto_parado'
    if q[axis]*sign < LOCAL_STEP_DEG*.5 or abs(q[axis]) > LOCAL_STEP_DEG*2:
        return False, 'deslocamento_angular_insuficiente_ou_excessivo'
    if abs(q[1-axis]) > .0005:
        return False, 'movimento_do_eixo_ortogonal'
    if response < max(4., 2*result['reference_noise_px']):
        return False, 'resposta_indistinguivel_da_variacao_sem_comando'
    return True, 'utilizavel'


def timed_pulse(axis, sign, rate, *, move, stop, clock, sleep):
    """Prazo de software inclui envio; nenhuma captura bloqueia a parada."""
    if axis not in (0, 1) or sign not in (-1, 1) or not np.isfinite(rate) or not 0 < rate <= .002:
        raise ValueError('Micropulso fora dos limites do diagnostico.')
    started = clock()
    audit = dict(axis=axis, sign=sign, rate_deg_s=rate, requested_seconds=MICROPULSE_SECONDS)
    try:
        move(axis, sign*rate, True)
        audit['command_ack_seconds'] = clock()-started
        sleep(max(0., MICROPULSE_SECONDS-(clock()-started)))
    finally:
        audit['stop_requested_seconds'] = clock()-started
        confirmed = stop()
        audit['stop_ack_seconds'] = clock()-started
        if not confirmed:
            raise RuntimeError('Parada nao confirmada no diagnostico de micropulso.')
    return audit
