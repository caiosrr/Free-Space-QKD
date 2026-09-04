"""Autoexposicao lenta e limitada para o beacon travado."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from modulos.configuracoes.tracker import (
    AUTO_EXPOSURE_BACKGROUND_HIGH,
    AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT,
    AUTO_EXPOSURE_BACKGROUND_PERCENTILE,
    AUTO_EXPOSURE_ENABLED,
    AUTO_EXPOSURE_HISTORY_SECONDS,
    AUTO_EXPOSURE_MAX_STEP_FRACTION,
    AUTO_EXPOSURE_MAX_US,
    AUTO_EXPOSURE_MIN_SAMPLES,
    AUTO_EXPOSURE_MIN_US,
    AUTO_EXPOSURE_SATURATION_FRACTION,
    AUTO_EXPOSURE_SATURATION_LEVEL,
    AUTO_EXPOSURE_TARGET_CENTER,
    AUTO_EXPOSURE_TARGET_HIGH,
    AUTO_EXPOSURE_TARGET_LOW,
    AUTO_EXPOSURE_UPDATE_SECONDS,
)


SAFETY_UPDATE_SECONDS = 1.0


@dataclass(frozen=True)
class ExposureDecision:
    exposure_us: float
    changed: bool
    reason: str
    peak_median: float | None
    background_percentile: float
    saturation_fraction: float


def border_background_metrics(
    frame: np.ndarray,
    *,
    percentile: float = AUTO_EXPOSURE_BACKGROUND_PERCENTILE,
    saturation_level: int = AUTO_EXPOSURE_SATURATION_LEVEL,
) -> tuple[float, float]:
    """Mede o fundo nas margens da ROI, longe do beacon central."""
    image = np.asarray(frame)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("A autoexposicao requer um frame monocromatico nao vazio.")
    height, width = image.shape
    margin = max(4, min(height, width) // 8)
    border = np.concatenate(
        (
            image[:margin, :].ravel(),
            image[-margin:, :].ravel(),
            image[margin:-margin, :margin].ravel(),
            image[margin:-margin, -margin:].ravel(),
        )
    )
    background = float(np.percentile(border, percentile))
    saturated = float(np.mean(border >= saturation_level))
    return background, saturated


class AutoExposureController:
    """Mantem contraste util sem reagir a oclusoes ou cintilacao breve."""

    def __init__(
        self,
        initial_exposure_us: float,
        *,
        enabled: bool = AUTO_EXPOSURE_ENABLED,
        minimum_us: float = AUTO_EXPOSURE_MIN_US,
        maximum_us: float = AUTO_EXPOSURE_MAX_US,
        target_low: float = AUTO_EXPOSURE_TARGET_LOW,
        target_high: float = AUTO_EXPOSURE_TARGET_HIGH,
        target_center: float = AUTO_EXPOSURE_TARGET_CENTER,
        update_seconds: float = AUTO_EXPOSURE_UPDATE_SECONDS,
        history_seconds: float = AUTO_EXPOSURE_HISTORY_SECONDS,
        min_samples: int = AUTO_EXPOSURE_MIN_SAMPLES,
        max_step_fraction: float = AUTO_EXPOSURE_MAX_STEP_FRACTION,
        background_high: float = AUTO_EXPOSURE_BACKGROUND_HIGH,
        background_increase_limit: float = AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT,
        saturation_fraction: float = AUTO_EXPOSURE_SATURATION_FRACTION,
        started_at: float = 0.0,
    ):
        self.enabled = bool(enabled)
        self.minimum_us = float(minimum_us)
        self.maximum_us = float(maximum_us)
        self.target_low = float(target_low)
        self.target_high = float(target_high)
        self.target_center = float(target_center)
        self.update_seconds = float(update_seconds)
        self.history_seconds = float(history_seconds)
        self.min_samples = int(min_samples)
        self.max_step_fraction = float(max_step_fraction)
        self.background_high = float(background_high)
        self.background_increase_limit = float(background_increase_limit)
        self.saturation_fraction_limit = float(saturation_fraction)
        self.current_exposure_us = float(
            np.clip(initial_exposure_us, self.minimum_us, self.maximum_us)
        )
        self._last_evaluation_t = float(started_at)
        self._last_safety_t = float(started_at)
        self._peak_samples = deque()
        self._background_samples = deque()

    def _purge(self, now: float) -> None:
        cutoff = now - self.history_seconds
        while self._peak_samples and self._peak_samples[0][0] < cutoff:
            self._peak_samples.popleft()
        while self._background_samples and self._background_samples[0][0] < cutoff:
            self._background_samples.popleft()

    def _history_ready(self, samples: deque) -> bool:
        if len(samples) < self.min_samples:
            return False
        return (samples[-1][0] - samples[0][0]) >= min(
            1.0, self.history_seconds * 0.5
        )

    def _decision(
        self,
        *,
        exposure_us: float | None = None,
        changed: bool = False,
        reason: str,
        peak_median: float | None,
        background: float,
        saturation: float,
    ) -> ExposureDecision:
        return ExposureDecision(
            self.current_exposure_us if exposure_us is None else exposure_us,
            changed,
            reason,
            peak_median,
            background,
            saturation,
        )

    def _apply_factor(
        self,
        factor: float,
        *,
        reason: str,
        peak_median: float | None,
        background: float,
        saturation: float,
    ) -> ExposureDecision:
        factor = float(
            np.clip(
                factor,
                1.0 - self.max_step_fraction,
                1.0 + self.max_step_fraction,
            )
        )
        new_exposure = float(
            np.clip(
                self.current_exposure_us * factor,
                self.minimum_us,
                self.maximum_us,
            )
        )
        new_exposure = float(round(new_exposure))
        changed = abs(new_exposure - self.current_exposure_us) >= 1.0
        self.current_exposure_us = new_exposure
        if changed:
            self._peak_samples.clear()
            self._background_samples.clear()
        return self._decision(
            exposure_us=new_exposure,
            changed=changed,
            reason=reason,
            peak_median=peak_median,
            background=background,
            saturation=saturation,
        )

    def observe(
        self,
        now: float,
        frame: np.ndarray,
        *,
        target_peak: float | None,
        trusted_target: bool,
    ) -> ExposureDecision:
        now = float(now)
        background, saturation = border_background_metrics(frame)
        self._background_samples.append((now, background, saturation))
        if trusted_target and target_peak is not None and np.isfinite(target_peak):
            self._peak_samples.append((now, float(target_peak)))
        self._purge(now)

        background_ready = self._history_ready(self._background_samples)
        background_median = float(
            np.median([item[1] for item in self._background_samples])
        )
        saturation_median = float(
            np.median([item[2] for item in self._background_samples])
        )
        peak_ready = self._history_ready(self._peak_samples)
        peak_median = (
            float(np.median([item[1] for item in self._peak_samples]))
            if self._peak_samples
            else None
        )

        if not self.enabled:
            return self._decision(
                reason="desativada",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )

        unsafe_background = background_ready and (
            background_median >= self.background_high
            or saturation_median >= self.saturation_fraction_limit
        )
        if unsafe_background and now - self._last_safety_t >= SAFETY_UPDATE_SECONDS:
            self._last_safety_t = now
            return self._apply_factor(
                1.0 - self.max_step_fraction,
                reason="reducao_fundo_saturando",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )

        if now - self._last_evaluation_t < self.update_seconds:
            return self._decision(
                reason="aguardando_intervalo",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )
        self._last_evaluation_t = now

        if not trusted_target or not peak_ready or peak_median is None:
            return self._decision(
                reason="congelada_sem_alvo_confiavel",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )
        if self.target_low <= peak_median <= self.target_high:
            return self._decision(
                reason="faixa_ideal",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )
        if peak_median < self.target_low:
            if background_median >= self.background_increase_limit:
                return self._decision(
                    reason="aumento_bloqueado_pelo_fundo",
                    peak_median=peak_median,
                    background=background_median,
                    saturation=saturation_median,
                )
            return self._apply_factor(
                self.target_center / max(peak_median, 1.0),
                reason="aumento_sinal_baixo",
                peak_median=peak_median,
                background=background_median,
                saturation=saturation_median,
            )
        return self._apply_factor(
            self.target_center / peak_median,
            reason="reducao_sinal_alto",
            peak_median=peak_median,
            background=background_median,
            saturation=saturation_median,
        )
