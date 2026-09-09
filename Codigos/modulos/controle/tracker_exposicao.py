"""Autoexposicao lenta orientada ao contraste do beacon travado."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from modulos.configuracoes.tracker import (
    AUTO_EXPOSURE_BACKGROUND_HIGH,
    AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT,
    AUTO_EXPOSURE_BACKGROUND_PERCENTILE,
    AUTO_EXPOSURE_CNR_HIGH,
    AUTO_EXPOSURE_CNR_LOW,
    AUTO_EXPOSURE_ENABLED,
    AUTO_EXPOSURE_HISTORY_SECONDS,
    AUTO_EXPOSURE_MAX_STEP_FRACTION,
    AUTO_EXPOSURE_MAX_US,
    AUTO_EXPOSURE_MIN_SAMPLES,
    AUTO_EXPOSURE_MIN_TRUSTED_FRACTION,
    AUTO_EXPOSURE_LOSS_SEARCH_INTERVAL_SECONDS,
    AUTO_EXPOSURE_LOSS_SEARCH_SECONDS,
    AUTO_EXPOSURE_LOSS_SEARCH_STEP_FRACTION,
    AUTO_EXPOSURE_MIN_US,
    AUTO_EXPOSURE_REDUCTION_STEP_FRACTION,
    AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS,
    AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS,
    AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS,
    AUTO_EXPOSURE_SATURATION_FRACTION,
    AUTO_EXPOSURE_SATURATION_LEVEL,
    AUTO_EXPOSURE_UPDATE_SECONDS,
)


@dataclass(frozen=True)
class ExposureMetrics:
    """Qualidade radiometrica medida na mesma escala bruta da camera."""

    target_level: float | None
    local_background: float | None
    local_noise: float | None
    cnr: float | None
    background_percentile: float
    saturation_fraction: float
    target_saturation_fraction: float


@dataclass(frozen=True)
class ExposureDecision:
    exposure_us: float
    changed: bool
    reason: str
    peak_median: float | None
    background_percentile: float
    saturation_fraction: float
    local_background_median: float | None
    local_noise_median: float | None
    cnr_median: float | None
    trusted_fraction: float
    target_saturation_fraction: float
    rollback: bool = False


def border_background_metrics(
    frame: np.ndarray,
    *,
    percentile: float = AUTO_EXPOSURE_BACKGROUND_PERCENTILE,
    saturation_level: int = AUTO_EXPOSURE_SATURATION_LEVEL,
) -> tuple[float, float]:
    """Mede a cena nas margens da ROI, longe do beacon central."""
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


def exposure_metrics(
    frame: np.ndarray,
    *,
    target_center: tuple[float, float] | None,
    target_diameter_px: float | None = None,
    percentile: float = AUTO_EXPOSURE_BACKGROUND_PERCENTILE,
    saturation_level: int = AUTO_EXPOSURE_SATURATION_LEVEL,
) -> ExposureMetrics:
    """Calcula CNR local e ocupacao da faixa dinamica fora do beacon."""
    image = np.asarray(frame, dtype=np.float32)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("A autoexposicao requer um frame monocromatico nao vazio.")

    yy, xx = np.indices(image.shape, dtype=np.float32)
    target_mask = None
    annulus = None
    if target_center is not None:
        x_px, y_px = (float(value) for value in target_center)
        if np.isfinite(x_px) and np.isfinite(y_px):
            diameter = float(target_diameter_px or 12.0)
            diameter = diameter if np.isfinite(diameter) and diameter > 0 else 12.0
            radius = float(np.clip(0.65 * diameter, 4.0, 32.0))
            distance_sq = (xx - x_px) ** 2 + (yy - y_px) ** 2
            target_mask = distance_sq <= radius**2
            annulus_inner = radius + 3.0
            annulus_outer = min(radius + 19.0, 48.0)
            annulus = (
                (distance_sq >= annulus_inner**2)
                & (distance_sq <= annulus_outer**2)
            )

    if target_mask is None or np.count_nonzero(target_mask) < 16:
        scene_pixels = image.ravel()
        background = float(np.percentile(scene_pixels, percentile))
        saturated = float(np.mean(scene_pixels >= saturation_level))
        return ExposureMetrics(None, None, None, None, background, saturated, 0.0)

    scene_pixels = image[~target_mask]
    if scene_pixels.size < 32:
        scene_pixels = image.ravel()
    background_percentile = float(np.percentile(scene_pixels, percentile))
    saturation_fraction = float(np.mean(scene_pixels >= saturation_level))

    local_pixels = image[annulus]
    if local_pixels.size < 32:
        local_pixels = scene_pixels
    local_background = float(np.median(local_pixels))
    median_absolute_deviation = float(
        np.median(np.abs(local_pixels - local_background))
    )
    robust_noise = 1.4826 * median_absolute_deviation
    if robust_noise < 1.0:
        robust_noise = max(float(np.std(local_pixels)), 1.0)

    target_pixels = image[target_mask]
    target_level = float(np.percentile(target_pixels, 98.0))
    target_saturation = float(np.mean(target_pixels >= saturation_level))
    cnr = max(0.0, (target_level - local_background) / robust_noise)
    return ExposureMetrics(
        target_level,
        local_background,
        robust_noise,
        cnr,
        background_percentile,
        saturation_fraction,
        target_saturation,
    )


class AutoExposureController:
    """Busca a menor exposicao com CNR estavel sem reagir a oclusoes."""

    def __init__(
        self,
        initial_exposure_us: float,
        *,
        enabled: bool = AUTO_EXPOSURE_ENABLED,
        minimum_us: float = AUTO_EXPOSURE_MIN_US,
        maximum_us: float = AUTO_EXPOSURE_MAX_US,
        cnr_low: float = AUTO_EXPOSURE_CNR_LOW,
        cnr_high: float = AUTO_EXPOSURE_CNR_HIGH,
        min_trusted_fraction: float = AUTO_EXPOSURE_MIN_TRUSTED_FRACTION,
        update_seconds: float = AUTO_EXPOSURE_UPDATE_SECONDS,
        history_seconds: float = AUTO_EXPOSURE_HISTORY_SECONDS,
        min_samples: int = AUTO_EXPOSURE_MIN_SAMPLES,
        max_step_fraction: float = AUTO_EXPOSURE_MAX_STEP_FRACTION,
        reduction_step_fraction: float = AUTO_EXPOSURE_REDUCTION_STEP_FRACTION,
        background_high: float = AUTO_EXPOSURE_BACKGROUND_HIGH,
        background_increase_limit: float = AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT,
        saturation_fraction: float = AUTO_EXPOSURE_SATURATION_FRACTION,
        rollback_window_s: float = AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS,
        rollback_loss_s: float = AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS,
        safety_update_s: float = AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS,
        loss_search_s: float = AUTO_EXPOSURE_LOSS_SEARCH_SECONDS,
        loss_search_step: float = AUTO_EXPOSURE_LOSS_SEARCH_STEP_FRACTION,
        loss_search_interval_s: float = AUTO_EXPOSURE_LOSS_SEARCH_INTERVAL_SECONDS,
        started_at: float = 0.0,
    ):
        self.enabled = bool(enabled)
        self.minimum_us = float(minimum_us)
        self.maximum_us = float(maximum_us)
        self.cnr_low = float(cnr_low)
        self.cnr_high = float(cnr_high)
        self.min_trusted_fraction = float(min_trusted_fraction)
        self.update_seconds = float(update_seconds)
        self.history_seconds = float(history_seconds)
        self.min_samples = int(min_samples)
        self.max_step_fraction = float(max_step_fraction)
        self.reduction_step_fraction = float(reduction_step_fraction)
        self.background_high = float(background_high)
        self.background_increase_limit = float(background_increase_limit)
        self.saturation_fraction_limit = float(saturation_fraction)
        self.rollback_window_s = float(rollback_window_s)
        self.rollback_loss_s = float(rollback_loss_s)
        self.safety_update_s = float(safety_update_s)
        self.loss_search_s = float(loss_search_s)
        self.loss_search_step = float(loss_search_step)
        self.loss_search_interval_s = float(loss_search_interval_s)
        self.current_exposure_us = float(
            np.clip(initial_exposure_us, self.minimum_us, self.maximum_us)
        )
        self._last_evaluation_t = float(started_at)
        self._last_safety_t = float(started_at)
        self._quality_samples = deque()
        self._scene_samples = deque()
        self._status_samples = deque()
        self._last_change_t = None
        self._last_change_previous_exposure = None
        self._last_change_can_rollback = False
        self._untrusted_since = None
        self._untrusted_safety_used = False
        self._last_loss_search_t = float(started_at)
        self._loss_search_active = False

    def _purge(self, now: float) -> None:
        cutoff = now - self.history_seconds
        for samples in (
            self._quality_samples,
            self._scene_samples,
            self._status_samples,
        ):
            while samples and samples[0][0] < cutoff:
                samples.popleft()

    def _history_ready(self, samples: deque) -> bool:
        if len(samples) < self.min_samples:
            return False
        return (samples[-1][0] - samples[0][0]) >= min(
            1.0, self.history_seconds * 0.5
        )

    @staticmethod
    def _median(samples: deque, index: int) -> float | None:
        values = [item[index] for item in samples if item[index] is not None]
        return float(np.median(values)) if values else None

    def _summarize(self) -> dict:
        trusted_fraction = (
            float(np.mean([item[1] for item in self._status_samples]))
            if self._status_samples
            else 0.0
        )
        return {
            "peak": self._median(self._quality_samples, 1),
            "local_background": self._median(self._quality_samples, 2),
            "local_noise": self._median(self._quality_samples, 3),
            "cnr": self._median(self._quality_samples, 4),
            "target_saturation": self._median(self._scene_samples, 3) or 0.0,
            "background": self._median(self._scene_samples, 1) or 0.0,
            "saturation": self._median(self._scene_samples, 2) or 0.0,
            "trusted_fraction": trusted_fraction,
        }

    def _decision(
        self,
        summary: dict,
        *,
        exposure_us: float | None = None,
        changed: bool = False,
        reason: str,
        rollback: bool = False,
    ) -> ExposureDecision:
        return ExposureDecision(
            self.current_exposure_us if exposure_us is None else exposure_us,
            changed,
            reason,
            summary["peak"],
            summary["background"],
            summary["saturation"],
            summary["local_background"],
            summary["local_noise"],
            summary["cnr"],
            summary["trusted_fraction"],
            summary["target_saturation"],
            rollback,
        )

    def _clear_history(self) -> None:
        self._quality_samples.clear()
        self._scene_samples.clear()
        self._status_samples.clear()

    def _apply_factor(
        self,
        factor: float,
        *,
        reason: str,
        summary: dict,
        allow_rollback: bool,
        now: float,
        max_fraction: float | None = None,
    ) -> ExposureDecision:
        # A busca por alvo ausente usa um passo maior que o controle normal.
        limite = self.max_step_fraction if max_fraction is None else float(max_fraction)
        factor = float(np.clip(factor, 1.0 - limite, 1.0 + limite))
        previous_exposure = self.current_exposure_us
        new_exposure = float(
            np.clip(previous_exposure * factor, self.minimum_us, self.maximum_us)
        )
        new_exposure = float(round(new_exposure))
        changed = abs(new_exposure - previous_exposure) >= 1.0
        self.current_exposure_us = new_exposure
        if changed:
            self._last_change_t = now
            self._last_change_previous_exposure = previous_exposure
            self._last_change_can_rollback = bool(allow_rollback)
            self._untrusted_since = None
            self._clear_history()
        return self._decision(
            summary,
            exposure_us=new_exposure,
            changed=changed,
            reason=reason,
        )

    def _rollback(self, now: float, summary: dict) -> ExposureDecision:
        previous = float(self._last_change_previous_exposure)
        changed = abs(previous - self.current_exposure_us) >= 1.0
        self.current_exposure_us = previous
        self._last_change_t = None
        self._last_change_previous_exposure = None
        self._last_change_can_rollback = False
        self._untrusted_since = None
        self._clear_history()
        self._last_evaluation_t = now
        return self._decision(
            summary,
            exposure_us=previous,
            changed=changed,
            reason="reversao_ajuste_sem_sinal",
            rollback=True,
        )

    def observe(
        self,
        now: float,
        frame: np.ndarray,
        *,
        target_center: tuple[float, float] | None,
        target_diameter_px: float | None,
        trusted_target: bool,
        target_present: bool,
    ) -> ExposureDecision:
        now = float(now)
        metrics = exposure_metrics(
            frame,
            target_center=target_center if target_present else None,
            target_diameter_px=target_diameter_px,
        )
        self._scene_samples.append(
            (
                now,
                metrics.background_percentile,
                metrics.saturation_fraction,
                metrics.target_saturation_fraction,
            )
        )
        self._status_samples.append(
            (now, float(bool(trusted_target)), float(bool(target_present)))
        )
        if trusted_target and metrics.cnr is not None:
            self._quality_samples.append(
                (
                    now,
                    metrics.target_level,
                    metrics.local_background,
                    metrics.local_noise,
                    metrics.cnr,
                )
            )
        self._purge(now)
        summary = self._summarize()

        if not self.enabled:
            return self._decision(summary, reason="desativada")

        scene_ready = self._history_ready(self._scene_samples)
        unsafe_scene = scene_ready and (
            summary["background"] >= self.background_high
            or summary["saturation"] >= self.saturation_fraction_limit
            or summary["target_saturation"] >= self.saturation_fraction_limit
        )

        if not trusted_target:
            if self._untrusted_since is None:
                self._untrusted_since = now
            untrusted_s = max(0.0, now - self._untrusted_since)
            recent_change = (
                self._last_change_can_rollback
                and self._last_change_t is not None
                and now - self._last_change_t <= self.rollback_window_s
            )
            if recent_change and untrusted_s >= self.rollback_loss_s:
                return self._rollback(now, summary)
            if (
                unsafe_scene
                and not self._untrusted_safety_used
                and now - self._last_safety_t >= self.safety_update_s
            ):
                self._last_safety_t = now
                self._untrusted_safety_used = True
                return self._apply_factor(
                    1.0 - self.max_step_fraction,
                    reason="reducao_emergencial_cena_saturando",
                    summary=summary,
                    allow_rollback=False,
                    now=now,
                )
            # Alvo ausente com a cena ESCURA: a hipotese mais provavel e que a
            # exposicao esteja baixa demais para o beacon atual. Congelar aqui
            # cria um impasse, porque so uma exposicao maior traria o alvo de
            # volta. Subir em degraus e seguro: sem alvo o mount ja esta parado,
            # e a cena escura garante que nao estamos fugindo de saturacao.
            if (
                not target_present
                and scene_ready
                and not unsafe_scene
                and summary["background"] <= self.background_increase_limit
                and (self._loss_search_active or untrusted_s >= self.loss_search_s)
                and now - self._last_loss_search_t >= self.loss_search_interval_s
                and self.current_exposure_us < self.maximum_us
            ):
                # Cada mudanca zera _untrusted_since; sem esta trava a rampa
                # esperaria loss_search_s de novo a cada degrau e chegaria tarde.
                self._loss_search_active = True
                self._last_loss_search_t = now
                return self._apply_factor(
                    1.0 + self.loss_search_step,
                    reason="busca_alvo_ausente_cena_escura",
                    summary=summary,
                    allow_rollback=False,
                    now=now,
                    max_fraction=self.loss_search_step,
                )
            reason = (
                "congelada_anomalia_optica"
                if target_present
                else "congelada_alvo_ausente"
            )
            return self._decision(summary, reason=reason)

        self._untrusted_since = None
        self._untrusted_safety_used = False
        self._loss_search_active = False
        if unsafe_scene and now - self._last_safety_t >= self.safety_update_s:
            self._last_safety_t = now
            return self._apply_factor(
                1.0 - self.max_step_fraction,
                reason="reducao_cena_saturando",
                summary=summary,
                allow_rollback=False,
                now=now,
            )

        if now - self._last_evaluation_t < self.update_seconds:
            return self._decision(summary, reason="aguardando_intervalo")
        self._last_evaluation_t = now

        quality_ready = self._history_ready(self._quality_samples)
        status_ready = self._history_ready(self._status_samples)
        if not quality_ready or not status_ready or summary["cnr"] is None:
            return self._decision(summary, reason="formando_historico_contraste")
        if summary["trusted_fraction"] < self.min_trusted_fraction:
            return self._decision(
                summary,
                reason="congelada_estabilidade_insuficiente",
            )

        if summary["cnr"] < self.cnr_low:
            if summary["background"] >= self.background_increase_limit:
                return self._decision(summary, reason="cnr_baixo_limitada_pelo_fundo")
            return self._apply_factor(
                1.0 + self.max_step_fraction,
                reason="aumento_cnr_baixo",
                summary=summary,
                allow_rollback=True,
                now=now,
            )

        if summary["cnr"] > self.cnr_high:
            return self._apply_factor(
                1.0 - self.reduction_step_fraction,
                reason="reducao_cnr_com_folga",
                summary=summary,
                allow_rollback=True,
                now=now,
            )
        return self._decision(summary, reason="faixa_cnr_adequada")
