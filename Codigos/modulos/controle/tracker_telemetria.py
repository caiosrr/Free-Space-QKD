"""Persistencia de telemetria e imagens de eventos do tracker."""

import csv
import json
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import (
    CSV_FLUSH_SECONDS,
    CSV_LOG_HZ,
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_RADIUS_PX,
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    SIGNAL_LOSS_LIMIT_SECONDS,
    TEMPORAL_RECOVERY_VALID_FRAMES,
    TEMPORAL_WARMUP_SECONDS,
    TEMPORAL_WINDOW_SECONDS,
    TRACKER_EVENT_IMAGE_LIMIT,
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
        "outlier_temporal", "variancia_x_px2", "variancia_y_px2",
        "desvio_padrao_2d_px", "erro_az_deg", "erro_alt_deg",
        "velocidade_az_deg_s", "velocidade_alt_deg_s", "azimute_absoluto_deg",
        "altitude_absoluta_deg", "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg", "loop_medicao_hz", "loop_controle_hz",
        "calibracao", "zona_parada_ativa", "correcao_lenta_ativa",
        "freio_ativo", "ilha_tocando_borda", "evento_seguranca",
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
            "temporal_window_seconds": TEMPORAL_WINDOW_SECONDS,
            "temporal_warmup_seconds": TEMPORAL_WARMUP_SECONDS,
            "temporal_recovery_valid_frames": TEMPORAL_RECOVERY_VALID_FRAMES,
            "signal_loss_limit_seconds": SIGNAL_LOSS_LIMIT_SECONDS,
            "detector": "locked_island",
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
            "freio_ativo": int(bool(state_values["brake_active"])),
            "ilha_tocando_borda": int(bool(state_values["spot_touches_border"])),
            "evento_seguranca": event,
        }
        self._writer.writerow(row)
        self._last_write_t = now
        if event or (now - self._last_flush_t) >= CSV_FLUSH_SECONDS:
            self._fp.flush()
            self._last_flush_t = now

    def save_event_frame(self, frame, event):
        if frame is None or self._event_frame_count >= TRACKER_EVENT_IMAGE_LIMIT:
            return None
        self._event_frame_count += 1
        safe_event = "".join(c if c.isalnum() else "_" for c in event).strip("_")
        timestamp = datetime.now().strftime("%H-%M-%S-%f")[:-3]
        path = self.session_dir / (
            f"evento_{self._event_frame_count:03d}_{timestamp}_{safe_event or 'seguranca'}.png"
        )
        cv2.imwrite(str(path), frame)
        return path

    def close(self, *, reason, return_result=None):
        self._summary.update({
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finish_reason": reason,
            "return_to_start": return_result,
        })
        self.summary_path.write_text(
            json.dumps(self._summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()
