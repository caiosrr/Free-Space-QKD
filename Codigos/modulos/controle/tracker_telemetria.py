"""Persistencia de telemetria e imagens de eventos do tracker."""

import csv
import json
from collections import deque
from datetime import datetime
from pathlib import Path
import time

import cv2
import numpy as np

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import (
    BORDER_CONFIRM_SECONDS,
    BORDER_MIN_PEAK_RATIO,
    BORDER_MIN_SIGNATURE_SIMILARITY,
    CSV_FLUSH_SECONDS,
    CSV_LOG_HZ,
    FAST_CORRECTION_RADIUS_PX,
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_RADIUS_PX,
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    OPTICAL_AREA_RATIO_HIGH,
    OPTICAL_AREA_RATIO_LOW,
    OPTICAL_INTENSITY_RATIO_HIGH,
    OPTICAL_INTENSITY_RATIO_LOW,
    OPTICAL_LINEAR_SIZE_RATIO_HIGH,
    OPTICAL_LINEAR_SIZE_RATIO_LOW,
    OPTICAL_RECOVERY_STABLE_SECONDS,
    SIGNAL_LOSS_LIMIT_SECONDS,
    SLOW_BIAS_WARMUP_SECONDS,
    SLOW_BIAS_WINDOW_SECONDS,
    SLOW_CORRECTION_PERSISTENCE_SECONDS,
    TEMPORAL_RECOVERY_VALID_FRAMES,
    TEMPORAL_WARMUP_SECONDS,
    TEMPORAL_WINDOW_SECONDS,
    TRACKER_EVENT_IMAGE_LIMIT,
    TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS,
    VARIANCE_WINDOW_SECONDS,
    roi_size_for_backend,
)
from modulos.controle.cameras.backend import backend_name


class TrackerCsvLogger:
    """Grava CSV compacto, resumo da sessao e frames de eventos."""

    FIELDNAMES = [
        "data_hora", "tempo_decorrido_s", "estado", "sinal_encontrado",
        "x_cm_px", "y_cm_px", "alvo_x_px", "alvo_y_px", "erro_x_px",
        "erro_y_px", "distancia_px", "erro_x_filtrado_px", "erro_y_filtrado_px",
        "frames_na_media", "janela_media_s", "frames_recuperacao",
        "tempo_sem_sinal_s", "exposicao_us", "pico_bruto_alvo",
        "intensidade_integrada_alvo",
        "outlier_temporal", "variancia_x_px2", "variancia_y_px2",
        "desvio_padrao_2d_px", "erro_az_deg", "erro_alt_deg",
        "velocidade_az_deg_s", "velocidade_alt_deg_s", "azimute_absoluto_deg",
        "altitude_absoluta_deg", "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg", "loop_medicao_hz", "loop_controle_hz",
        "calibracao", "zona_parada_ativa", "correcao_lenta_ativa",
        "erro_controle_x_px", "erro_controle_y_px", "raio_controle_px",
        "janela_vies_lento_s", "vies_lento_pronto",
        "persistencia_erro_s", "fonte_erro_controle",
        "freio_ativo", "autoteste_ativo", "autoteste_aprovado",
        "qualidade_optica", "motivo_anomalia_optica",
        "razao_intensidade", "razao_area", "razao_largura", "razao_altura",
        "tempo_estavel_optico_s",
        "ilha_tocando_borda", "borda_compativel", "tempo_borda_s",
        "evento_seguranca",
    ]

    def __init__(self, output_dir, session_started, initial_az, initial_alt, max_hours):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = Path(output_dir) / "sessoes" / f"tracker_{timestamp}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.csv_path = self.session_dir / "telemetria.csv"
        self.summary_path = self.session_dir / "resumo.json"
        self._fp = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fp, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self._session_started = session_started
        self._last_write_t = 0.0
        self._last_flush_t = session_started
        self._samples = deque()
        self._event_frame_count = 0
        self._event_frames_suppressed = 0
        self._last_event_frame_t = float("-inf")
        self._terminal_frame_path = None
        self._summary = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "initial_azimuth_deg": initial_az,
            "initial_altitude_deg": initial_alt,
            "max_session_hours": max_hours,
            "max_offset_az_deg": MAX_OFFSET_AZ_DEG,
            "max_offset_alt_deg": MAX_OFFSET_ALT_DEG,
            "roi_size_px": roi_size_for_backend(backend_name()),
            "hold_enter_radius_px": HOLD_ENTER_RADIUS_PX,
            "hold_exit_radius_px": HOLD_EXIT_RADIUS_PX,
            "slow_bias_window_seconds": SLOW_BIAS_WINDOW_SECONDS,
            "slow_bias_warmup_seconds": SLOW_BIAS_WARMUP_SECONDS,
            "slow_correction_persistence_seconds": (
                SLOW_CORRECTION_PERSISTENCE_SECONDS
            ),
            "fast_correction_radius_px": FAST_CORRECTION_RADIUS_PX,
            "temporal_window_seconds": TEMPORAL_WINDOW_SECONDS,
            "temporal_warmup_seconds": TEMPORAL_WARMUP_SECONDS,
            "temporal_recovery_valid_frames": TEMPORAL_RECOVERY_VALID_FRAMES,
            "signal_loss_limit_seconds": SIGNAL_LOSS_LIMIT_SECONDS,
            "border_confirmation_seconds": BORDER_CONFIRM_SECONDS,
            "border_min_peak_ratio": BORDER_MIN_PEAK_RATIO,
            "border_min_signature_similarity": BORDER_MIN_SIGNATURE_SIMILARITY,
            "optical_quality_gate": "rolling_median_intensity_area_shape",
            "optical_recovery_stable_seconds": OPTICAL_RECOVERY_STABLE_SECONDS,
            "optical_intensity_ratio_range": [
                OPTICAL_INTENSITY_RATIO_LOW,
                OPTICAL_INTENSITY_RATIO_HIGH,
            ],
            "optical_area_ratio_range": [
                OPTICAL_AREA_RATIO_LOW,
                OPTICAL_AREA_RATIO_HIGH,
            ],
            "optical_linear_size_ratio_range": [
                OPTICAL_LINEAR_SIZE_RATIO_LOW,
                OPTICAL_LINEAR_SIZE_RATIO_HIGH,
            ],
            "detector": "locked_island",
            "event_image_limit": TRACKER_EVENT_IMAGE_LIMIT,
            "event_image_min_interval_seconds": (
                TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS
            ),
            "csv_path": display_path(self.csv_path),
        }

    def write(self, now, *, state_values, status, x_cm, y_cm, target_x, target_y,
              dx, dy, event=""):
        has_signal = bool(state_values["has_signal"])
        if has_signal:
            self._samples.append((now, float(dx), float(dy)))
        cutoff = now - VARIANCE_WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        if not event and self._last_write_t > 0.0:
            if (now - self._last_write_t) < (1.0 / CSV_LOG_HZ):
                return

        if self._samples:
            values = np.asarray([(item[1], item[2]) for item in self._samples], dtype=float)
            variance_x = float(np.var(values[:, 0]))
            variance_y = float(np.var(values[:, 1]))
        else:
            variance_x = variance_y = 0.0

        def number(value, digits=6):
            return "" if value is None else round(float(value), digits)

        row = {
            "data_hora": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "tempo_decorrido_s": round(now - self._session_started, 3),
            "estado": status,
            "sinal_encontrado": int(has_signal),
            "x_cm_px": number(x_cm, 3), "y_cm_px": number(y_cm, 3),
            "alvo_x_px": number(target_x, 3), "alvo_y_px": number(target_y, 3),
            "erro_x_px": number(dx, 3), "erro_y_px": number(dy, 3),
            "distancia_px": number(np.hypot(dx, dy), 3),
            "erro_x_filtrado_px": number(state_values["dx_filt_px"], 3),
            "erro_y_filtrado_px": number(state_values["dy_filt_px"], 3),
            "frames_na_media": int(state_values["temporal_frame_count"]),
            "janela_media_s": number(state_values["temporal_window_s"], 3),
            "frames_recuperacao": int(state_values["recovery_valid_frames"]),
            "tempo_sem_sinal_s": number(state_values["signal_lost_s"], 3),
            "exposicao_us": number(state_values["exposure_us"], 1),
            "pico_bruto_alvo": number(state_values["target_raw_peak"], 2),
            "intensidade_integrada_alvo": number(
                state_values["target_raw_total"], 2
            ),
            "outlier_temporal": int(bool(state_values["temporal_outlier"])),
            "variancia_x_px2": round(variance_x, 4),
            "variancia_y_px2": round(variance_y, 4),
            "desvio_padrao_2d_px": round(np.sqrt(variance_x + variance_y), 4),
            "erro_az_deg": number(state_values["err_az_deg"]),
            "erro_alt_deg": number(state_values["err_alt_deg"]),
            "velocidade_az_deg_s": number(state_values["cmd_az_deg_s"]),
            "velocidade_alt_deg_s": number(state_values["cmd_alt_deg_s"]),
            "azimute_absoluto_deg": number(state_values["mount_az_deg"]),
            "altitude_absoluta_deg": number(state_values["mount_alt_deg"]),
            "deslocamento_az_desde_inicio_deg": number(state_values["offset_az_deg"]),
            "deslocamento_alt_desde_inicio_deg": number(state_values["offset_alt_deg"]),
            "loop_medicao_hz": number(state_values["measurement_hz"], 2),
            "loop_controle_hz": number(state_values["control_loop_hz"], 2),
            "calibracao": state_values["calibration_name"],
            "zona_parada_ativa": int(bool(state_values["hold_active"])),
            "correcao_lenta_ativa": int(bool(state_values["trim_mode_active"])),
            "erro_controle_x_px": number(state_values["control_dx_px"], 3),
            "erro_controle_y_px": number(state_values["control_dy_px"], 3),
            "raio_controle_px": number(state_values["control_radius_px"], 3),
            "janela_vies_lento_s": number(state_values["slow_bias_window_s"], 3),
            "vies_lento_pronto": int(bool(state_values["slow_bias_ready"])),
            "persistencia_erro_s": number(
                state_values["correction_persistence_s"], 3
            ),
            "fonte_erro_controle": state_values["control_error_source"],
            "freio_ativo": int(bool(state_values["brake_active"])),
            "autoteste_ativo": int(bool(state_values["preflight_active"])),
            "autoteste_aprovado": int(bool(state_values["preflight_passed"])),
            "qualidade_optica": state_values["optical_quality_phase"],
            "motivo_anomalia_optica": state_values["optical_anomaly_reason"],
            "razao_intensidade": number(state_values["optical_intensity_ratio"], 3),
            "razao_area": number(state_values["optical_area_ratio"], 3),
            "razao_largura": number(state_values["optical_width_ratio"], 3),
            "razao_altura": number(state_values["optical_height_ratio"], 3),
            "tempo_estavel_optico_s": number(state_values["optical_stable_s"], 3),
            "ilha_tocando_borda": int(bool(state_values["spot_touches_border"])),
            "borda_compativel": int(
                bool(state_values["border_candidate_plausible"])
            ),
            "tempo_borda_s": number(state_values["border_persistence_s"], 3),
            "evento_seguranca": event,
        }
        self._writer.writerow(row)
        self._last_write_t = now
        if event or (now - self._last_flush_t) >= CSV_FLUSH_SECONDS:
            self._fp.flush()
            self._last_flush_t = now

    def save_event_frame(self, frame, event, *, critical=False):
        """Salva amostras espaçadas; eventos terminais sempre têm uma reserva."""
        if frame is None:
            return None
        safe_event = "".join(c if c.isalnum() else "_" for c in event).strip("_")
        timestamp = datetime.now().strftime("%H-%M-%S-%f")[:-3]
        if critical:
            path = self.session_dir / (
                f"evento_terminal_{timestamp}_{safe_event or 'seguranca'}.png"
            )
        else:
            now = time.monotonic()
            if (
                self._event_frame_count >= TRACKER_EVENT_IMAGE_LIMIT
                or now - self._last_event_frame_t
                < TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS
            ):
                self._event_frames_suppressed += 1
                return None
            path = self.session_dir / (
                f"evento_{self._event_frame_count + 1:03d}_{timestamp}_"
                f"{safe_event or 'seguranca'}.png"
            )

        if not cv2.imwrite(str(path), frame):
            return None
        if critical:
            self._terminal_frame_path = display_path(path)
        else:
            self._event_frame_count += 1
            self._last_event_frame_t = time.monotonic()
        return path

    def close(self, *, reason, return_result=None):
        self._summary.update({
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finish_reason": reason,
            "return_to_start": return_result,
            "event_images_saved": self._event_frame_count,
            "event_images_suppressed": self._event_frames_suppressed,
            "terminal_event_frame": self._terminal_frame_path,
        })
        self.summary_path.write_text(
            json.dumps(self._summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()
