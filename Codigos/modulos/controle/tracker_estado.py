"""Estado compartilhado entre aquisicao, controle, watchdog e interface."""

from dataclasses import dataclass, field, fields
import threading


@dataclass
class TrackerState:
    """Uma fotografia mutavel da sessao, protegida por ``lock``."""

    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    stop: bool = False
    has_signal: bool = False
    measurement_seq: int = 0
    measurement_ts: float = 0.0
    dx_filt_px: float = 0.0
    dy_filt_px: float = 0.0
    err_az_deg: float = 0.0
    err_alt_deg: float = 0.0
    cmd_az_deg_s: float = 0.0
    cmd_alt_deg_s: float = 0.0
    brake_active: bool = False
    calibration_name: str = "continua"
    trim_mode_active: bool = False
    hold_active: bool = True
    control_dx_px: float = 0.0
    control_dy_px: float = 0.0
    control_radius_px: float = 0.0
    slow_bias_window_s: float = 0.0
    slow_bias_ready: bool = False
    correction_persistence_s: float = 0.0
    control_error_source: str = "repouso"
    measurement_hz: float = 0.0
    temporal_frame_count: int = 0
    temporal_window_s: float = 0.0
    recovery_valid_frames: int = 0
    signal_lost_s: float = 0.0
    exposure_us: float = 0.0
    target_raw_peak: float | None = None
    target_raw_total: float | None = None
    temporal_outlier: bool = False
    optical_quality_phase: str = "aquecendo"
    optical_anomaly_reason: str = ""
    optical_stable_s: float = 0.0
    optical_intensity_ratio: float | None = None
    optical_area_ratio: float | None = None
    optical_width_ratio: float | None = None
    optical_height_ratio: float | None = None
    control_loop_hz: float = 0.0
    spot_touches_border: bool = False
    border_candidate_plausible: bool = False
    border_persistence_s: float = 0.0
    mount_az_deg: float | None = None
    mount_alt_deg: float | None = None
    offset_az_deg: float = 0.0
    offset_alt_deg: float = 0.0
    safety_stop_reason: str | None = None
    watchdog_error: str | None = None
    preflight_active: bool = False
    preflight_passed: bool = False

    def snapshot(self) -> dict:
        """Copia atomica usada pela tela e pela telemetria."""
        with self.lock:
            return {
                item.name: getattr(self, item.name)
                for item in fields(self)
                if item.name != "lock"
            }

    def request_stop(self, reason: str | None = None) -> bool:
        """Sinaliza encerramento; o primeiro motivo de seguranca prevalece."""
        with self.lock:
            if reason is not None:
                if self.safety_stop_reason is not None:
                    return False
                self.safety_stop_reason = str(reason)
            self.stop = True
            return True


# Nome antigo mantido para nao quebrar scripts externos durante a transicao.
SharedState = TrackerState
