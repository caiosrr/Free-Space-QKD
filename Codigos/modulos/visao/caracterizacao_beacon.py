"""Caracteriza a variacao temporal de um beacon sem controlar o mount.

O programa usa a camera IDS e a mesma selecao manual/trava de identidade do
tracker, mas nao conecta ao ASCOM e nao possui nenhuma chamada de movimento.
Cada sessao salva telemetria por frame, medias da imagem e frames de eventos.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np


# =============================================================================
# BLOCO 1 - CAMINHOS E PARAMETROS FACEIS DE AJUSTAR
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
CODIGOS_DIR = SCRIPT_DIR.parents[1]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.configuracoes import camera_ids as camera_config


DEFAULT_DURATION_MIN = 10.0
DEFAULT_ROI_SIZE_PX = 512
DISPLAY_HZ = 10.0
MAX_IDENTITY_JUMP_PX = 180.0

# Gatilhos apenas salvam imagens; eles nunca encerram a sessao nem movem o mount.
POSITION_EVENT_PX = 15.0
SIZE_RATIO_LOW = 0.55
SIZE_RATIO_HIGH = 1.80
INTENSITY_RATIO_LOW = 0.35
INTENSITY_RATIO_HIGH = 2.80
EVENT_BASELINE_FRAMES = 20
EVENT_COOLDOWN_S = 5.0

# Limites rigidos para uma sessao longa nao ocupar o disco inteiro.
MAX_TELEMETRY_MB = 512.0
MAX_EVENT_IMAGE_SETS = 200
MAX_EVENT_STORAGE_MB = 256.0
MIN_FREE_DISK_GB = 2.0
DISK_CHECK_INTERVAL_S = 5.0

# Regiao local usada para estimar largura e orientacao do spot.
SHAPE_RADIUS_PX = 90


CSV_FIELDS = [
    "timestamp_iso",
    "elapsed_s",
    "frame_index",
    "status",
    "valid",
    "capture_dt_s",
    "measured_fps",
    "x_roi_px",
    "y_roi_px",
    "x_sensor_px",
    "y_sensor_px",
    "dx_px",
    "dy_px",
    "distance_px",
    "raw_peak",
    "raw_total",
    "area_px",
    "bbox_w_px",
    "bbox_h_px",
    "compactness",
    "similarity_primary",
    "sigma_x_px",
    "sigma_y_px",
    "sigma_major_px",
    "sigma_minor_px",
    "angle_deg",
    "candidate_count",
    "touches_border",
    "raw_min",
    "raw_max",
    "raw_median",
    "raw_std",
    "pedestal",
    "event",
]


# =============================================================================
# BLOCO 2 - ESTATISTICAS, FORMA DO SPOT E DETECCAO DE EVENTOS
# =============================================================================


@dataclass
class OnlineStats:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf

    def add(self, value: float) -> None:
        value = float(value)
        if not np.isfinite(value):
            return
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    def as_dict(self) -> dict[str, float | int | None]:
        std = math.sqrt(self.m2 / (self.count - 1)) if self.count > 1 else 0.0
        return {
            "count": self.count,
            "mean": self.mean if self.count else None,
            "std": std if self.count else None,
            "min": self.minimum if self.count else None,
            "max": self.maximum if self.count else None,
        }


@dataclass
class RunningImageMean:
    count: int = 0
    mean: np.ndarray | None = None

    def add(self, frame: np.ndarray) -> None:
        frame_float = frame.astype(np.float64, copy=False)
        if self.mean is None:
            self.mean = frame_float.copy()
            self.count = 1
            return
        if self.mean.shape != frame_float.shape:
            raise ValueError("Todos os frames da media devem ter o mesmo tamanho.")
        self.count += 1
        self.mean += (frame_float - self.mean) / self.count


def measure_spot_shape(
    raw_frame: np.ndarray,
    x_px: float,
    y_px: float,
    pedestal: float,
    threshold_percent: float,
    radius_px: int = SHAPE_RADIUS_PX,
) -> dict[str, float] | None:
    """Calcula momentos de segunda ordem numa vizinhanca fixa do centroide."""
    frame = raw_frame.astype(np.float64, copy=False)
    height, width = frame.shape[:2]
    x0 = max(0, int(math.floor(x_px - radius_px)))
    x1 = min(width, int(math.ceil(x_px + radius_px + 1)))
    y0 = max(0, int(math.floor(y_px - radius_px)))
    y1 = min(height, int(math.ceil(y_px + radius_px + 1)))
    signal = np.clip(frame[y0:y1, x0:x1] - float(pedestal), 0.0, None)
    if signal.size == 0 or float(signal.max()) <= 0.0:
        return None

    threshold = float(signal.max()) * float(np.clip(threshold_percent, 0.02, 0.90))
    weights = np.where(signal >= threshold, signal, 0.0)
    total = float(weights.sum())
    if total <= 0.0:
        return None

    yy, xx = np.indices(weights.shape, dtype=np.float64)
    xx += x0
    yy += y0
    mean_x = float((xx * weights).sum() / total)
    mean_y = float((yy * weights).sum() / total)
    centered_x = xx - mean_x
    centered_y = yy - mean_y
    var_x = float((centered_x * centered_x * weights).sum() / total)
    var_y = float((centered_y * centered_y * weights).sum() / total)
    cov_xy = float((centered_x * centered_y * weights).sum() / total)
    covariance = np.array([[var_x, cov_xy], [cov_xy, var_y]], dtype=float)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    order = np.argsort(eigenvalues)[::-1]
    major_vector = eigenvectors[:, order[0]]

    return {
        "sigma_x_px": math.sqrt(max(var_x, 0.0)),
        "sigma_y_px": math.sqrt(max(var_y, 0.0)),
        "sigma_major_px": math.sqrt(float(eigenvalues[order[0]])),
        "sigma_minor_px": math.sqrt(float(eigenvalues[order[1]])),
        "angle_deg": math.degrees(math.atan2(major_vector[1], major_vector[0])),
    }


@dataclass
class EventDetector:
    baseline_frames: int = EVENT_BASELINE_FRAMES
    cooldown_s: float = EVENT_COOLDOWN_S
    position_event_px: float = POSITION_EVENT_PX
    last_signal_state: bool | None = None
    last_valid_xy: tuple[float, float] | None = None
    last_saved_by_kind: dict[str, float] = field(default_factory=dict)
    intensity_history: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    size_history: deque[float] = field(default_factory=lambda: deque(maxlen=120))

    def _allow(self, kind: str, now_s: float) -> bool:
        last = self.last_saved_by_kind.get(kind)
        if last is not None and (now_s - last) < self.cooldown_s:
            return False
        self.last_saved_by_kind[kind] = now_s
        return True

    def update(
        self,
        now_s: float,
        *,
        valid: bool,
        touches_border: bool,
        x_px: float | None,
        y_px: float | None,
        raw_total: float | None,
        sigma_major_px: float | None,
    ) -> list[str]:
        candidates: list[str] = []

        if self.last_signal_state is not None and valid != self.last_signal_state:
            candidates.append("signal_recovered" if valid else "signal_lost")
        if touches_border:
            candidates.append("spot_at_roi_border")

        if valid and x_px is not None and y_px is not None:
            if self.last_valid_xy is not None:
                jump = float(np.hypot(x_px - self.last_valid_xy[0], y_px - self.last_valid_xy[1]))
                if jump >= self.position_event_px:
                    candidates.append("sudden_position_jump")

            if raw_total is not None and len(self.intensity_history) >= self.baseline_frames:
                baseline = float(np.median(self.intensity_history))
                if baseline > 0:
                    ratio = float(raw_total) / baseline
                    if ratio < INTENSITY_RATIO_LOW:
                        candidates.append("intensity_drop")
                    elif ratio > INTENSITY_RATIO_HIGH:
                        candidates.append("intensity_increase")

            if sigma_major_px is not None and len(self.size_history) >= self.baseline_frames:
                baseline = float(np.median(self.size_history))
                if baseline > 0:
                    ratio = float(sigma_major_px) / baseline
                    if ratio < SIZE_RATIO_LOW:
                        candidates.append("spot_shrank")
                    elif ratio > SIZE_RATIO_HIGH:
                        candidates.append("spot_expanded")

            self.last_valid_xy = (float(x_px), float(y_px))
            if raw_total is not None and raw_total > 0:
                self.intensity_history.append(float(raw_total))
            if sigma_major_px is not None and sigma_major_px > 0:
                self.size_history.append(float(sigma_major_px))

        self.last_signal_state = bool(valid)
        return [kind for kind in dict.fromkeys(candidates) if self._allow(kind, now_s)]


# =============================================================================
# BLOCO 3 - COORDENADAS, ARQUIVOS E INTERFACE VISUAL
# =============================================================================


def displayed_to_raw_sensor(
    x_px: float,
    y_px: float,
    sensor_w: int,
    sensor_h: int,
    rotated_180: bool,
) -> tuple[float, float]:
    if not rotated_180:
        return float(x_px), float(y_px)
    return float(sensor_w - 1 - x_px), float(sensor_h - 1 - y_px)


def roi_to_displayed_sensor(
    x_roi: float,
    y_roi: float,
    actual_roi: tuple[int, int, int, int],
    sensor_w: int,
    sensor_h: int,
    rotated_180: bool,
) -> tuple[float, float]:
    roi_w, roi_h, offset_x, offset_y = actual_roi
    if not rotated_180:
        return float(offset_x + x_roi), float(offset_y + y_roi)
    raw_x = offset_x + (roi_w - 1 - x_roi)
    raw_y = offset_y + (roi_h - 1 - y_roi)
    return float(sensor_w - 1 - raw_x), float(sensor_h - 1 - raw_y)


def target_in_actual_roi(
    target_x_display: float,
    target_y_display: float,
    actual_roi: tuple[int, int, int, int],
    sensor_w: int,
    sensor_h: int,
    rotated_180: bool,
) -> tuple[float, float]:
    raw_x, raw_y = displayed_to_raw_sensor(
        target_x_display, target_y_display, sensor_w, sensor_h, rotated_180
    )
    roi_w, roi_h, offset_x, offset_y = actual_roi
    local_raw_x = raw_x - offset_x
    local_raw_y = raw_y - offset_y
    if not rotated_180:
        return float(local_raw_x), float(local_raw_y)
    return float(roi_w - 1 - local_raw_x), float(roi_h - 1 - local_raw_y)


def create_session_dir() -> Path:
    root = Path(
        getattr(
            camera_config,
            "BEACON_CHARACTERIZATION_OUTPUT_DIR",
            camera_config.RESULTS_DIR / "caracterizacao_beacon",
        )
    )
    session = root / "sessoes" / datetime.now().strftime("beacon_%Y-%m-%d_%H-%M-%S")
    (session / "eventos").mkdir(parents=True, exist_ok=False)
    return session


def json_write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_png_unicode(path: Path, image: np.ndarray) -> None:
    """Grava PNG em caminhos Unicode no Windows sem depender de cv2.imwrite."""
    image_u8 = np.clip(image, 0, 255).astype(np.uint8)
    success, encoded = cv2.imencode(".png", image_u8)
    if not success:
        raise RuntimeError(f"OpenCV nao conseguiu codificar o PNG: {path}")
    path.write_bytes(encoded.tobytes())


def save_mean(session_dir: Path, name: str, running: RunningImageMean) -> None:
    if running.mean is None or running.count == 0:
        return
    mean_float = running.mean.astype(np.float32)
    np.save(session_dir / f"{name}.npy", mean_float)
    image = np.clip(np.rint(mean_float), 0, 255).astype(np.uint8)
    write_png_unicode(session_dir / f"{name}.png", image)


def make_overlay(
    frame: np.ndarray,
    *,
    reference_xy: tuple[float, float],
    measured_xy: tuple[float, float] | None,
    status: str,
    elapsed_s: float,
    measured_fps: float,
    valid_percent: float,
    event_text: str = "",
) -> np.ndarray:
    canvas = cv2.cvtColor(frame.astype(np.uint8, copy=False), cv2.COLOR_GRAY2BGR)
    ref_x, ref_y = (int(round(value)) for value in reference_xy)
    cv2.drawMarker(canvas, (ref_x, ref_y), (255, 0, 0), cv2.MARKER_CROSS, 28, 2)
    if measured_xy is not None:
        point = tuple(int(round(value)) for value in measured_xy)
        cv2.circle(canvas, point, 7, (0, 255, 255), -1)

    color = (0, 255, 0) if status == "valid" else (0, 0, 255)
    lines = [
        f"{status.upper()} | {measured_fps:.1f} Hz | validos={valid_percent:.1f}%",
        f"tempo={elapsed_s:.1f} s | mount: NAO CONECTADO",
    ]
    if event_text:
        lines.append(f"evento: {event_text}")
    for index, line in enumerate(lines):
        cv2.putText(
            canvas,
            line,
            (18, 34 + (index * 31)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color if index == 0 else (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return canvas


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")[:80] or "event"


def save_event_images(
    session_dir: Path,
    event_index: int,
    kinds: list[str],
    raw_frame: np.ndarray,
    overlay: np.ndarray,
) -> tuple[Path, Path]:
    stem = f"evento_{event_index:04d}_{safe_name('-'.join(kinds))}"
    raw_path = session_dir / "eventos" / f"{stem}_raw.png"
    marked_path = session_dir / "eventos" / f"{stem}_marcado.png"
    write_png_unicode(raw_path, raw_frame)
    write_png_unicode(marked_path, overlay)
    return raw_path, marked_path


def event_images_allowed(saved_sets: int, saved_bytes: int) -> bool:
    """Decide se outro par de imagens cabe no orcamento da sessao."""
    return (
        int(saved_sets) < MAX_EVENT_IMAGE_SETS
        and int(saved_bytes) < int(MAX_EVENT_STORAGE_MB * 1024 * 1024)
    )


def storage_limit_reason(session_dir: Path, telemetry_path: Path) -> str | None:
    """Retorna a protecao de disco acionada, sem apagar nenhum resultado."""
    if telemetry_path.exists() and telemetry_path.stat().st_size >= MAX_TELEMETRY_MB * 1024 * 1024:
        return "telemetry_size_limit"
    free_bytes = shutil.disk_usage(session_dir).free
    if free_bytes < MIN_FREE_DISK_GB * 1024 * 1024 * 1024:
        return "low_disk_space"
    return None


# =============================================================================
# BLOCO 4 - AQUISICAO DA SESSAO (SEM MOUNT)
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mede a variacao temporal do beacon IDS sem mover o mount."
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=DEFAULT_DURATION_MIN,
        help="Duracao em minutos; use 0 para rodar ate Q/Esc/Ctrl+C.",
    )
    parser.add_argument(
        "--roi",
        type=int,
        default=DEFAULT_ROI_SIZE_PX,
        help="Lado solicitado para a ROI quadrada da IDS.",
    )
    parser.add_argument("--no-display", action="store_true", help="Nao abre a janela ao vivo.")
    return parser.parse_args()


def run_session(args: argparse.Namespace) -> Path:
    if args.minutes < 0:
        raise ValueError("--minutes deve ser maior ou igual a zero.")
    if args.roi < 128:
        raise ValueError("--roi deve ter pelo menos 128 pixels.")

    # A configuracao precisa ser aplicada antes de importar o backend e o detector.
    camera_config.apply_environment()
    from modulos.controle.alvo_alinhamento import roi_incluindo_alvo
    from modulos.controle.cameras.backend import direct_camera
    from modulos.visao import detector_ilhas as focus

    session_dir = create_session_dir()
    camera = direct_camera()
    camera_connected = False
    telemetry_file = None
    events_file = None
    stopped_by = "completed"
    setup_started_perf = time.perf_counter()
    measurement_started_perf: float | None = None
    started_wall = datetime.now().astimezone().isoformat(timespec="milliseconds")
    frame_index = 0
    valid_count = 0
    capture_error_count = 0
    consecutive_capture_errors = 0
    event_detected_count = 0
    event_image_sets_saved = 0
    event_storage_bytes = 0
    event_images_suppressed = 0
    event_kinds: Counter[str] = Counter()
    x_stats = OnlineStats()
    y_stats = OnlineStats()
    distance_stats = OnlineStats()
    fps_stats = OnlineStats()
    all_mean = RunningImageMean()
    valid_mean = RunningImageMean()
    normalized_valid_mean = RunningImageMean()
    event_detector = EventDetector()
    last_frame_time = 0.0
    last_flush_time = setup_started_perf
    last_disk_check_time = setup_started_perf
    last_display_time = 0.0
    last_raw: np.ndarray | None = None
    last_norm: np.ndarray | None = None
    reference_local = (0.0, 0.0)
    actual_roi = (0, 0, 0, 0)
    sensor_w = sensor_h = 0
    selection: dict[str, Any] = {}

    try:
        initial_storage_reason = storage_limit_reason(
            session_dir,
            session_dir / "telemetria.csv",
        )
        if initial_storage_reason is not None:
            raise RuntimeError(
                "Nao ha margem segura de disco para iniciar: "
                f"{initial_storage_reason}."
            )
        camera.connect()
        camera_connected = True
        sensor_w, sensor_h = camera.get_sensor_size()

        print("\nSelecione manualmente o beacon que sera caracterizado.")
        selection_frame = focus.capture_frame(float(camera_config.EXPOSURE_US) * 1e-6)
        selection = focus.escolher_ilha_manualmente(
            selection_frame,
            max_jump_px=MAX_IDENTITY_JUMP_PX,
        )
        target_display_x = float(selection["x_px"])
        target_display_y = float(selection["y_px"])
        raw_target_x, raw_target_y = displayed_to_raw_sensor(
            target_display_x,
            target_display_y,
            sensor_w,
            sensor_h,
            bool(camera_config.ROTATE_IMAGE_180),
        )
        requested_roi = min(int(args.roi), sensor_w, sensor_h)
        start_x, start_y, _, _ = roi_incluindo_alvo(
            sensor_w,
            sensor_h,
            requested_roi,
            requested_roi,
            raw_target_x,
            raw_target_y,
        )
        actual_roi = camera.set_roi(
            requested_roi,
            requested_roi,
            start_x,
            start_y,
        )
        reference_local = target_in_actual_roi(
            target_display_x,
            target_display_y,
            actual_roi,
            sensor_w,
            sensor_h,
            bool(camera_config.ROTATE_IMAGE_180),
        )
        if not focus.initialize_focus_lock(
            selection["signature"],
            expected_x=reference_local[0],
            expected_y=reference_local[1],
            freeze_reference=True,
            max_jump_px=MAX_IDENTITY_JUMP_PX,
            threshold_percent=float(selection["selection_threshold_percent"]),
        ):
            raise RuntimeError("Nao foi possivel transferir a trava do beacon para a ROI.")

        metadata = {
            "program": "caracterizar_beacon_ids",
            "started_at": started_wall,
            "mount_movido": False,
            "camera": "IDS",
            "camera_settings": {
                "exposure_us": camera_config.EXPOSURE_US,
                "frame_rate_fps": camera_config.FRAME_RATE_FPS,
                "analog_gain": camera_config.ANALOG_GAIN,
                "digital_gain": camera_config.DIGITAL_GAIN,
                "rotate_image_180": camera_config.ROTATE_IMAGE_180,
            },
            "duration_minutes_requested": args.minutes,
            "sensor_size": [sensor_w, sensor_h],
            "roi_requested_px": requested_roi,
            "roi_actual": list(actual_roi),
            "reference_local_px": list(reference_local),
            "selection": selection,
            "event_thresholds": {
                "position_event_px": POSITION_EVENT_PX,
                "size_ratio": [SIZE_RATIO_LOW, SIZE_RATIO_HIGH],
                "intensity_ratio": [INTENSITY_RATIO_LOW, INTENSITY_RATIO_HIGH],
                "baseline_frames": EVENT_BASELINE_FRAMES,
                "cooldown_s": EVENT_COOLDOWN_S,
            },
            "storage_limits": {
                "max_telemetry_mb": MAX_TELEMETRY_MB,
                "max_event_image_sets": MAX_EVENT_IMAGE_SETS,
                "max_event_storage_mb": MAX_EVENT_STORAGE_MB,
                "min_free_disk_gb": MIN_FREE_DISK_GB,
            },
        }
        json_write(session_dir / "metadados.json", metadata)
        write_png_unicode(session_dir / "frame_selecao.png", selection_frame)

        telemetry_file = (session_dir / "telemetria.csv").open(
            "w", newline="", encoding="utf-8"
        )
        telemetry_writer = csv.DictWriter(telemetry_file, fieldnames=CSV_FIELDS)
        telemetry_writer.writeheader()
        events_file = (session_dir / "eventos.csv").open("w", newline="", encoding="utf-8")
        events_writer = csv.DictWriter(
            events_file,
            fieldnames=[
                "timestamp_iso",
                "elapsed_s",
                "frame_index",
                "event",
                "raw_image",
                "marked_image",
                "uses_last_available_frame",
                "images_saved",
            ],
        )
        events_writer.writeheader()

        def register_event(
            kinds: list[str],
            raw_image: np.ndarray | None,
            marked_image: np.ndarray | None,
            elapsed_s: float,
            *,
            uses_last_frame: bool,
        ) -> None:
            """Registra todo evento, mas salva imagens somente dentro do limite."""
            nonlocal event_detected_count
            nonlocal event_image_sets_saved
            nonlocal event_storage_bytes
            nonlocal event_images_suppressed

            if not kinds:
                return
            event_detected_count += 1
            event_kinds.update(kinds)
            raw_path = None
            marked_path = None
            images_saved = 0
            if (
                raw_image is not None
                and marked_image is not None
                and event_images_allowed(event_image_sets_saved, event_storage_bytes)
            ):
                raw_path, marked_path = save_event_images(
                    session_dir,
                    event_image_sets_saved + 1,
                    kinds,
                    raw_image,
                    marked_image,
                )
                new_bytes = raw_path.stat().st_size + marked_path.stat().st_size
                max_event_bytes = int(MAX_EVENT_STORAGE_MB * 1024 * 1024)
                if event_storage_bytes + new_bytes <= max_event_bytes:
                    event_image_sets_saved += 1
                    event_storage_bytes += new_bytes
                    images_saved = 1
                else:
                    # Os dois arquivos acabaram de ser criados por esta funcao
                    # e ainda nao foram publicados no indice de eventos.
                    raw_path.unlink(missing_ok=True)
                    marked_path.unlink(missing_ok=True)
                    raw_path = None
                    marked_path = None
                    event_images_suppressed += 1
            else:
                event_images_suppressed += 1

            events_writer.writerow(
                {
                    "timestamp_iso": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "elapsed_s": f"{elapsed_s:.6f}",
                    "frame_index": frame_index,
                    "event": ";".join(kinds),
                    "raw_image": "" if raw_path is None else raw_path.relative_to(session_dir),
                    "marked_image": "" if marked_path is None else marked_path.relative_to(session_dir),
                    "uses_last_available_frame": int(uses_last_frame),
                    "images_saved": images_saved,
                }
            )
            events_file.flush()

        print(f"\nSessao salva em: {session_dir}")
        print(
            f"ROI IDS: {actual_roi[0]}x{actual_roi[1]} em "
            f"({actual_roi[2]}, {actual_roi[3]})"
        )
        print("O mount NAO sera conectado. Pressione Q, Esc ou Ctrl+C para terminar.")

        # A duracao solicitada mede somente a aquisicao, sem incluir conexao e selecao.
        measurement_started_perf = time.perf_counter()
        last_flush_time = measurement_started_perf
        last_disk_check_time = measurement_started_perf

        while True:
            loop_started = time.perf_counter()
            elapsed = loop_started - measurement_started_perf
            if args.minutes > 0 and elapsed >= args.minutes * 60.0:
                stopped_by = "duration_reached"
                break

            frame_index += 1
            try:
                frame = focus.capture_frame(float(camera_config.EXPOSURE_US) * 1e-6)
                capture_dt = time.perf_counter() - loop_started
                consecutive_capture_errors = 0
            except Exception as exc:
                capture_error_count += 1
                consecutive_capture_errors += 1
                now = time.perf_counter()
                elapsed = now - measurement_started_perf
                event = ["capture_error"] if event_detector._allow("capture_error", now) else []
                row = {field: "" for field in CSV_FIELDS}
                row.update(
                    {
                        "timestamp_iso": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        "elapsed_s": f"{elapsed:.6f}",
                        "frame_index": frame_index,
                        "status": "capture_error",
                        "valid": 0,
                        "capture_dt_s": f"{now - loop_started:.6f}",
                        "event": ";".join(event),
                    }
                )
                telemetry_writer.writerow(row)
                telemetry_file.flush()
                print(f"Aviso: falha de captura {consecutive_capture_errors}/5: {exc}")
                if event:
                    overlay = None
                    if last_norm is not None:
                        overlay = make_overlay(
                            last_norm,
                            reference_xy=reference_local,
                            measured_xy=None,
                            status="capture_error",
                            elapsed_s=elapsed,
                            measured_fps=0.0,
                            valid_percent=100.0 * valid_count / max(frame_index, 1),
                            event_text=";".join(event),
                        )
                    register_event(
                        event,
                        last_raw,
                        overlay,
                        elapsed,
                        uses_last_frame=True,
                    )
                if consecutive_capture_errors >= 5:
                    stopped_by = "five_consecutive_capture_errors"
                    break
                continue

            now = time.perf_counter()
            elapsed = now - measurement_started_perf
            measured_fps = 0.0 if last_frame_time <= 0 else 1.0 / max(now - last_frame_time, 1e-6)
            last_frame_time = now
            if measured_fps > 0:
                fps_stats.add(measured_fps)

            raw_frame = np.asarray(focus.LAST_RAW_FRAME).copy()
            last_raw = raw_frame
            last_norm = frame.copy()
            all_mean.add(raw_frame)
            cm = focus.centro_massa(frame)
            debug = focus.get_focus_debug()
            selected = debug.get("selected") or {}
            touches_border = bool(selected.get("toca_borda", False))
            valid = cm is not None and not touches_border
            status = "valid" if valid else "roi_border" if touches_border else "no_signal"

            x_roi = float(cm[0]) if cm is not None else None
            y_roi = float(cm[1]) if cm is not None else None
            dx = x_roi - reference_local[0] if x_roi is not None else None
            dy = y_roi - reference_local[1] if y_roi is not None else None
            distance = float(np.hypot(dx, dy)) if dx is not None and dy is not None else None
            x_sensor = y_sensor = None
            if x_roi is not None and y_roi is not None:
                x_sensor, y_sensor = roi_to_displayed_sensor(
                    x_roi,
                    y_roi,
                    actual_roi,
                    sensor_w,
                    sensor_h,
                    bool(camera_config.ROTATE_IMAGE_180),
                )

            capture_stats = dict(focus.LAST_CAPTURE_STATS)
            shape = None
            if x_roi is not None and y_roi is not None:
                shape = measure_spot_shape(
                    raw_frame,
                    x_roi,
                    y_roi,
                    float(capture_stats.get("pedestal", 0.0)),
                    float(selection["selection_threshold_percent"]),
                )
            shape = shape or {}

            if valid:
                valid_count += 1
                valid_mean.add(raw_frame)
                normalized_valid_mean.add(frame)
                x_stats.add(x_sensor)
                y_stats.add(y_sensor)
                distance_stats.add(distance)

            events = event_detector.update(
                now,
                valid=valid,
                touches_border=touches_border,
                x_px=x_roi,
                y_px=y_roi,
                raw_total=selected.get("raw_total"),
                sigma_major_px=shape.get("sigma_major_px"),
            )
            valid_percent = 100.0 * valid_count / max(frame_index, 1)
            overlay = make_overlay(
                frame,
                reference_xy=reference_local,
                measured_xy=None if cm is None else (x_roi, y_roi),
                status=status,
                elapsed_s=elapsed,
                measured_fps=measured_fps,
                valid_percent=valid_percent,
                event_text=";".join(events),
            )

            if events:
                register_event(
                    events,
                    raw_frame,
                    overlay,
                    elapsed,
                    uses_last_frame=False,
                )

            row = {field: "" for field in CSV_FIELDS}
            values = {
                "timestamp_iso": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "elapsed_s": elapsed,
                "frame_index": frame_index,
                "status": status,
                "valid": int(valid),
                "capture_dt_s": capture_dt,
                "measured_fps": measured_fps,
                "x_roi_px": x_roi,
                "y_roi_px": y_roi,
                "x_sensor_px": x_sensor,
                "y_sensor_px": y_sensor,
                "dx_px": dx,
                "dy_px": dy,
                "distance_px": distance,
                "raw_peak": selected.get("raw_peak"),
                "raw_total": selected.get("raw_total"),
                "area_px": selected.get("area"),
                "bbox_w_px": selected.get("bbox_w"),
                "bbox_h_px": selected.get("bbox_h"),
                "compactness": selected.get("compactness"),
                "similarity_primary": selected.get("similarity_primary"),
                "sigma_x_px": shape.get("sigma_x_px"),
                "sigma_y_px": shape.get("sigma_y_px"),
                "sigma_major_px": shape.get("sigma_major_px"),
                "sigma_minor_px": shape.get("sigma_minor_px"),
                "angle_deg": shape.get("angle_deg"),
                "candidate_count": debug.get("candidate_count"),
                "touches_border": int(touches_border),
                "raw_min": capture_stats.get("raw_min"),
                "raw_max": capture_stats.get("raw_max"),
                "raw_median": capture_stats.get("raw_median"),
                "raw_std": capture_stats.get("raw_std"),
                "pedestal": capture_stats.get("pedestal"),
                "event": ";".join(events),
            }
            for key, value in values.items():
                if value is None:
                    row[key] = ""
                elif isinstance(value, float):
                    row[key] = f"{value:.8f}"
                else:
                    row[key] = value
            telemetry_writer.writerow(row)
            if (now - last_flush_time) >= 1.0:
                telemetry_file.flush()
                last_flush_time = now

            if (now - last_disk_check_time) >= DISK_CHECK_INTERVAL_S:
                telemetry_file.flush()
                limit_reason = storage_limit_reason(
                    session_dir,
                    session_dir / "telemetria.csv",
                )
                last_disk_check_time = now
                if limit_reason is not None:
                    stopped_by = limit_reason
                    print(
                        "\nCaracterizacao encerrada pela protecao de disco: "
                        f"{limit_reason}."
                    )
                    break

            if not args.no_display and (
                last_display_time <= 0 or (now - last_display_time) >= 1.0 / DISPLAY_HZ
            ):
                max_display_w, max_display_h = 1100, 850
                scale = min(1.0, max_display_w / overlay.shape[1], max_display_h / overlay.shape[0])
                display = overlay
                if scale < 1.0:
                    display = cv2.resize(
                        overlay,
                        (int(overlay.shape[1] * scale), int(overlay.shape[0] * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                cv2.imshow("Caracterizacao do beacon IDS - SEM MOUNT", display)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    stopped_by = "user_key"
                    break
                last_display_time = now

    except KeyboardInterrupt:
        stopped_by = "ctrl_c"
        print("\nCaracterizacao interrompida pelo usuario.")
    except Exception:
        stopped_by = "error"
        raise
    finally:
        ended_perf = time.perf_counter()
        total_duration_s = ended_perf - setup_started_perf
        duration_s = (
            0.0
            if measurement_started_perf is None
            else ended_perf - measurement_started_perf
        )
        if telemetry_file is not None:
            telemetry_file.flush()
            telemetry_file.close()
        if events_file is not None:
            events_file.flush()
            events_file.close()
        cv2.destroyAllWindows()
        save_mean(session_dir, "media_todos_frames", all_mean)
        save_mean(session_dir, "media_frames_validos", valid_mean)
        save_mean(
            session_dir,
            "media_normalizada_frames_validos",
            normalized_valid_mean,
        )
        summary = {
            "started_at": started_wall,
            "ended_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "stopped_by": stopped_by,
            "duration_s": duration_s,
            "total_duration_including_setup_s": total_duration_s,
            "frames_attempted": frame_index,
            "valid_frames": valid_count,
            "valid_percent": 100.0 * valid_count / max(frame_index, 1),
            "capture_errors": capture_error_count,
            "events_detected": event_detected_count,
            "event_image_sets_saved": event_image_sets_saved,
            "event_images_suppressed_by_limit": event_images_suppressed,
            "event_storage_mb": event_storage_bytes / (1024 * 1024),
            "events_by_kind": dict(event_kinds),
            "x_sensor_px": x_stats.as_dict(),
            "y_sensor_px": y_stats.as_dict(),
            "distance_from_reference_px": distance_stats.as_dict(),
            "instantaneous_fps": fps_stats.as_dict(),
            "mean_all_frame_count": all_mean.count,
            "mean_valid_frame_count": valid_mean.count,
            "mean_normalized_valid_frame_count": normalized_valid_mean.count,
            "mount_movido": False,
        }
        json_write(session_dir / "resumo.json", summary)
        if camera_connected:
            try:
                camera.reset_roi()
            except Exception as exc:
                print(f"Aviso: nao foi possivel restaurar o sensor completo: {exc}")
            camera.disconnect()

    print(f"\nCaracterizacao finalizada: {session_dir}")
    print(
        f"Duracao={duration_s:.1f}s | frames={frame_index} | "
        f"validos={valid_count} | eventos={event_detected_count} | "
        f"imagens de eventos={event_image_sets_saved}"
    )
    return session_dir


def main() -> None:
    args = parse_args()
    run_session(args)


if __name__ == "__main__":
    main()
