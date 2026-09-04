"""Rejeita mudancas opticas bruscas antes da media e do controle do mount."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from modulos.configuracoes.tracker import (
    OPTICAL_AREA_RATIO_HIGH,
    OPTICAL_AREA_RATIO_LOW,
    OPTICAL_ANOMALY_ENTRY_BAD_FRACTION,
    OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES,
    OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS,
    OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS,
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
    OPTICAL_RECOVERY_ACCEPTED_FRACTION,
    OPTICAL_RECOVERY_MIN_SAMPLES,
    OPTICAL_RECOVERY_POSITION_P90_PX,
    OPTICAL_RECOVERY_STABLE_SECONDS,
    OPTICAL_RECOVERY_WINDOW_SECONDS,
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
    control_allowed: bool = False
    transient_rejection: bool = False
    anomaly_fraction: float = 0.0
    anomaly_window_s: float = 0.0
    recovery_fraction: float = 0.0
    position_spread_px: float | None = None
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


def _candidate_position(candidate: dict | None) -> tuple[float, float] | None:
    if not isinstance(candidate, dict):
        return None
    try:
        position = float(candidate["x_cm"]), float(candidate["y_cm"])
    except (KeyError, TypeError, ValueError):
        return None
    return position if all(np.isfinite(value) for value in position) else None


class OpticalQualityGate:
    """Mantem uma referencia robusta e congela o controle durante anomalias."""

    def __init__(
        self,
        initial_reference: dict | None = None,
        *,
        baseline_window_s: float = OPTICAL_BASELINE_WINDOW_SECONDS,
        initial_stable_s: float = OPTICAL_INITIAL_STABLE_SECONDS,
        recovery_stable_s: float = OPTICAL_RECOVERY_STABLE_SECONDS,
        recovery_window_s: float = OPTICAL_RECOVERY_WINDOW_SECONDS,
        recovery_accepted_fraction: float = OPTICAL_RECOVERY_ACCEPTED_FRACTION,
        recovery_min_samples: int = OPTICAL_RECOVERY_MIN_SAMPLES,
        recovery_position_p90_px: float = OPTICAL_RECOVERY_POSITION_P90_PX,
        anomaly_entry_window_s: float = OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS,
        anomaly_entry_min_coverage_s: float = (
            OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS
        ),
        anomaly_entry_bad_fraction: float = OPTICAL_ANOMALY_ENTRY_BAD_FRACTION,
        anomaly_entry_min_bad_frames: int = OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES,
        min_baseline_frames: int = OPTICAL_MIN_BASELINE_FRAMES,
    ):
        self.baseline_window_s = float(baseline_window_s)
        self.initial_stable_s = float(initial_stable_s)
        self.recovery_stable_s = float(recovery_stable_s)
        self.recovery_window_s = float(recovery_window_s)
        self.recovery_accepted_fraction = float(recovery_accepted_fraction)
        self.recovery_min_samples = int(recovery_min_samples)
        self.recovery_position_p90_px = float(recovery_position_p90_px)
        self.anomaly_entry_window_s = float(anomaly_entry_window_s)
        self.anomaly_entry_min_coverage_s = float(anomaly_entry_min_coverage_s)
        self.anomaly_entry_bad_fraction = float(anomaly_entry_bad_fraction)
        self.anomaly_entry_min_bad_frames = int(anomaly_entry_min_bad_frames)
        self.min_baseline_frames = int(min_baseline_frames)
        self.phase = "aquecendo"
        self._entries = deque()
        self._recovery_entries = deque()
        self._normal_votes = deque()
        self._warmup_started = None
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

    def _clear_recovery(self) -> None:
        self._recovery_entries.clear()

    def _clear_normal_votes(self) -> None:
        self._normal_votes.clear()

    def _append_normal_vote(self, now: float, *, anomalous: bool) -> None:
        self._normal_votes.append((float(now), bool(anomalous)))
        cutoff = float(now) - self.anomaly_entry_window_s
        while self._normal_votes and self._normal_votes[0][0] < cutoff:
            self._normal_votes.popleft()

    def _anomaly_is_persistent(self) -> tuple[bool, float, float]:
        if not self._normal_votes:
            return False, 0.0, 0.0
        coverage_s = max(
            0.0,
            self._normal_votes[-1][0] - self._normal_votes[0][0],
        )
        bad_count = sum(entry[1] for entry in self._normal_votes)
        bad_fraction = bad_count / len(self._normal_votes)
        persistent = bool(
            bad_count >= self.anomaly_entry_min_bad_frames
            and coverage_s >= self.anomaly_entry_min_coverage_s
            and bad_fraction >= self.anomaly_entry_bad_fraction
        )
        return persistent, coverage_s, bad_fraction

    def _append_recovery(
        self,
        now: float,
        *,
        compatible: bool,
        metrics: dict[str, float],
        candidate: dict,
    ) -> None:
        self._recovery_entries.append(
            (
                float(now),
                bool(compatible),
                metrics.copy(),
                _candidate_position(candidate),
            )
        )
        cutoff = float(now) - self.recovery_window_s
        while self._recovery_entries and self._recovery_entries[0][0] < cutoff:
            self._recovery_entries.popleft()

    def _recovery_consensus(self) -> tuple[bool, float, float, float | None]:
        """Confirma maioria opticamente compativel e centro espacialmente estavel."""
        if not self._recovery_entries:
            return False, 0.0, 0.0, None
        coverage_s = max(
            0.0,
            self._recovery_entries[-1][0] - self._recovery_entries[0][0],
        )
        compatible_fraction = float(
            np.mean([entry[1] for entry in self._recovery_entries])
        )
        positions = [
            entry[3] for entry in self._recovery_entries if entry[3] is not None
        ]
        position_spread = None
        position_stable = True
        if positions:
            points = np.asarray(positions, dtype=float)
            center = np.median(points, axis=0)
            radial = np.hypot(points[:, 0] - center[0], points[:, 1] - center[1])
            position_spread = float(np.percentile(radial, 90))
            position_stable = position_spread <= self.recovery_position_p90_px
        ready = bool(
            len(self._recovery_entries) >= self.recovery_min_samples
            and coverage_s >= self.recovery_stable_s
            and compatible_fraction >= self.recovery_accepted_fraction
            and position_stable
        )
        return ready, coverage_s, compatible_fraction, position_spread

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
        recovery_fraction: float = 0.0,
        position_spread_px: float | None = None,
        event: str = "",
        control_allowed: bool | None = None,
        transient_rejection: bool = False,
        anomaly_fraction: float = 0.0,
        anomaly_window_s: float = 0.0,
    ) -> OpticalQualityDecision:
        return OpticalQualityDecision(
            accepted=accepted,
            phase=self.phase,
            reasons=reasons,
            ratios={} if ratios is None else ratios,
            stable_seconds=float(stable_seconds),
            control_allowed=(
                accepted if control_allowed is None else control_allowed
            ),
            transient_rejection=bool(transient_rejection),
            anomaly_fraction=float(anomaly_fraction),
            anomaly_window_s=float(anomaly_window_s),
            recovery_fraction=float(recovery_fraction),
            position_spread_px=position_spread_px,
            event=event,
        )

    def observe(
        self,
        now: float,
        candidate: dict | None,
    ) -> OpticalQualityDecision:
        """Aceita aparencia normal ou recuperada por consenso temporal."""
        now = float(now)
        metrics = _positive_metrics(candidate)
        if metrics is None:
            if self.phase != "aquecendo":
                self.phase = "sem_sinal"
            self._clear_recovery()
            self._clear_normal_votes()
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
                self._clear_recovery()
                self._clear_normal_votes()
                self._append_normal_vote(now, anomalous=False)
                return self._decision(True, ratios=ratios, stable_seconds=stable_s)
            return self._decision(False, ratios=ratios, stable_seconds=stable_s)

        if self.phase == "normal":
            self._append_normal_vote(now, anomalous=bool(reasons))
            if reasons:
                persistent, coverage_s, bad_fraction = (
                    self._anomaly_is_persistent()
                )
                if not persistent:
                    return self._decision(
                        False,
                        reasons,
                        ratios,
                        control_allowed=True,
                        transient_rejection=True,
                        anomaly_fraction=bad_fraction,
                        anomaly_window_s=coverage_s,
                    )

                first_anomaly = not self._anomaly_reported
                self.phase = "anomalia"
                self._clear_normal_votes()
                self._clear_recovery()
                self._append_recovery(
                    now,
                    compatible=False,
                    metrics=metrics,
                    candidate=candidate,
                )
                self._anomaly_reported = True
                event = ""
                if first_anomaly:
                    event = "anomalia_optica_" + "_".join(reasons[:3])
                return self._decision(
                    False,
                    reasons,
                    ratios,
                    event=event,
                    anomaly_fraction=bad_fraction,
                    anomaly_window_s=coverage_s,
                )

            self._append(now, metrics)
            return self._decision(True, ratios=ratios)

        if self.phase in {"anomalia", "recuperando", "sem_sinal"}:
            self.phase = "recuperando"
            self._append_recovery(
                now,
                compatible=not reasons,
                metrics=metrics,
                candidate=candidate,
            )
            ready, coverage_s, fraction, position_spread = (
                self._recovery_consensus()
            )
            if not ready:
                return self._decision(
                    False,
                    reasons,
                    ratios,
                    stable_seconds=coverage_s,
                    recovery_fraction=fraction,
                    position_spread_px=position_spread,
                )

            event = "anomalia_optica_recuperada" if self._anomaly_reported else ""
            self.phase = "normal"
            self._anomaly_reported = False
            self._clear_normal_votes()
            # A maioria confiavel da nova janela passa a ser a referencia. Isso
            # permite acompanhar mudancas lentas sem aprender os frames extremos.
            self._entries.clear()
            for timestamp, compatible, accepted_metrics, _ in self._recovery_entries:
                if compatible:
                    self._entries.append((timestamp, accepted_metrics.copy()))
            self._clear_recovery()
            return self._decision(
                True,
                ratios=ratios,
                stable_seconds=coverage_s,
                recovery_fraction=fraction,
                position_spread_px=position_spread,
                event=event,
            )

        self.phase = "normal"
        self._clear_normal_votes()
        self._clear_recovery()
        self._append(now, metrics)
        return self._decision(True, ratios=ratios)
