"""Rejeita mudancas opticas bruscas antes da media e do controle do mount."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from modulos.configuracoes.tracker import (
    OPTICAL_AREA_RATIO_HIGH,
    OPTICAL_AREA_RATIO_LOW,
    OPTICAL_BASELINE_WINDOW_SECONDS,
    OPTICAL_COMPACTNESS_RATIO_HIGH,
    OPTICAL_COMPACTNESS_RATIO_LOW,
    OPTICAL_INITIAL_STABLE_SECONDS,
    OPTICAL_INTENSITY_RATIO_HIGH,
    OPTICAL_INTENSITY_RATIO_LOW,
    OPTICAL_LINEAR_SIZE_RATIO_HIGH,
    OPTICAL_LINEAR_SIZE_RATIO_LOW,
    OPTICAL_MIN_BASELINE_FRAMES,
    OPTICAL_MIN_SIGNATURE_SIMILARITY,
    OPTICAL_RECOVERY_STABLE_SECONDS,
)


METRIC_KEYS = ("raw_total", "area", "bbox_w", "bbox_h", "compactness")
RATIO_RULES = (
    (
        "intensidade", "raw_total",
        OPTICAL_INTENSITY_RATIO_LOW, OPTICAL_INTENSITY_RATIO_HIGH,
        "intensidade_baixa", "intensidade_alta",
    ),
    (
        "area", "area",
        OPTICAL_AREA_RATIO_LOW, OPTICAL_AREA_RATIO_HIGH,
        "area_reduzida", "area_expandida",
    ),
    (
        "largura", "bbox_w",
        OPTICAL_LINEAR_SIZE_RATIO_LOW, OPTICAL_LINEAR_SIZE_RATIO_HIGH,
        "largura_reduzida", "largura_expandida",
    ),
    (
        "altura", "bbox_h",
        OPTICAL_LINEAR_SIZE_RATIO_LOW, OPTICAL_LINEAR_SIZE_RATIO_HIGH,
        "altura_reduzida", "altura_expandida",
    ),
    (
        "compacidade", "compactness",
        OPTICAL_COMPACTNESS_RATIO_LOW, OPTICAL_COMPACTNESS_RATIO_HIGH,
        "forma_alterada", "forma_alterada",
    ),
)


@dataclass(frozen=True)
class OpticalQualityDecision:
    """Resultado usado pela aquisicao, interface e telemetria."""

    accepted: bool
    phase: str
    reasons: tuple[str, ...]
    ratios: dict[str, float]
    stable_seconds: float
    event: str = ""


def _positive_metrics(candidate: dict | None) -> dict[str, float] | None:
    if not isinstance(candidate, dict):
        return None
    try:
        metrics = {key: float(candidate[key]) for key in METRIC_KEYS}
        similarity = candidate.get("similarity_primary")
        metrics["similarity_primary"] = (
            float(similarity) if similarity is not None else 1.0
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not all(np.isfinite(value) for value in metrics.values()):
        return None
    if any(metrics[key] <= 0.0 for key in METRIC_KEYS):
        return None
    return metrics


class OpticalQualityGate:
    """Mantem uma referencia robusta e congela o controle durante anomalias."""

    def __init__(
        self,
        initial_reference: dict | None = None,
        *,
        baseline_window_s: float = OPTICAL_BASELINE_WINDOW_SECONDS,
        initial_stable_s: float = OPTICAL_INITIAL_STABLE_SECONDS,
        recovery_stable_s: float = OPTICAL_RECOVERY_STABLE_SECONDS,
        min_baseline_frames: int = OPTICAL_MIN_BASELINE_FRAMES,
    ):
        self.baseline_window_s = float(baseline_window_s)
        self.initial_stable_s = float(initial_stable_s)
        self.recovery_stable_s = float(recovery_stable_s)
        self.min_baseline_frames = int(min_baseline_frames)
        self.phase = "aquecendo"
        self._entries = deque()
        self._warmup_started = None
        self._recovery_started = None
        self._anomaly_reported = False
        self._initial_reference = _positive_metrics(initial_reference)

    @staticmethod
    def _median(entries) -> dict[str, float] | None:
        if not entries:
            return None
        return {
            key: float(np.median([entry[1][key] for entry in entries]))
            for key in METRIC_KEYS
        }

    def _reference(self) -> dict[str, float] | None:
        reference = self._median(self._entries)
        if reference is not None:
            return reference
        if self._initial_reference is None:
            return None
        return {key: self._initial_reference[key] for key in METRIC_KEYS}

    def _prune(self, now: float) -> None:
        cutoff = float(now) - self.baseline_window_s
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()

    def _append(self, now: float, metrics: dict[str, float]) -> None:
        self._entries.append((float(now), metrics.copy()))
        self._prune(now)

    @staticmethod
    def _ratio(value: float, reference: float) -> float:
        return float(value / max(reference, 1e-9))

    def _compare(
        self,
        metrics: dict[str, float],
        reference: dict[str, float] | None,
    ) -> tuple[tuple[str, ...], dict[str, float]]:
        if reference is None:
            return (), {}

        ratios = {
            name: self._ratio(metrics[metric], reference[metric])
            for name, metric, *_ in RATIO_RULES
        }
        ratios["similaridade"] = float(metrics["similarity_primary"])
        reasons = []
        for name, _, low, high, low_name, high_name in RATIO_RULES:
            ratio = ratios[name]
            if ratio < low:
                reasons.append(low_name)
            elif ratio > high:
                reasons.append(high_name)
        if ratios["similaridade"] < OPTICAL_MIN_SIGNATURE_SIMILARITY:
            reasons.append("assinatura_baixa")
        return tuple(dict.fromkeys(reasons)), ratios

    def _decision(
        self,
        accepted: bool,
        reasons: tuple[str, ...] = (),
        ratios: dict[str, float] | None = None,
        stable_seconds: float = 0.0,
        event: str = "",
    ) -> OpticalQualityDecision:
        return OpticalQualityDecision(
            accepted=accepted,
            phase=self.phase,
            reasons=reasons,
            ratios={} if ratios is None else ratios,
            stable_seconds=float(stable_seconds),
            event=event,
        )

    def observe(
        self,
        now: float,
        candidate: dict | None,
    ) -> OpticalQualityDecision:
        """Aceita apenas aparencia normal ou recuperada de forma persistente."""
        now = float(now)
        metrics = _positive_metrics(candidate)
        if metrics is None:
            if self.phase != "aquecendo":
                self.phase = "sem_sinal"
            self._recovery_started = None
            return self._decision(False)

        reference = self._reference()
        reasons, ratios = self._compare(metrics, reference)

        if self.phase == "aquecendo":
            if reasons:
                self._entries.clear()
                self._warmup_started = None
                return self._decision(False, reasons, ratios)
            if self._warmup_started is None:
                self._warmup_started = now
            self._append(now, metrics)
            stable_s = now - self._warmup_started
            if (
                stable_s >= self.initial_stable_s
                and len(self._entries) >= self.min_baseline_frames
            ):
                self.phase = "normal"
                return self._decision(True, ratios=ratios, stable_seconds=stable_s)
            return self._decision(False, ratios=ratios, stable_seconds=stable_s)

        if reasons:
            first_anomaly = not self._anomaly_reported
            self.phase = "anomalia"
            self._recovery_started = None
            self._anomaly_reported = True
            event = ""
            if first_anomaly:
                event = "anomalia_optica_" + "_".join(reasons[:3])
            return self._decision(False, reasons, ratios, event=event)

        if self.phase in {"anomalia", "recuperando", "sem_sinal"}:
            self.phase = "recuperando"
            if self._recovery_started is None:
                self._recovery_started = now
            stable_s = now - self._recovery_started
            if stable_s < self.recovery_stable_s:
                return self._decision(False, ratios=ratios, stable_seconds=stable_s)

            event = "anomalia_optica_recuperada" if self._anomaly_reported else ""
            self.phase = "normal"
            self._recovery_started = None
            self._anomaly_reported = False
            self._append(now, metrics)
            return self._decision(True, ratios=ratios, stable_seconds=stable_s, event=event)

        self.phase = "normal"
        self._append(now, metrics)
        return self._decision(True, ratios=ratios)
