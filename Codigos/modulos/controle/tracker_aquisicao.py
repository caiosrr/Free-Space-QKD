"""Aquisicao, validacao da ilha e media temporal do tracker."""

from dataclasses import dataclass
import time

import numpy as np

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import (
    BORDER_CONFIRM_FRAMES,
    SIGNAL_LOSS_LIMIT_SECONDS,
    TEMPORAL_RECOVERY_VALID_FRAMES,
    TEMPORAL_RESET_AFTER_LOSS_SECONDS,
)
from modulos.controle.tracker_camera import EXPOSURE_SECONDS, capture_frame
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_interface import TrackerDisplay, tracking_status
from modulos.controle.tracker_medicao import TemporalFrameEstimator, measurement_quality
from modulos.controle.tracker_seguranca import solicitar_parada
from modulos.controle.tracker_telemetria import TrackerCsvLogger
from modulos.visao import detector_ilhas as foco


DISPLAY_HZ = 6.0


@dataclass(frozen=True)
class ResultadoAquisicao:
    """Dados devolvidos ao orquestrador para encerrar a sessao."""

    motivo: str
    ultimo_frame: np.ndarray | None


def medir_laser(frame: np.ndarray) -> tuple[float, float] | None:
    """Retorna o centro da ilha travada ou ``None`` quando ela e rejeitada."""
    center = foco.centro_massa(frame)
    if center is None:
        return None
    return float(center[0]), float(center[1])


def executar_aquisicao(
    state: TrackerState,
    logger: TrackerCsvLogger,
    display: TrackerDisplay,
    *,
    target_x: float,
    target_y: float,
    session_started: float,
    session_hours: float,
) -> ResultadoAquisicao:
    """Le frames ate o usuario ou uma trava de seguranca encerrar a sessao."""
    estimator = TemporalFrameEstimator()
    last_measurement_t = 0.0
    measurement_hz = 0.0
    last_display_t = 0.0
    display_interval_s = 1.0 / DISPLAY_HZ
    border_frames = 0
    signal_lost_since = None
    estimator_cleared_for_loss = False
    recovery_valid_frames = 0
    ever_locked = False
    last_frame = None

    while True:
        frame = capture_frame(EXPOSURE_SECONDS)
        last_frame = frame
        now = time.perf_counter()

        if last_measurement_t:
            instant_hz = 1.0 / max(now - last_measurement_t, 1e-4)
            measurement_hz = (
                instant_hz
                if measurement_hz <= 0.0
                else (0.15 * instant_hz) + (0.85 * measurement_hz)
            )
        last_measurement_t = now

        instant_center = medir_laser(frame)
        focus_debug = foco.get_focus_debug()
        selected = focus_debug.get("selected") or {}
        touches_border = bool(selected.get("toca_borda", False))
        target_raw_peak = selected.get("raw_peak")
        instant_valid = instant_center is not None and not touches_border
        border_frames = border_frames + 1 if touches_border else 0

        temporal_outlier = False
        temporal_estimate = None
        if instant_valid:
            accepted = estimator.add(
                now,
                frame,
                instant_center[0],
                instant_center[1],
                quality=measurement_quality(selected),
            )
            temporal_outlier = not accepted
            if accepted:
                recovery_valid_frames = min(
                    recovery_valid_frames + 1,
                    TEMPORAL_RECOVERY_VALID_FRAMES,
                )
                temporal_estimate = estimator.estimate(now)
        else:
            recovery_valid_frames = 0

        if not instant_valid or temporal_outlier:
            if signal_lost_since is None:
                signal_lost_since = now
            recovery_valid_frames = 0
            if (
                now - signal_lost_since >= TEMPORAL_RESET_AFTER_LOSS_SECONDS
                and not estimator_cleared_for_loss
            ):
                estimator.clear()
                estimator_cleared_for_loss = True

        measurement_valid = bool(
            temporal_estimate is not None
            and recovery_valid_frames >= TEMPORAL_RECOVERY_VALID_FRAMES
        )
        if measurement_valid:
            x_cm = float(temporal_estimate["x_px"])
            y_cm = float(temporal_estimate["y_px"])
            dx = x_cm - target_x
            dy = y_cm - target_y
            signal_lost_since = None
            estimator_cleared_for_loss = False
        else:
            x_cm, y_cm = target_x, target_y
            dx = dy = 0.0

        signal_lost_s = (
            0.0 if signal_lost_since is None else now - signal_lost_since
        )
        with state.lock:
            signal_was_locked = state.has_signal
            state.has_signal = measurement_valid
            state.spot_touches_border = touches_border
            state.measurement_seq += 1
            state.measurement_ts = now
            state.measurement_hz = measurement_hz
            if measurement_valid:
                state.dx_filt_px = dx
                state.dy_filt_px = dy
            state.temporal_frame_count = estimator.frame_count
            state.temporal_window_s = estimator.window_span_s
            state.recovery_valid_frames = recovery_valid_frames
            state.signal_lost_s = signal_lost_s
            state.target_raw_peak = (
                None if target_raw_peak is None else float(target_raw_peak)
            )
            state.temporal_outlier = temporal_outlier

        if border_frames >= BORDER_CONFIRM_FRAMES:
            solicitar_parada(state, "ilha_tocou_a_borda_da_roi")
        if signal_lost_s >= SIGNAL_LOSS_LIMIT_SECONDS:
            solicitar_parada(state, "sinal_perdido_por_tempo_excessivo")

        values = state.snapshot()
        status, status_color = tracking_status(
            values,
            spot_touches_border=touches_border,
            temporal_outlier=temporal_outlier,
            instant_signal=instant_center is not None,
            measurement_valid=measurement_valid,
        )

        event = values["safety_stop_reason"] or ""
        if not event and signal_was_locked and not measurement_valid:
            event = "inicio_perda_sinal"
            logger.save_event_frame(frame, event)
        elif not event and measurement_valid and not signal_was_locked:
            event = "sinal_recuperado" if ever_locked else "sinal_inicial_confirmado"
            if ever_locked:
                logger.save_event_frame(frame, event)
            ever_locked = True

        logger.write(
            now,
            state_values=values,
            status=status,
            x_cm=None if not measurement_valid else x_cm,
            y_cm=None if not measurement_valid else y_cm,
            target_x=target_x,
            target_y=target_y,
            dx=dx,
            dy=dy,
            event=event,
        )

        if values["safety_stop_reason"]:
            reason = values["safety_stop_reason"]
            event_path = logger.save_event_frame(last_frame, reason)
            if event_path is not None:
                print(f"Frame do evento: {display_path(event_path)}")
            print(f"\nPARADA DE SEGURANCA: {reason}")
            return ResultadoAquisicao(reason, last_frame)

        if last_display_t == 0.0 or now - last_display_t >= display_interval_s:
            display.show(
                frame,
                x_cm=x_cm,
                y_cm=y_cm,
                dx=dx,
                dy=dy,
                state=values,
                status=status,
                status_color=status_color,
                elapsed_hours=(now - session_started) / 3600.0,
                session_hours=session_hours,
            )
            last_display_t = now

        if display.should_close():
            return ResultadoAquisicao("encerrado_pelo_usuario", last_frame)
