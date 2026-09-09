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
from modulos.configuracoes import optica
from modulos.configuracoes.tracker import (
    AUTO_EXPOSURE_BACKGROUND_HIGH,
    AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT,
    AUTO_EXPOSURE_CNR_HIGH,
    AUTO_EXPOSURE_CNR_LOW,
    AUTO_EXPOSURE_ENABLED,
    AUTO_EXPOSURE_HISTORY_SECONDS,
    AUTO_EXPOSURE_MAX_STEP_FRACTION,
    AUTO_EXPOSURE_MAX_US,
    AUTO_EXPOSURE_MIN_TRUSTED_FRACTION,
    AUTO_EXPOSURE_MIN_US,
    AUTO_EXPOSURE_REDUCTION_STEP_FRACTION,
    AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS,
    AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS,
    AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS,
    AUTO_EXPOSURE_SATURATION_FRACTION,
    AUTO_EXPOSURE_UPDATE_SECONDS,
    BORDER_CONFIRM_SECONDS,
    BORDER_MIN_PEAK_RATIO,
    BORDER_MIN_SIGNATURE_SIMILARITY,
    CSV_FLUSH_SECONDS,
    CSV_LOG_HZ,
    FAST_CORRECTION_RADIUS_PX,
    FAST_ERROR_CONFIRM_SECONDS,
    FAST_ERROR_MIN_DIRECTION_COHERENCE,
    FAST_ERROR_MIN_LARGE_FRACTION,
    FAST_ERROR_MIN_SAMPLES,
    FAST_ERROR_WINDOW_SECONDS,
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_RADIUS_PX,
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    OPTICAL_AREA_RATIO_HIGH,
    OPTICAL_AREA_RATIO_LOW,
    OPTICAL_ANOMALY_ENTRY_BAD_FRACTION,
    OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES,
    OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS,
    OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS,
    OPTICAL_INTENSITY_RATIO_HIGH,
    OPTICAL_INTENSITY_RATIO_LOW,
    OPTICAL_LINEAR_SIZE_RATIO_HIGH,
    OPTICAL_LINEAR_SIZE_RATIO_LOW,
    OPTICAL_RECOVERY_ACCEPTED_FRACTION,
    OPTICAL_RECOVERY_MIN_SAMPLES,
    OPTICAL_RECOVERY_POSITION_P90_PX,
    OPTICAL_RECOVERY_STABLE_SECONDS,
    OPTICAL_RECOVERY_WINDOW_SECONDS,
    SIGNAL_LOSS_LIMIT_SECONDS,
    SLOW_BIAS_WARMUP_SECONDS,
    SLOW_BIAS_WINDOW_SECONDS,
    SLOW_CORRECTION_PERSISTENCE_SECONDS,
    TEMPORAL_RECOVERY_VALID_FRAMES,
    TEMPORAL_OPTICAL_HOLD_SECONDS,
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
        "erro_y_px", "distancia_px", "distancia_alvo_m", "erro_x_filtrado_px", "erro_y_filtrado_px",
        "frames_na_media", "janela_media_s", "frames_recuperacao",
        "alvo_detectado", "tempo_sem_sinal_s", "tempo_aparencia_instavel_s",
        "exposicao_us", "auto_exposicao_ativa",
        "motivo_autoexposicao", "pico_mediano_autoexposicao",
        "fundo_percentil_autoexposicao", "fracao_saturada_autoexposicao",
        "fundo_local_autoexposicao", "ruido_local_autoexposicao",
        "cnr_autoexposicao", "fracao_alvo_confiavel_autoexposicao",
        "fracao_saturada_alvo_autoexposicao", "reversao_autoexposicao",
        "ajustes_autoexposicao", "pico_bruto_alvo",
        "intensidade_integrada_alvo",
        "outlier_temporal", "frame_optico_transitorio_rejeitado",
        "variancia_x_px2", "variancia_y_px2",
        "desvio_padrao_2d_px", "erro_az_deg", "erro_alt_deg",
        "velocidade_az_deg_s", "velocidade_alt_deg_s", "azimute_absoluto_deg",
        "altitude_absoluta_deg", "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg", "loop_medicao_hz", "loop_controle_hz",
        "calibracao", "zona_parada_ativa", "zona_parada_raio_px",
        "regime_controle",
        "correcao_lenta_ativa",
        "erro_controle_x_px", "erro_controle_y_px", "raio_controle_px",
        "janela_vies_lento_s", "vies_lento_pronto",
        "sombra_vies_x_px", "sombra_vies_y_px", "sombra_vies_raio_px",
        "sombra_vies_pronto", "sombra_janela_s", "sombra_correcoes",
        "janela_erro_grande_s", "fracao_erro_grande",
        "coerencia_direcional_erro_grande", "erro_grande_confirmado",
        "persistencia_erro_s", "fonte_erro_controle",
        "fase_correcao", "ciclos_correcao", "espera_pos_movimento_s",
        "freio_ativo", "autoteste_ativo", "autoteste_aprovado",
        "qualidade_optica", "motivo_anomalia_optica",
        "fracao_anomalia_janela", "duracao_janela_anomalia_s",
        "razao_intensidade", "razao_area", "razao_largura", "razao_altura",
        "tempo_estavel_optico_s", "fracao_consenso_recuperacao",
        "dispersao_posicao_recuperacao_px",
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
        self._exposure_min_us = float("inf")
        self._exposure_max_us = float("-inf")
        self._auto_exposure_adjustments = 0
        self._logged_rows = 0
        self._target_present_rows = 0
        self._optical_transient_rejection_rows = 0
        self._max_target_absent_s = 0.0
        self._max_optical_unstable_s = 0.0
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
            "fast_error_window_seconds": FAST_ERROR_WINDOW_SECONDS,
            "fast_error_confirm_seconds": FAST_ERROR_CONFIRM_SECONDS,
            "fast_error_min_large_fraction": (
                FAST_ERROR_MIN_LARGE_FRACTION
            ),
            "fast_error_min_direction_coherence": (
                FAST_ERROR_MIN_DIRECTION_COHERENCE
            ),
            "fast_error_min_samples": FAST_ERROR_MIN_SAMPLES,
            "control_strategy": "bounded_pulses_with_post_motion_measurements",
            "auto_exposure_enabled": (
                AUTO_EXPOSURE_ENABLED and backend_name() == "ids"
            ),
            "auto_exposure_range_us": [
                AUTO_EXPOSURE_MIN_US,
                AUTO_EXPOSURE_MAX_US,
            ],
            "auto_exposure_strategy": "minimum_exposure_with_local_cnr",
            "auto_exposure_cnr_definition": (
                "(target_aperture_p98-local_annulus_median)/"
                "local_annulus_robust_noise"
            ),
            "auto_exposure_cnr_range": [
                AUTO_EXPOSURE_CNR_LOW,
                AUTO_EXPOSURE_CNR_HIGH,
            ],
            "auto_exposure_min_trusted_fraction": (
                AUTO_EXPOSURE_MIN_TRUSTED_FRACTION
            ),
            "auto_exposure_update_seconds": AUTO_EXPOSURE_UPDATE_SECONDS,
            "auto_exposure_history_seconds": AUTO_EXPOSURE_HISTORY_SECONDS,
            "auto_exposure_max_step_fraction": AUTO_EXPOSURE_MAX_STEP_FRACTION,
            "auto_exposure_reduction_step_fraction": (
                AUTO_EXPOSURE_REDUCTION_STEP_FRACTION
            ),
            "auto_exposure_background_high": AUTO_EXPOSURE_BACKGROUND_HIGH,
            "auto_exposure_background_increase_limit": (
                AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT
            ),
            "auto_exposure_saturation_fraction": (
                AUTO_EXPOSURE_SATURATION_FRACTION
            ),
            "auto_exposure_rollback_window_seconds": (
                AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS
            ),
            "auto_exposure_rollback_loss_seconds": (
                AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS
            ),
            "auto_exposure_safety_update_seconds": (
                AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS
            ),
            "temporal_window_seconds": TEMPORAL_WINDOW_SECONDS,
            "temporal_warmup_seconds": TEMPORAL_WARMUP_SECONDS,
            "temporal_recovery_valid_frames": TEMPORAL_RECOVERY_VALID_FRAMES,
            "temporal_optical_hold_seconds": TEMPORAL_OPTICAL_HOLD_SECONDS,
            "signal_loss_limit_seconds": SIGNAL_LOSS_LIMIT_SECONDS,
            "border_confirmation_seconds": BORDER_CONFIRM_SECONDS,
            "border_min_peak_ratio": BORDER_MIN_PEAK_RATIO,
            "border_min_signature_similarity": BORDER_MIN_SIGNATURE_SIMILARITY,
            "optical_quality_gate": (
                "debounced_rolling_median_with_recovery_consensus"
            ),
            "optical_anomaly_entry_window_seconds": (
                OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS
            ),
            "optical_anomaly_entry_min_coverage_seconds": (
                OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS
            ),
            "optical_anomaly_entry_bad_fraction": (
                OPTICAL_ANOMALY_ENTRY_BAD_FRACTION
            ),
            "optical_anomaly_entry_min_bad_frames": (
                OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES
            ),
            "optical_recovery_stable_seconds": OPTICAL_RECOVERY_STABLE_SECONDS,
            "optical_recovery_window_seconds": OPTICAL_RECOVERY_WINDOW_SECONDS,
            "optical_recovery_accepted_fraction": (
                OPTICAL_RECOVERY_ACCEPTED_FRACTION
            ),
            "optical_recovery_min_samples": OPTICAL_RECOVERY_MIN_SAMPLES,
            "optical_recovery_position_p90_px": (
                OPTICAL_RECOVERY_POSITION_P90_PX
            ),
            "signal_loss_timer_basis": "locked_target_absent",
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
            # O mesmo erro no plano do alvo. Os limiares do controle continuam
            # em pixels, amarrados a resolucao do mount; isto e para leitura.
            "distancia_alvo_m": number(
                optica.px_para_metros(float(np.hypot(dx, dy))), 4
            ),
            "erro_x_filtrado_px": number(state_values["dx_filt_px"], 3),
            "erro_y_filtrado_px": number(state_values["dy_filt_px"], 3),
            "frames_na_media": int(state_values["temporal_frame_count"]),
            "janela_media_s": number(state_values["temporal_window_s"], 3),
            "frames_recuperacao": int(state_values["recovery_valid_frames"]),
            "alvo_detectado": int(bool(state_values["target_present"])),
            "tempo_sem_sinal_s": number(state_values["signal_lost_s"], 3),
            "tempo_aparencia_instavel_s": number(
                state_values["optical_unstable_s"], 3
            ),
            "exposicao_us": number(state_values["exposure_us"], 1),
            "auto_exposicao_ativa": int(
                bool(state_values["auto_exposure_enabled"])
            ),
            "motivo_autoexposicao": state_values["auto_exposure_reason"],
            "pico_mediano_autoexposicao": number(
                state_values["auto_exposure_peak_median"], 2
            ),
            "fundo_percentil_autoexposicao": number(
                state_values["auto_exposure_background"], 2
            ),
            "fracao_saturada_autoexposicao": number(
                state_values["auto_exposure_saturation_fraction"], 6
            ),
            "fundo_local_autoexposicao": number(
                state_values["auto_exposure_local_background"], 2
            ),
            "ruido_local_autoexposicao": number(
                state_values["auto_exposure_local_noise"], 3
            ),
            "cnr_autoexposicao": number(
                state_values["auto_exposure_cnr"], 3
            ),
            "fracao_alvo_confiavel_autoexposicao": number(
                state_values["auto_exposure_trusted_fraction"], 4
            ),
            "fracao_saturada_alvo_autoexposicao": number(
                state_values["auto_exposure_target_saturation_fraction"], 6
            ),
            "reversao_autoexposicao": int(
                bool(state_values["auto_exposure_rollback"])
            ),
            "ajustes_autoexposicao": int(
                state_values["auto_exposure_adjustments"]
            ),
            "pico_bruto_alvo": number(state_values["target_raw_peak"], 2),
            "intensidade_integrada_alvo": number(
                state_values["target_raw_total"], 2
            ),
            "outlier_temporal": int(bool(state_values["temporal_outlier"])),
            "frame_optico_transitorio_rejeitado": int(
                bool(state_values["optical_transient_rejection"])
            ),
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
            "zona_parada_raio_px": number(state_values["hold_enter_radius_px"], 2),
            "regime_controle": state_values["control_regime"],
            "correcao_lenta_ativa": int(bool(state_values["trim_mode_active"])),
            "erro_controle_x_px": number(state_values["control_dx_px"], 3),
            "erro_controle_y_px": number(state_values["control_dy_px"], 3),
            "raio_controle_px": number(state_values["control_radius_px"], 3),
            "janela_vies_lento_s": number(state_values["slow_bias_window_s"], 3),
            "vies_lento_pronto": int(bool(state_values["slow_bias_ready"])),
            # Modo sombra: o que uma zona de repouso mais apertada teria feito.
            "sombra_vies_x_px": number(state_values["shadow_bias_dx_px"], 3),
            "sombra_vies_y_px": number(state_values["shadow_bias_dy_px"], 3),
            "sombra_vies_raio_px": number(state_values["shadow_bias_radius_px"], 3),
            "sombra_vies_pronto": int(bool(state_values["shadow_bias_ready"])),
            "sombra_janela_s": number(state_values["shadow_bias_window_s"], 1),
            "sombra_correcoes": state_values["shadow_corrections"],
            "janela_erro_grande_s": number(
                state_values["fast_error_window_s"], 3
            ),
            "fracao_erro_grande": number(
                state_values["fast_error_large_fraction"], 4
            ),
            "coerencia_direcional_erro_grande": number(
                state_values["fast_error_direction_coherence"], 4
            ),
            "erro_grande_confirmado": int(
                bool(state_values["fast_error_ready"])
            ),
            "persistencia_erro_s": number(
                state_values["correction_persistence_s"], 3
            ),
            "fonte_erro_controle": state_values["control_error_source"],
            "fase_correcao": state_values.get("correction_phase", "pronto"),
            "ciclos_correcao": state_values.get("correction_cycles", 0),
            "espera_pos_movimento_s": number(state_values.get("post_motion_wait_s", 0.0), 3),
            "freio_ativo": int(bool(state_values["brake_active"])),
            "autoteste_ativo": int(bool(state_values["preflight_active"])),
            "autoteste_aprovado": int(bool(state_values["preflight_passed"])),
            "qualidade_optica": state_values["optical_quality_phase"],
            "motivo_anomalia_optica": state_values["optical_anomaly_reason"],
            "fracao_anomalia_janela": number(
                state_values["optical_anomaly_fraction"], 4
            ),
            "duracao_janela_anomalia_s": number(
                state_values["optical_anomaly_window_s"], 3
            ),
            "razao_intensidade": number(state_values["optical_intensity_ratio"], 3),
            "razao_area": number(state_values["optical_area_ratio"], 3),
            "razao_largura": number(state_values["optical_width_ratio"], 3),
            "razao_altura": number(state_values["optical_height_ratio"], 3),
            "tempo_estavel_optico_s": number(state_values["optical_stable_s"], 3),
            "fracao_consenso_recuperacao": number(
                state_values["optical_recovery_fraction"], 4
            ),
            "dispersao_posicao_recuperacao_px": number(
                state_values["optical_position_spread_px"], 3
            ),
            "ilha_tocando_borda": int(bool(state_values["spot_touches_border"])),
            "borda_compativel": int(
                bool(state_values["border_candidate_plausible"])
            ),
            "tempo_borda_s": number(state_values["border_persistence_s"], 3),
            "evento_seguranca": event,
        }
        exposure_us = state_values["exposure_us"]
        if exposure_us is not None and np.isfinite(float(exposure_us)):
            self._exposure_min_us = min(self._exposure_min_us, float(exposure_us))
            self._exposure_max_us = max(self._exposure_max_us, float(exposure_us))
        self._auto_exposure_adjustments = max(
            self._auto_exposure_adjustments,
            int(state_values["auto_exposure_adjustments"]),
        )
        self._logged_rows += 1
        self._target_present_rows += int(bool(state_values["target_present"]))
        self._optical_transient_rejection_rows += int(
            bool(state_values["optical_transient_rejection"])
        )
        self._max_target_absent_s = max(
            self._max_target_absent_s,
            float(state_values["signal_lost_s"]),
        )
        self._max_optical_unstable_s = max(
            self._max_optical_unstable_s,
            float(state_values["optical_unstable_s"]),
        )
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
        exposure_range = None
        if np.isfinite(self._exposure_min_us) and np.isfinite(self._exposure_max_us):
            exposure_range = [
                round(self._exposure_min_us, 1),
                round(self._exposure_max_us, 1),
            ]
        self._summary.update({
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finish_reason": reason,
            "return_to_start": return_result,
            "event_images_saved": self._event_frame_count,
            "event_images_suppressed": self._event_frames_suppressed,
            "terminal_event_frame": self._terminal_frame_path,
            "auto_exposure_adjustments": self._auto_exposure_adjustments,
            "exposure_used_range_us": exposure_range,
            "target_present_percent_logged": (
                0.0
                if self._logged_rows == 0
                else round(100.0 * self._target_present_rows / self._logged_rows, 3)
            ),
            "max_target_absent_seconds": round(self._max_target_absent_s, 3),
            "max_optical_unstable_seconds": round(
                self._max_optical_unstable_s,
                3,
            ),
            "optical_transient_rejections_logged": (
                self._optical_transient_rejection_rows
            ),
        })
        self.summary_path.write_text(
            json.dumps(self._summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()
