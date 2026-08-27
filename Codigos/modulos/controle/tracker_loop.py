"""Malha rapida que converte erro em pixels em comandos seguros do mount."""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from modulos.configuracoes.tracker import (
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_CONFIRM_FRAMES,
    HOLD_EXIT_RADIUS_PX,
    MAX_TRACKING_RATE_DEG_S,
    TEMPORAL_CONTROL_GAIN_SCALE,
)
from modulos.controle.cameras.backend import backend_name
from modulos.controle.mount_control import (
    VEL_MAX_LIMITE,
    VEL_MIN_LIMITE,
    move_axis,
    stop_axes_safely,
)
from modulos.controle.tracker_controle import MeasurementPDTrim, pixel_error_to_mount_error
from modulos.controle.tracker_estado import TrackerState


# Frequencia e limites enviados ao mount.
CONTROL_HZ = float(os.environ.get("QKD_IDS_FPS", "20")) if backend_name() == "ids" else 45.0
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
TRIM_ENTER_RADIUS_PX = 1.3
TRIM_EXIT_RADIUS_PX = 2.2

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


def atualizar_zona_de_reposo(
    hold_active: bool,
    exit_count: int,
    radius_px: float,
) -> tuple[bool, int]:
    """Aplica histerese na zona central para evitar liga/desliga rapido."""
    if hold_active:
        if radius_px >= HOLD_EXIT_RADIUS_PX:
            exit_count += 1
            if exit_count >= HOLD_EXIT_CONFIRM_FRAMES:
                return False, 0
            return True, exit_count
        return True, 0
    if radius_px <= HOLD_ENTER_RADIUS_PX:
        return True, 0
    return False, 0


def limitar_variacao(current: float, target: float, max_delta: float) -> float:
    delta = target - current
    if abs(delta) <= max_delta:
        return float(target)
    return float(current + (np.sign(delta) * max_delta))


def executar_loop_controle(state: TrackerState, A_inv: np.ndarray) -> None:
    """Mantem o controle do mount desacoplado da aquisicao de imagens."""
    ctrl_az = _novo_controlador(KP_AZ, KD_AZ, TRIM_GAIN_AZ)
    ctrl_alt = _novo_controlador(KP_ALT, KD_ALT, TRIM_GAIN_ALT)

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
    trim_mode_active = False
    hold_active = False
    hold_exit_count = 0
    control_loop_hz = 0.0

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
                    ctrl_az.reset()
                    ctrl_alt.reset()
                    prev_radius_px = None
                    prev_dx_filt_px = None
                    prev_dy_filt_px = None
                    runaway_count = 0
                    trim_mode_active = False
                    hold_active = False
                    hold_exit_count = 0
                elif seq != last_seq:
                    last_seq = seq
                    radius_px = float(np.hypot(dx_filt, dy_filt))
                    previous_hold_active = hold_active
                    hold_active, hold_exit_count = atualizar_zona_de_reposo(
                        hold_active,
                        hold_exit_count,
                        radius_px,
                    )
                    if hold_active != previous_hold_active:
                        ctrl_az.reset()
                        ctrl_alt.reset()

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
                            and radius_px > (2.0 * TOLERANCIA_PX)
                        )

                    prev_dx_filt_px = dx_filt
                    prev_dy_filt_px = dy_filt

                    if manual_jump:
                        target_cmd_az = target_cmd_alt = 0.0
                        cmd_az = cmd_alt = 0.0
                        ctrl_az.reset()
                        ctrl_alt.reset()
                        trim_mode_active = False
                        hold_active = False
                        hold_exit_count = 0
                        brake_until = loop_t0 + MANUAL_JUMP_HOLD_S
                        runaway_count = 0
                        if (loop_t0 - last_runaway_log_t) >= RUNAWAY_LOG_COOLDOWN_S:
                            print(
                                "\nMovimento manual brusco detectado. "
                                "Zerando o controle antes de recentralizar."
                            )
                            last_runaway_log_t = loop_t0
                    else:
                        if hold_active:
                            trim_mode_active = False
                            ctrl_az.clear_trim()
                            ctrl_alt.clear_trim()
                        elif radius_px <= TRIM_ENTER_RADIUS_PX:
                            trim_mode_active = True
                        elif radius_px >= TRIM_EXIT_RADIUS_PX:
                            if trim_mode_active:
                                ctrl_az.clear_trim()
                                ctrl_alt.clear_trim()
                            trim_mode_active = False

                        if hold_active:
                            err_az = err_alt = 0.0
                            target_cmd_az = target_cmd_alt = 0.0
                        else:
                            err_az, err_alt = pixel_error_to_mount_error(
                                dx_filt, dy_filt, A_inv
                            )
                            trim_allowed = trim_mode_active and (loop_t0 >= brake_until)
                            target_cmd_az, _ = ctrl_az.update(
                                err_az, measurement_ts, trim_allowed
                            )
                            target_cmd_alt, _ = ctrl_alt.update(
                                err_alt, measurement_ts, trim_allowed
                            )

                        if ENABLE_RUNAWAY_BRAKE:
                            cmd_norm = float(np.hypot(cmd_az, cmd_alt))
                            if (
                                prev_radius_px is not None
                                and cmd_norm >= VEL_MIN_LIMITE
                                and radius_px > (prev_radius_px + RUNAWAY_MARGIN_PX)
                                and radius_px > (2.0 * TOLERANCIA_PX)
                            ):
                                runaway_count += 1
                            else:
                                runaway_count = 0

                            prev_radius_px = radius_px
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
                                ctrl_az.reset()
                                ctrl_alt.reset()
                                trim_mode_active = False
                                hold_active = False
                                hold_exit_count = 0
                                brake_until = loop_t0 + RUNAWAY_HOLD_S
                                runaway_count = 0

                if loop_t0 < brake_until:
                    target_cmd_az = target_cmd_alt = 0.0

                target_cmd_az = float(
                    np.clip(target_cmd_az, -VEL_MAX_TESTE, VEL_MAX_TESTE)
                )
                target_cmd_alt = float(
                    np.clip(target_cmd_alt, -VEL_MAX_TESTE, VEL_MAX_TESTE)
                )

                max_step = CMD_ACCEL_LIMIT * dt_loop
                cmd_az = limitar_variacao(cmd_az, target_cmd_az, max_step)
                cmd_alt = limitar_variacao(cmd_alt, target_cmd_alt, max_step)

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

                with state.lock:
                    state.err_az_deg = err_az
                    state.err_alt_deg = err_alt
                    state.cmd_az_deg_s = cmd_az
                    state.cmd_alt_deg_s = cmd_alt
                    state.brake_active = loop_t0 < brake_until
                    state.trim_mode_active = trim_mode_active
                    state.hold_active = hold_active
                    state.control_loop_hz = control_loop_hz

                elapsed = time.perf_counter() - loop_t0
                if elapsed < dt_target:
                    time.sleep(dt_target - elapsed)
        finally:
            stop_axes_safely()
