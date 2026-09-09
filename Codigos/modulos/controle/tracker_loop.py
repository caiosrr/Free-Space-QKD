"""Malha rapida que converte erro em pixels em comandos seguros do mount."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields

import numpy as np

from modulos.configuracoes import camera_ids as ids_config
from modulos.configuracoes.tracker import (
    FAST_CORRECTION_RADIUS_PX,
    FAST_ERROR_CONFIRM_SECONDS,
    FAST_ERROR_MIN_DIRECTION_COHERENCE,
    FAST_ERROR_MIN_LARGE_FRACTION,
    FAST_ERROR_MIN_SAMPLES,
    FAST_ERROR_WINDOW_SECONDS,
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_RADIUS_PX,
    MAX_TRACKING_RATE_DEG_S,
    SLOW_BIAS_WARMUP_SECONDS,
    SLOW_BIAS_WINDOW_SECONDS,
    SHADOW_BIAS_TRIGGER_PX,
    SHADOW_BIAS_WARMUP_SECONDS,
    SHADOW_BIAS_WINDOW_SECONDS,
    SLOW_CORRECTION_PERSISTENCE_SECONDS,
    TEMPORAL_CONTROL_GAIN_SCALE,
    TEMPORAL_WINDOW_SECONDS,
)
from modulos.controle.cameras.backend import backend_name
from modulos.controle.mount_ascom import (
    VEL_MAX_LIMITE,
    VEL_MIN_LIMITE,
    move_axis,
    stop_axes_safely,
)
from modulos.controle.tracker_controle import (
    DirectionalErrorEstimator,
    FinePulseAxis,
    MeasurementPDTrim,
    SlowBiasEstimator,
    SlowCorrectionGate,
    pixel_error_to_mount_error,
)
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_pulsos import BoundedCorrectionCycle


# Frequencia e limites enviados ao mount.
CONTROL_HZ = (
    float(os.environ.get("QKD_IDS_FPS", ids_config.FRAME_RATE_FPS))
    if backend_name() == "ids"
    else 45.0
)
SIGNAL_TIMEOUT_S = 0.45
VEL_MAX_TESTE = min(MAX_TRACKING_RATE_DEG_S, VEL_MAX_LIMITE)
CMD_ACCEL_LIMIT = 2.00
CMD_KEEPALIVE_S = 0.15
MIN_CMD_DELTA_TO_SEND = 2e-4
CMD_ZERO_SNAP = 0.35 * VEL_MIN_LIMITE

# Ganhos da malha PD e do trim lento.
KP_AZ = 1.5000
KP_ALT = 1.4400
KD_AZ = 0.1800
KD_ALT = 0.1800
DERIVATIVE_ALPHA = 0.70
TRIM_GAIN_AZ = 1.2
TRIM_GAIN_ALT = 1.2
TRIM_LIMIT = 0.020
TRIM_LEAK = 0.985
TRIM_ERROR_MAX_DEG = 0.0006
TRIM_DERIVATIVE_MAX_DEG_S = 0.006
TRIM_SAME_SIGN_S = 0.80
TRIM_SIGN_EPS_DEG = 0.00010
TRIM_SIGN_FLIP_DAMP = 0.35

# Abaixo deste raio, comandos menores que a velocidade minima viram pulsos
# curtos. Entre pulsos, o mount para e espera a media temporal se atualizar.
FINE_PULSE_RADIUS_PX = 3.0
FINE_PULSE_CORRECTION_FRACTION = 0.35
FINE_PULSE_MIN_S = 1.0 / CONTROL_HZ
FINE_PULSE_MAX_S = 0.12
# A mediana lenta leva aproximadamente metade de sua janela para refletir um
# movimento. Um novo pulso antes disso poderia corrigir varias vezes o mesmo erro.
FINE_PULSE_SETTLE_S = SLOW_BIAS_WARMUP_SECONDS

# Freios contra salto manual e erro crescente.
TOLERANCIA_PX = HOLD_ENTER_RADIUS_PX
ENABLE_RUNAWAY_BRAKE = True
RUNAWAY_MARGIN_PX = 1.0
RUNAWAY_FRAMES = 4
RUNAWAY_HOLD_S = 0.40
RUNAWAY_LOG_COOLDOWN_S = 2.0
ENABLE_MANUAL_JUMP_BRAKE = True
MANUAL_JUMP_PX = 18.0
MANUAL_JUMP_HOLD_S = 0.25


@dataclass
class EstadoControle:
    """Grandezas derivadas da medicao que decidem o comando do mount.

    Todas voltam ao repouso juntas: sem sinal, freio de movimento manual, freio
    de erro crescente e acomodacao pos-movimento. Antes esse bloco estava
    copiado em tres pontos do laco, e uma variavel nova precisava ser lembrada
    nos tres para nao sobreviver a uma parada.
    """

    hold_active: bool = True
    trim_mode_active: bool = False
    control_dx_px: float = 0.0
    control_dy_px: float = 0.0
    control_radius_px: float = 0.0
    slow_dx_px: float = 0.0
    slow_dy_px: float = 0.0
    slow_radius_px: float = 0.0
    slow_span_s: float = 0.0
    slow_ready: bool = False
    fast_dx_px: float = 0.0
    fast_dy_px: float = 0.0
    fast_span_s: float = 0.0
    fast_large_fraction: float = 0.0
    fast_direction_coherence: float = 0.0
    fast_ready: bool = False
    correction_persistence_s: float = 0.0
    source: str = "repouso"

    def repousar(self, source: str) -> None:
        """Descarta o historico derivado e registra por que houve a parada."""
        limpo = EstadoControle(source=str(source))
        for campo in fields(limpo):
            setattr(self, campo.name, getattr(limpo, campo.name))


def _novo_controlador(kp: float, kd: float, trim_gain: float) -> MeasurementPDTrim:
    return MeasurementPDTrim(
        kp=kp * TEMPORAL_CONTROL_GAIN_SCALE,
        kd=kd * TEMPORAL_CONTROL_GAIN_SCALE,
        trim_gain=trim_gain * TEMPORAL_CONTROL_GAIN_SCALE,
        output_limits=(-VEL_MAX_TESTE, VEL_MAX_TESTE),
        derivative_alpha=DERIVATIVE_ALPHA,
        trim_limit=TRIM_LIMIT,
        trim_leak=TRIM_LEAK,
        trim_error_max=TRIM_ERROR_MAX_DEG,
        trim_derivative_max=TRIM_DERIVATIVE_MAX_DEG_S,
        trim_same_sign_s=TRIM_SAME_SIGN_S,
        trim_sign_eps=TRIM_SIGN_EPS_DEG,
        trim_sign_flip_damp=TRIM_SIGN_FLIP_DAMP,
    )


def aplicar_velocidade_minima(cmd: float) -> float:
    if abs(cmd) < CMD_ZERO_SNAP:
        return 0.0
    if 0.0 < abs(cmd) < VEL_MIN_LIMITE:
        return float(VEL_MIN_LIMITE * np.sign(cmd))
    return float(cmd)


def limitar_variacao(current: float, target: float, max_delta: float) -> float:
    delta = target - current
    if abs(delta) <= max_delta:
        return float(target)
    return float(current + (np.sign(delta) * max_delta))


def executar_loop_controle(state: TrackerState, A_inv: np.ndarray) -> None:
    """Mantem o controle do mount desacoplado da aquisicao de imagens."""
    ctrl_az = _novo_controlador(KP_AZ, KD_AZ, TRIM_GAIN_AZ)
    ctrl_alt = _novo_controlador(KP_ALT, KD_ALT, TRIM_GAIN_ALT)
    fine_az = FinePulseAxis(
        VEL_MIN_LIMITE,
        correction_fraction=FINE_PULSE_CORRECTION_FRACTION,
        min_pulse_s=FINE_PULSE_MIN_S,
        max_pulse_s=FINE_PULSE_MAX_S,
        settle_s=FINE_PULSE_SETTLE_S,
    )
    fine_alt = FinePulseAxis(
        VEL_MIN_LIMITE,
        correction_fraction=FINE_PULSE_CORRECTION_FRACTION,
        min_pulse_s=FINE_PULSE_MIN_S,
        max_pulse_s=FINE_PULSE_MAX_S,
        settle_s=FINE_PULSE_SETTLE_S,
    )
    slow_bias = SlowBiasEstimator(
        window_s=SLOW_BIAS_WINDOW_SECONDS,
        warmup_s=SLOW_BIAS_WARMUP_SECONDS,
    )
    pulse_cycle = BoundedCorrectionCycle(
        VEL_MIN_LIMITE, min_s=1.0 / CONTROL_HZ,
        image_window_s=TEMPORAL_WINDOW_SECONDS,
    )
    directional_error = DirectionalErrorEstimator(
        radius_threshold_px=FAST_CORRECTION_RADIUS_PX,
        window_s=FAST_ERROR_WINDOW_SECONDS,
        confirm_s=FAST_ERROR_CONFIRM_SECONDS,
        min_large_fraction=FAST_ERROR_MIN_LARGE_FRACTION,
        min_direction_coherence=FAST_ERROR_MIN_DIRECTION_COHERENCE,
        min_samples=FAST_ERROR_MIN_SAMPLES,
    )
    correction_gate = SlowCorrectionGate(
        enter_radius_px=HOLD_ENTER_RADIUS_PX,
        exit_radius_px=HOLD_EXIT_RADIUS_PX,
        persistence_s=SLOW_CORRECTION_PERSISTENCE_SECONDS,
        fast_radius_px=FAST_CORRECTION_RADIUS_PX,
    )

    # Observador do modo sombra: janela longa, so registro. Nunca entra em
    # nenhuma decisao de comando; existe para medir o que uma zona de repouso
    # mais apertada teria feito.
    shadow_bias = SlowBiasEstimator(
        window_s=SHADOW_BIAS_WINDOW_SECONDS,
        warmup_s=SHADOW_BIAS_WARMUP_SECONDS,
    )
    shadow_corrections = 0
    shadow_armed = True
    shadow_estimate = None

    dt_target = 1.0 / CONTROL_HZ
    last_loop_t = time.perf_counter()
    last_seq = -1

    target_cmd_az = target_cmd_alt = 0.0
    cmd_az = cmd_alt = 0.0
    last_sent_az = last_sent_alt = None
    last_sent_az_t = last_sent_alt_t = 0.0

    err_az = err_alt = 0.0
    prev_radius_px = None
    prev_dx_filt_px = None
    prev_dy_filt_px = None
    runaway_count = 0
    brake_until = 0.0
    last_runaway_log_t = 0.0
    control_loop_hz = 0.0
    estado = EstadoControle()

    def repousar(source: str) -> None:
        """Zera estado derivado, controladores e estimadores de uma vez."""
        estado.repousar(source)
        ctrl_az.reset()
        ctrl_alt.reset()
        fine_az.reset()
        fine_alt.reset()
        slow_bias.reset()
        directional_error.reset()
        correction_gate.reset()

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            while True:
                loop_t0 = time.perf_counter()
                dt_loop = max(loop_t0 - last_loop_t, 1e-4)
                last_loop_t = loop_t0
                if dt_loop >= 1e-3:
                    instant_control_hz = 1.0 / dt_loop
                    control_loop_hz = (
                        instant_control_hz
                        if control_loop_hz <= 0.0
                        else (0.15 * instant_control_hz) + (0.85 * control_loop_hz)
                    )

                with state.lock:
                    stop = state.stop
                    has_signal = state.has_signal
                    seq = state.measurement_seq
                    measurement_ts = state.measurement_ts
                    dx_filt = state.dx_filt_px
                    dy_filt = state.dy_filt_px

                if stop:
                    break

                measurement_age = (loop_t0 - measurement_ts) if measurement_ts else 1e9
                signal_ok = has_signal and (measurement_age <= SIGNAL_TIMEOUT_S)

                if not signal_ok:
                    err_az = err_alt = 0.0
                    target_cmd_az = target_cmd_alt = 0.0
                    prev_radius_px = None
                    prev_dx_filt_px = None
                    prev_dy_filt_px = None
                    runaway_count = 0
                    repousar("sem_sinal")
                    shadow_bias.reset()
                    shadow_estimate = None
                elif pulse_cycle.ready(measurement_ts) and seq != last_seq:
                    last_seq = seq
                    fast_radius_px = float(np.hypot(dx_filt, dy_filt))
                    fast_estimate = directional_error.observe(
                        measurement_ts,
                        dx_filt,
                        dy_filt,
                    )
                    estado.fast_dx_px = fast_estimate.dx_px
                    estado.fast_dy_px = fast_estimate.dy_px
                    estado.fast_span_s = fast_estimate.span_s
                    estado.fast_large_fraction = fast_estimate.large_fraction
                    estado.fast_direction_coherence = (
                        fast_estimate.direction_coherence
                    )
                    estado.fast_ready = fast_estimate.ready
                    shadow_estimate = shadow_bias.observe(measurement_ts, dx_filt, dy_filt)
                    # Conta uma correcao hipotetica por travessia do limiar,
                    # com rearme abaixo da metade, para nao contar oscilacao.
                    if shadow_estimate.ready:
                        if shadow_armed and shadow_estimate.radius_px >= SHADOW_BIAS_TRIGGER_PX:
                            shadow_corrections += 1
                            shadow_armed = False
                        elif shadow_estimate.radius_px <= SHADOW_BIAS_TRIGGER_PX * 0.5:
                            shadow_armed = True
                    bias = slow_bias.observe(measurement_ts, dx_filt, dy_filt)
                    estado.slow_dx_px = bias.dx_px
                    estado.slow_dy_px = bias.dy_px
                    estado.slow_radius_px = bias.radius_px
                    estado.slow_span_s = bias.span_s
                    estado.slow_ready = bias.ready

                    decision = correction_gate.update(
                        measurement_ts,
                        fast_radius_px=fast_radius_px,
                        fast_confirmed=estado.fast_ready,
                        fast_persistence_s=estado.fast_span_s,
                        slow_radius_px=estado.slow_radius_px,
                        slow_ready=estado.slow_ready,
                    )
                    previous_hold_active = estado.hold_active
                    estado.hold_active = decision.hold_active
                    estado.correction_persistence_s = decision.persistence_s
                    estado.source = decision.source
                    if decision.source == "erro_grande_persistente":
                        estado.control_dx_px, estado.control_dy_px = estado.fast_dx_px, estado.fast_dy_px
                    elif estado.slow_ready:
                        estado.control_dx_px, estado.control_dy_px = estado.slow_dx_px, estado.slow_dy_px
                    else:
                        estado.control_dx_px, estado.control_dy_px = dx_filt, dy_filt
                    estado.control_radius_px = float(
                        np.hypot(estado.control_dx_px, estado.control_dy_px)
                    )
                    if estado.hold_active != previous_hold_active:
                        ctrl_az.reset()
                        ctrl_alt.reset()
                        fine_az.reset()
                        fine_alt.reset()

                    manual_jump = False
                    if (
                        ENABLE_MANUAL_JUMP_BRAKE
                        and prev_dx_filt_px is not None
                        and prev_dy_filt_px is not None
                    ):
                        jump_px = float(
                            np.hypot(
                                dx_filt - prev_dx_filt_px,
                                dy_filt - prev_dy_filt_px,
                            )
                        )
                        manual_jump = (
                            jump_px >= MANUAL_JUMP_PX
                            and fast_radius_px > (2.0 * TOLERANCIA_PX)
                        )

                    prev_dx_filt_px = dx_filt
                    prev_dy_filt_px = dy_filt

                    if manual_jump:
                        target_cmd_az = target_cmd_alt = 0.0
                        cmd_az = cmd_alt = 0.0
                        repousar("freio_movimento_manual")
                        brake_until = loop_t0 + MANUAL_JUMP_HOLD_S
                        runaway_count = 0
                        if (loop_t0 - last_runaway_log_t) >= RUNAWAY_LOG_COOLDOWN_S:
                            print(
                                "\nMovimento manual brusco detectado. "
                                "Zerando o controle antes de recentralizar."
                            )
                            last_runaway_log_t = loop_t0
                    else:
                        fine_mode = (
                            not estado.hold_active
                            and (
                                estado.source == "vies_lento"
                                or estado.control_radius_px <= FINE_PULSE_RADIUS_PX
                            )
                        )
                        if estado.hold_active:
                            estado.trim_mode_active = False
                            ctrl_az.clear_trim()
                            ctrl_alt.clear_trim()
                            fine_az.reset()
                            fine_alt.reset()
                        else:
                            estado.trim_mode_active = fine_mode

                        if estado.hold_active:
                            err_az = err_alt = 0.0
                            target_cmd_az = target_cmd_alt = 0.0
                        else:
                            err_az, err_alt = pixel_error_to_mount_error(
                                estado.control_dx_px, estado.control_dy_px, A_inv
                            )
                            if fine_mode:
                                ctrl_az.reset()
                                ctrl_alt.reset()
                                pulse_enabled = loop_t0 >= brake_until
                                target_cmd_az = fine_az.command(
                                    loop_t0, err_az, pulse_enabled
                                )
                                target_cmd_alt = fine_alt.command(
                                    loop_t0, err_alt, pulse_enabled
                                )
                            else:
                                fine_az.reset()
                                fine_alt.reset()
                                target_cmd_az, _ = ctrl_az.update(
                                    err_az, measurement_ts, False
                                )
                                target_cmd_alt, _ = ctrl_alt.update(
                                    err_alt, measurement_ts, False
                                )

                        if ENABLE_RUNAWAY_BRAKE:
                            cmd_norm = float(np.hypot(cmd_az, cmd_alt))
                            if (
                                prev_radius_px is not None
                                and cmd_norm >= VEL_MIN_LIMITE
                                and estado.control_radius_px
                                > (prev_radius_px + RUNAWAY_MARGIN_PX)
                                and estado.control_radius_px > (2.0 * TOLERANCIA_PX)
                            ):
                                runaway_count += 1
                            else:
                                runaway_count = 0

                            prev_radius_px = estado.control_radius_px
                            if runaway_count >= RUNAWAY_FRAMES:
                                if (
                                    loop_t0 - last_runaway_log_t
                                ) >= RUNAWAY_LOG_COOLDOWN_S:
                                    print(
                                        "\nErro aumentou em varios frames. "
                                        "Freando o mount; verifique movimento manual "
                                        "ou sinal/eixo invertido."
                                    )
                                    last_runaway_log_t = loop_t0
                                target_cmd_az = target_cmd_alt = 0.0
                                cmd_az = cmd_alt = 0.0
                                repousar("freio_erro_crescente")
                                brake_until = loop_t0 + RUNAWAY_HOLD_S
                                runaway_count = 0

                if loop_t0 < brake_until:
                    target_cmd_az = target_cmd_alt = 0.0

                # Supervisor final: limita inclusive o modo PD de erro grande.
                # A contagem do pulso independe da chegada de novos frames.
                current_angular_error = pixel_error_to_mount_error(dx_filt, dy_filt, A_inv)
                target_cmd_az, target_cmd_alt = pulse_cycle.command(
                    loop_t0, measurement_ts, (target_cmd_az, target_cmd_alt),
                    (err_az, err_alt), current_angular_error,
                    fine=estado.trim_mode_active, enabled=signal_ok and loop_t0 >= brake_until,
                )
                if pulse_cycle.phase in {"parando", "acomodacao"}:
                    estado.source = "acomodacao_pos_movimento"

                target_cmd_az = float(
                    np.clip(target_cmd_az, -VEL_MAX_TESTE, VEL_MAX_TESTE)
                )
                target_cmd_alt = float(
                    np.clip(target_cmd_alt, -VEL_MAX_TESTE, VEL_MAX_TESTE)
                )

                max_step = CMD_ACCEL_LIMIT * dt_loop
                cmd_az = 0.0 if target_cmd_az == 0.0 else limitar_variacao(cmd_az, target_cmd_az, max_step)
                cmd_alt = 0.0 if target_cmd_alt == 0.0 else limitar_variacao(cmd_alt, target_cmd_alt, max_step)

                if abs(target_cmd_az) < 1e-12 and abs(cmd_az) < VEL_MIN_LIMITE:
                    cmd_az = 0.0
                if abs(target_cmd_alt) < 1e-12 and abs(cmd_alt) < VEL_MIN_LIMITE:
                    cmd_alt = 0.0

                cmd_az = aplicar_velocidade_minima(
                    float(np.clip(cmd_az, -VEL_MAX_TESTE, VEL_MAX_TESTE))
                )
                cmd_alt = aplicar_velocidade_minima(
                    float(np.clip(cmd_alt, -VEL_MAX_TESTE, VEL_MAX_TESTE))
                )

                send_az = (
                    last_sent_az is None
                    or abs(cmd_az - last_sent_az) >= MIN_CMD_DELTA_TO_SEND
                    or (loop_t0 - last_sent_az_t) >= CMD_KEEPALIVE_S
                )
                send_alt = (
                    last_sent_alt is None
                    or abs(cmd_alt - last_sent_alt) >= MIN_CMD_DELTA_TO_SEND
                    or (loop_t0 - last_sent_alt_t) >= CMD_KEEPALIVE_S
                )

                future_az = executor.submit(move_axis, 0, cmd_az, True) if send_az else None
                future_alt = (
                    executor.submit(move_axis, 1, cmd_alt, True) if send_alt else None
                )

                if future_az is not None:
                    future_az.result()
                    last_sent_az = cmd_az
                    last_sent_az_t = loop_t0
                if future_alt is not None:
                    future_alt.result()
                    last_sent_alt = cmd_alt
                    last_sent_alt_t = loop_t0

                if cmd_az == 0.0 and cmd_alt == 0.0 and pulse_cycle.confirm_stopped(time.perf_counter()):
                    # Nao reutilizar medianas/derivadas anteriores ao movimento.
                    repousar("acomodacao_pos_movimento")
                    target_cmd_az = target_cmd_alt = 0.0
                    err_az = err_alt = 0.0
                    prev_radius_px = prev_dx_filt_px = prev_dy_filt_px = None
                    runaway_count = 0
                    last_seq = seq

                with state.lock:
                    state.err_az_deg = err_az
                    state.err_alt_deg = err_alt
                    state.cmd_az_deg_s = cmd_az
                    state.cmd_alt_deg_s = cmd_alt
                    state.brake_active = loop_t0 < brake_until
                    state.trim_mode_active = estado.trim_mode_active
                    state.hold_active = estado.hold_active
                    state.control_dx_px = estado.control_dx_px
                    state.control_dy_px = estado.control_dy_px
                    state.control_radius_px = estado.control_radius_px
                    state.slow_bias_window_s = estado.slow_span_s
                    state.slow_bias_ready = estado.slow_ready
                    state.fast_error_window_s = estado.fast_span_s
                    state.fast_error_large_fraction = estado.fast_large_fraction
                    state.fast_error_direction_coherence = (
                        estado.fast_direction_coherence
                    )
                    state.fast_error_ready = estado.fast_ready
                    state.correction_persistence_s = estado.correction_persistence_s
                    state.control_error_source = estado.source
                    state.shadow_bias_dx_px = shadow_estimate.dx_px if shadow_estimate else 0.0
                    state.shadow_bias_dy_px = shadow_estimate.dy_px if shadow_estimate else 0.0
                    state.shadow_bias_radius_px = (
                        shadow_estimate.radius_px if shadow_estimate else 0.0
                    )
                    state.shadow_bias_ready = bool(shadow_estimate and shadow_estimate.ready)
                    state.shadow_bias_window_s = shadow_estimate.span_s if shadow_estimate else 0.0
                    state.shadow_corrections = shadow_corrections
                    state.control_loop_hz = control_loop_hz
                    state.correction_phase = pulse_cycle.phase
                    state.correction_cycles = pulse_cycle.completed
                    state.post_motion_wait_s = max(0.0, pulse_cycle.accept_after - loop_t0)

                elapsed = time.perf_counter() - loop_t0
                if elapsed < dt_target:
                    time.sleep(dt_target - elapsed)
        finally:
            stop_axes_safely()
