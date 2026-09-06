"""Media temporal do tracker.

Recebe somente frames cuja identidade de ilha ja foi validada. A soma temporal
reduz o seeing rapido; o controle reage ao centro da mancha media, nao a cada
oscilacao instantanea.
"""

from collections import deque

import numpy as np

from modulos.visao.centroide import centroide_em_abertura
from modulos.configuracoes.tracker import (
    TEMPORAL_APERTURE_RADIUS_PX,
    TEMPORAL_INPUT_JUMP_PX,
    TEMPORAL_MEAN_THRESHOLD_PERCENT,
    TEMPORAL_MIN_VALID_FRAMES,
    TEMPORAL_WARMUP_SECONDS,
    TEMPORAL_WINDOW_SECONDS,
)


class TemporalFrameEstimator:
    """Soma frames normalizados e mede o CM robusto numa janela de tempo."""

    def __init__(
        self,
        window_seconds=TEMPORAL_WINDOW_SECONDS,
        warmup_seconds=TEMPORAL_WARMUP_SECONDS,
        min_frames=TEMPORAL_MIN_VALID_FRAMES,
        aperture_radius_px=TEMPORAL_APERTURE_RADIUS_PX,
        max_input_jump_px=TEMPORAL_INPUT_JUMP_PX,
        threshold_percent=TEMPORAL_MEAN_THRESHOLD_PERCENT,
    ):
        self.window_seconds = float(window_seconds)
        self.warmup_seconds = float(warmup_seconds)
        self.min_frames = int(min_frames)
        self.aperture_radius_px = int(aperture_radius_px)
        self.max_input_jump_px = float(max_input_jump_px)
        self.threshold_percent = float(threshold_percent)
        self._entries = deque()
        self._weighted_sum = None
        self._weight_sum = 0.0
        self.rejected_inputs = 0

    def clear(self) -> None:
        self._entries.clear()
        self._weighted_sum = None
        self._weight_sum = 0.0

    @property
    def frame_count(self) -> int:
        return len(self._entries)

    @property
    def window_span_s(self) -> float:
        if len(self._entries) < 2:
            return 0.0
        return float(self._entries[-1][0] - self._entries[0][0])

    def _prune(self, now: float) -> None:
        cutoff = float(now) - self.window_seconds
        while self._entries and self._entries[0][0] < cutoff:
            _, old_frame, _, _, old_weight = self._entries.popleft()
            self._weighted_sum -= old_frame.astype(np.float32) * old_weight
            self._weight_sum -= old_weight
        if not self._entries:
            self._weighted_sum = None
            self._weight_sum = 0.0

    def add(
        self,
        now: float,
        frame: np.ndarray,
        x_px: float,
        y_px: float,
        quality: float = 1.0,
    ) -> bool:
        """Adiciona um frame; retorna False quando o CM e um salto isolado."""
        self._prune(now)
        x_px = float(x_px)
        y_px = float(y_px)
        if not np.isfinite(x_px) or not np.isfinite(y_px):
            return False

        if self._entries:
            centers = np.asarray([(item[2], item[3]) for item in self._entries], dtype=float)
            center = np.median(centers, axis=0)
            radial = np.hypot(centers[:, 0] - center[0], centers[:, 1] - center[1])
            radial_mad = float(np.median(np.abs(radial - np.median(radial))))
            allowed_jump = self.max_input_jump_px + min(8.0, 4.0 * radial_mad)
            last_x = float(self._entries[-1][2])
            last_y = float(self._entries[-1][3])
            if np.hypot(x_px - last_x, y_px - last_y) > allowed_jump:
                self.rejected_inputs += 1
                return False

        frame_u8 = np.asarray(frame, dtype=np.uint8)
        if frame_u8.ndim != 2:
            raise ValueError("A media temporal espera frames monocromaticos 2D.")
        if self._weighted_sum is not None and frame_u8.shape != self._weighted_sum.shape:
            raise ValueError("O tamanho do frame mudou durante a media temporal.")

        weight = float(np.clip(quality, 0.25, 1.0))
        stored = np.ascontiguousarray(frame_u8).copy()
        if self._weighted_sum is None:
            self._weighted_sum = np.zeros(stored.shape, dtype=np.float32)
        self._weighted_sum += stored.astype(np.float32) * weight
        self._weight_sum += weight
        self._entries.append((float(now), stored, x_px, y_px, weight))
        self._prune(now)
        return True

    def estimate(self, now: float) -> dict | None:
        self._prune(now)
        if len(self._entries) < self.min_frames or self._weight_sum <= 0.0:
            return None
        span_s = float(self._entries[-1][0] - self._entries[0][0])
        if span_s < self.warmup_seconds:
            return None

        centers = np.asarray([(item[2], item[3]) for item in self._entries], dtype=float)
        expected_x, expected_y = np.median(centers, axis=0)
        mean_frame = self._weighted_sum / self._weight_sum
        center = centroide_em_abertura(
            mean_frame,
            expected_x,
            expected_y,
            radius_px=self.aperture_radius_px,
            threshold_percent=self.threshold_percent,
        )
        if center is None:
            return None

        return {
            "x_px": center[0],
            "y_px": center[1],
            # Usada apenas pelo display. O controle continua consumindo somente
            # o centro calculado acima.
            "mean_frame": mean_frame,
            "frame_count": self.frame_count,
            "window_span_s": span_s,
            "centroid_std_x_px": float(np.std(centers[:, 0])),
            "centroid_std_y_px": float(np.std(centers[:, 1])),
        }


def measurement_quality(selected_debug: dict) -> float:
    """Converte a similaridade da assinatura em peso da media temporal."""
    similarity = selected_debug.get("similarity_primary")
    if similarity is None or not np.isfinite(similarity):
        return 1.0
    return float(np.clip(similarity, 0.25, 1.0))
