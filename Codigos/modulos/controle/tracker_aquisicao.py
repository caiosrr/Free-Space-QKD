"""Aquisicao, validacao da ilha e media temporal do tracker."""

from dataclasses import dataclass
import time

import numpy as np

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import (
    AUTO_EXPOSURE_ENABLED,
    EVENT_MIN_ABSENCE_SECONDS,
    BORDER_CONFIRM_SECONDS,
    BORDER_MIN_PEAK_RATIO,
    BORDER_MIN_SIGNATURE_SIMILARITY,
    SIGNAL_LOSS_LIMIT_SECONDS,
    TEMPORAL_OPTICAL_HOLD_SECONDS,
    TEMPORAL_RECOVERY_VALID_FRAMES,
    TEMPORAL_RESET_AFTER_LOSS_SECONDS,
)
from modulos.controle.cameras.backend import backend_name
from modulos.controle.tracker_camera import (
    EXPOSURE_SECONDS,
    capture_frame,
    latest_raw_frame,
)
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_exposicao import AutoExposureController
from modulos.controle.tracker_interface import TrackerDisplay, tracking_status
from modulos.controle.tracker_medicao import TemporalFrameEstimator, measurement_quality
from modulos.controle.tracker_qualidade import OpticalQualityGate
from modulos.controle.tracker_seguranca import solicitar_parada
from modulos.controle.tracker_telemetria import TrackerCsvLogger
from modulos.visao import detector_ilhas as foco


DISPLAY_HZ = 6.0


@dataclass(frozen=True)
class ResultadoAquisicao:
    """Dados devolvidos ao orquestrador para encerrar a sessao."""

    motivo: str
    ultimo_frame: np.ndarray | None


@dataclass(frozen=True)
class EstadoDisponibilidade:
    """Tempos distintos para alvo ausente e alvo presente ainda nao confiavel."""

    target_present: bool
    absent_seconds: float
    unstable_seconds: float


class TemporizadoresDisponibilidade:
    """Impede que turbulencia optica seja confundida com perda do beacon."""

    def __init__(self):
        self._absent_since: float | None = None
        self._unstable_since: float | None = None

    def observe(
        self,
        now: float,
        *,
        target_present: bool,
        measurement_valid: bool,
    ) -> EstadoDisponibilidade:
        now = float(now)
        if not target_present:
            if self._absent_since is None:
                self._absent_since = now
            self._unstable_since = None
        else:
            self._absent_since = None
            if measurement_valid:
                self._unstable_since = None
            elif self._unstable_since is None:
                self._unstable_since = now
        return EstadoDisponibilidade(
            target_present=bool(target_present),
            absent_seconds=(
                0.0 if self._absent_since is None else now - self._absent_since
            ),
            unstable_seconds=(
                0.0
                if self._unstable_since is None
                else now - self._unstable_since
            ),
        )


class ConfirmacaoBorda:
    """Confirma por tempo uma ilha plausivel continuamente cortada pela ROI."""

    def __init__(self, confirm_seconds: float = BORDER_CONFIRM_SECONDS):
        self.confirm_seconds = float(confirm_seconds)
        self.started_at: float | None = None

    def observe(self, now: float, plausible: bool) -> float:
        if not plausible:
            self.started_at = None
            return 0.0
        if self.started_at is None:
            self.started_at = float(now)
        return max(0.0, float(now) - self.started_at)


def candidato_borda_compativel(selected: dict, focus_signature: dict) -> bool:
    """Rejeita ruido de borda fraco ou incompatível com a ilha travada."""
    if not selected.get("toca_borda", False):
        return False
    primary = focus_signature.get("primary") or {}
    try:
        raw_peak = float(selected["raw_peak"])
        reference_peak = float(primary["raw_peak"])
        similarity = float(selected["similarity_primary"])
    except (KeyError, TypeError, ValueError):
        return False
    if not all(np.isfinite(value) for value in (raw_peak, reference_peak, similarity)):
        return False
    return bool(
        reference_peak > 0.0
        and raw_peak >= reference_peak * BORDER_MIN_PEAK_RATIO
        and similarity >= BORDER_MIN_SIGNATURE_SIMILARITY
    )


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
    focus_signature = foco.get_focus_signature() or {}
    quality_gate = OpticalQualityGate(focus_signature.get("primary"))
    exposure_controller = AutoExposureController(
        EXPOSURE_SECONDS * 1e6,
        enabled=AUTO_EXPOSURE_ENABLED and backend_name() == "ids",
        started_at=session_started,
    )
    current_exposure_us = exposure_controller.current_exposure_us
    exposure_adjustments = 0
    last_trusted_center = (float(target_x), float(target_y))
    last_measurement_t = 0.0
    measurement_hz = 0.0
    last_display_t = 0.0
    display_interval_s = 1.0 / DISPLAY_HZ
    border_guard = ConfirmacaoBorda()
    availability_timers = TemporizadoresDisponibilidade()
    measurement_invalid_since = None
    estimator_cleared_for_loss = False
    recovery_valid_frames = 0
    last_estimator_input_t = None
    ever_locked = False
    ausencia_registrada = False
    last_frame = None

    while True:
        frame = capture_frame(current_exposure_us * 1e-6)
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
        target_raw_total = selected.get("raw_total")
        target_diameter_px = max(
            float(selected.get("bbox_w") or 0.0),
            float(selected.get("bbox_h") or 0.0),
        ) or None
        target_present_for_exposure = instant_center is not None
        candidate_valid = target_present_for_exposure and not touches_border
        plausible_border = candidato_borda_compativel(selected, focus_signature)
        border_persistence_s = border_guard.observe(now, plausible_border)
        quality = quality_gate.observe(now, selected if candidate_valid else None)
        frame_accepted = candidate_valid and quality.accepted
        raw_exposure_frame = latest_raw_frame()
        exposure_decision = exposure_controller.observe(
            now,
            raw_exposure_frame if raw_exposure_frame is not None else frame,
            target_center=instant_center,
            target_diameter_px=target_diameter_px,
            trusted_target=frame_accepted,
            target_present=target_present_for_exposure,
        )
        exposure_event = ""
        if exposure_decision.changed:
            current_exposure_us = exposure_decision.exposure_us
            exposure_adjustments += 1
            exposure_event = f"autoexposicao_{exposure_decision.reason}"
            # Sem print: a exposicao muda dezenas de vezes por sessao e o
            # terminal e onde o operador acompanha o que exige acao. Exposicao,
            # CNR e motivo aparecem ao vivo no painel e ficam no CSV.
        if candidate_valid:
            if quality.accepted:
                last_trusted_center = (float(instant_center[0]), float(instant_center[1]))
            else:
                # O detector atualiza sua ancora a cada candidato proximo. Um
                # frame rejeitado nao pode arrastar essa ancora para a anomalia.
                foco.set_focus_expected_position(*last_trusted_center)
        temporal_outlier = False
        if frame_accepted:
            accepted = estimator.add(
                now,
                frame,
                instant_center[0],
                instant_center[1],
                quality=measurement_quality(selected),
            )
            temporal_outlier = not accepted
            if accepted:
                last_estimator_input_t = now
                recovery_valid_frames = min(
                    recovery_valid_frames + 1,
                    TEMPORAL_RECOVERY_VALID_FRAMES,
                )
        elif not quality.control_allowed:
            recovery_valid_frames = 0

        temporal_estimate = estimator.estimate(now)
        estimate_is_fresh = bool(
            last_estimator_input_t is not None
            and now - last_estimator_input_t <= TEMPORAL_OPTICAL_HOLD_SECONDS
        )
        measurement_valid = bool(
            temporal_estimate is not None
            and candidate_valid
            and quality.control_allowed
            and not temporal_outlier
            and estimate_is_fresh
            and recovery_valid_frames >= TEMPORAL_RECOVERY_VALID_FRAMES
        )

        if not measurement_valid:
            if measurement_invalid_since is None:
                measurement_invalid_since = now
            if (
                now - measurement_invalid_since
                >= TEMPORAL_RESET_AFTER_LOSS_SECONDS
                and not estimator_cleared_for_loss
            ):
                estimator.clear()
                estimator_cleared_for_loss = True
        if measurement_valid:
            x_cm = float(temporal_estimate["x_px"])
            y_cm = float(temporal_estimate["y_px"])
            dx = x_cm - target_x
            dy = y_cm - target_y
            measurement_invalid_since = None
            estimator_cleared_for_loss = False
        else:
            x_cm, y_cm = target_x, target_y
            dx = dy = 0.0

        availability = availability_timers.observe(
            now,
            target_present=candidate_valid,
            measurement_valid=measurement_valid,
        )
        signal_lost_s = availability.absent_seconds
        optical_unstable_s = availability.unstable_seconds
        with state.lock:
            signal_was_locked = state.has_signal
            target_was_present = state.target_present
            state.has_signal = measurement_valid
            state.target_present = availability.target_present
            state.spot_touches_border = touches_border
            state.border_candidate_plausible = plausible_border
            state.border_persistence_s = border_persistence_s
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
            state.optical_unstable_s = optical_unstable_s
            state.exposure_us = current_exposure_us
            state.auto_exposure_enabled = exposure_controller.enabled
            state.auto_exposure_reason = exposure_decision.reason
            state.auto_exposure_peak_median = exposure_decision.peak_median
            state.auto_exposure_background = (
                exposure_decision.background_percentile
            )
            state.auto_exposure_saturation_fraction = (
                exposure_decision.saturation_fraction
            )
            state.auto_exposure_local_background = (
                exposure_decision.local_background_median
            )
            state.auto_exposure_local_noise = exposure_decision.local_noise_median
            state.auto_exposure_cnr = exposure_decision.cnr_median
            state.auto_exposure_trusted_fraction = (
                exposure_decision.trusted_fraction
            )
            state.auto_exposure_target_saturation_fraction = (
                exposure_decision.target_saturation_fraction
            )
            state.auto_exposure_rollback = exposure_decision.rollback
            state.auto_exposure_adjustments = exposure_adjustments
            state.target_raw_peak = (
                None if target_raw_peak is None else float(target_raw_peak)
            )
            state.target_raw_total = (
                None if target_raw_total is None else float(target_raw_total)
            )
            state.temporal_outlier = temporal_outlier
            state.optical_transient_rejection = quality.transient_rejection
            state.optical_anomaly_fraction = quality.anomaly_fraction
            state.optical_anomaly_window_s = quality.anomaly_window_s
            state.optical_quality_phase = quality.phase
            state.optical_anomaly_reason = ",".join(quality.reasons)
            state.optical_stable_s = quality.stable_seconds
            state.optical_recovery_fraction = quality.recovery_fraction
            state.optical_position_spread_px = quality.position_spread_px
            state.optical_intensity_ratio = quality.ratios.get("intensidade")
            state.optical_area_ratio = quality.ratios.get("area")
            state.optical_width_ratio = quality.ratios.get("largura")
            state.optical_height_ratio = quality.ratios.get("altura")

        if border_persistence_s >= BORDER_CONFIRM_SECONDS:
            solicitar_parada(state, "ilha_tocou_a_borda_da_roi")
        if signal_lost_s >= SIGNAL_LOSS_LIMIT_SECONDS:
            solicitar_parada(state, "sinal_perdido_por_tempo_excessivo")

        values = state.snapshot()
        status, status_color = tracking_status(
            values,
            spot_touches_border=touches_border,
            temporal_outlier=temporal_outlier,
            instant_signal=candidate_valid,
            measurement_valid=measurement_valid,
        )

        event = values["safety_stop_reason"] or quality.event or exposure_event
        if quality.event:
            if quality.event != "anomalia_optica_recuperada":
                logger.save_event_frame(frame, quality.event)
            if quality.event.startswith("anomalia_optica_") and not quality.event.endswith(
                "recuperada"
            ):
                print(
                    "\nAnomalia optica: "
                    + ", ".join(quality.reasons)
                    + ". Mount parado; aguardando estabilidade."
                )
            elif quality.event == "anomalia_optica_recuperada":
                print(
                    "\nQualidade optica recuperada. "
                    "Reconstruindo a media antes de liberar o controle."
                )
        # Registrar a ausencia so depois que ela persiste: piscadas de fracao de
        # segundo sao a maioria e afogavam os eventos que importam.
        if candidate_valid:
            ausencia_registrada = False
        elif (
            not event
            and not ausencia_registrada
            and signal_lost_s >= EVENT_MIN_ABSENCE_SECONDS
        ):
            ausencia_registrada = True
            event = "alvo_ausente"
            logger.save_event_frame(frame, event)
        elif not event and measurement_valid and not signal_was_locked:
            event = "sinal_recuperado" if ever_locked else "sinal_inicial_confirmado"
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
            event_path = logger.save_event_frame(last_frame, reason, critical=True)
            if event_path is not None:
                print(f"Frame do evento: {display_path(event_path)}")
            print(f"\nPARADA DE SEGURANCA: {reason}")
            return ResultadoAquisicao(reason, last_frame)

        if last_display_t == 0.0 or now - last_display_t >= display_interval_s:
            display_frame = frame
            if measurement_valid:
                display_frame = np.clip(
                    temporal_estimate["mean_frame"], 0, 255
                ).astype(np.uint8)
            display.show(
                display_frame,
                x_cm=x_cm,
                y_cm=y_cm,
                dx=dx,
                dy=dy,
                state=values,
                status=status,
                status_color=status_color,
                elapsed_hours=(now - session_started) / 3600.0,
                session_hours=session_hours,
                sigma_centroide_px=selected.get("sigma_centroide_px"),
            )
            last_display_t = now

        if display.should_close():
            return ResultadoAquisicao("encerrado_pelo_usuario", last_frame)
