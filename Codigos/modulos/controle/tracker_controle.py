"""Controlador matematico do tracker, sem camera ou interface grafica."""

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SlowBiasEstimate:
    dx_px: float
    dy_px: float
    span_s: float
    ready: bool

    @property
    def radius_px(self) -> float:
        return float(np.hypot(self.dx_px, self.dy_px))


class SlowBiasEstimator:
    """Mediana temporal longa usada apenas para decidir correcoes finas."""

    def __init__(self, window_s=8.0, warmup_s=4.0, min_samples=5):
        self.window_s = float(window_s)
        self.warmup_s = float(warmup_s)
        self.min_samples = int(min_samples)
        if not (0.0 < self.warmup_s <= self.window_s):
            raise ValueError("O aquecimento do vies precisa caber na janela.")
        if self.min_samples < 2:
            raise ValueError("O estimador lento precisa de pelo menos duas amostras.")
        self.reset()

    def reset(self):
        self._samples = deque()

    def observe(self, timestamp, dx_px, dy_px) -> SlowBiasEstimate:
        timestamp = float(timestamp)
        values = np.asarray([dx_px, dy_px], dtype=float)
        if not np.all(np.isfinite(values)):
            self.reset()
            return SlowBiasEstimate(0.0, 0.0, 0.0, False)

        self._samples.append((timestamp, float(values[0]), float(values[1])))
        cutoff = timestamp - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        samples = np.asarray([(item[1], item[2]) for item in self._samples])
        span_s = max(0.0, timestamp - self._samples[0][0])
        median = np.median(samples, axis=0)
        ready = len(self._samples) >= self.min_samples and span_s >= self.warmup_s
        return SlowBiasEstimate(
            float(median[0]),
            float(median[1]),
            span_s,
            bool(ready),
        )


@dataclass(frozen=True)
class DirectionalErrorEstimate:
    """Resumo robusto da persistencia de um deslocamento grande."""

    dx_px: float
    dy_px: float
    span_s: float
    large_fraction: float
    direction_coherence: float
    ready: bool

    @property
    def radius_px(self) -> float:
        return float(np.hypot(self.dx_px, self.dy_px))


class DirectionalErrorEstimator:
    """Distingue deslocamento coerente de oscilacao radial em torno do alvo."""

    def __init__(
        self,
        radius_threshold_px=5.0,
        window_s=3.0,
        confirm_s=2.0,
        min_large_fraction=0.70,
        min_direction_coherence=0.80,
        min_samples=8,
    ):
        self.radius_threshold_px = float(radius_threshold_px)
        self.window_s = float(window_s)
        self.confirm_s = float(confirm_s)
        self.min_large_fraction = float(min_large_fraction)
        self.min_direction_coherence = float(min_direction_coherence)
        self.min_samples = int(min_samples)
        if not (
            self.radius_threshold_px > 0.0
            and 0.0 < self.confirm_s <= self.window_s
            and 0.5 < self.min_large_fraction <= 1.0
            and 0.5 < self.min_direction_coherence <= 1.0
            and self.min_samples >= 3
        ):
            raise ValueError("Parametros invalidos para confirmar erro direcional.")
        self.reset()

    def reset(self):
        self._samples = deque()

    def observe(self, timestamp, dx_px, dy_px) -> DirectionalErrorEstimate:
        timestamp = float(timestamp)
        values = np.asarray([dx_px, dy_px], dtype=float)
        if not np.all(np.isfinite(values)):
            self.reset()
            return DirectionalErrorEstimate(0.0, 0.0, 0.0, 0.0, 0.0, False)

        self._samples.append((timestamp, float(values[0]), float(values[1])))
        cutoff = timestamp - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        points = np.asarray([(item[1], item[2]) for item in self._samples])
        radii = np.hypot(points[:, 0], points[:, 1])
        large = radii >= self.radius_threshold_px
        large_fraction = float(np.mean(large))
        span_s = max(0.0, timestamp - self._samples[0][0])

        if np.any(large):
            large_points = points[large]
            large_radii = radii[large]
            unit_vectors = large_points / large_radii[:, None]
            direction_coherence = float(np.linalg.norm(np.mean(unit_vectors, axis=0)))
            median = np.median(large_points, axis=0)
        else:
            direction_coherence = 0.0
            median = np.zeros(2, dtype=float)

        median_radius = float(np.hypot(median[0], median[1]))
        ready = bool(
            len(self._samples) >= self.min_samples
            and span_s >= self.confirm_s
            and large_fraction >= self.min_large_fraction
            and direction_coherence >= self.min_direction_coherence
            and median_radius >= self.radius_threshold_px
        )
        return DirectionalErrorEstimate(
            float(median[0]),
            float(median[1]),
            span_s,
            large_fraction,
            direction_coherence,
            ready,
        )


@dataclass(frozen=True)
class CorrectionDecision:
    hold_active: bool
    source: str
    persistence_s: float


class SlowCorrectionGate:
    """Atua em vies lento ou em erro grande previamente confirmado."""

    def __init__(
        self,
        enter_radius_px=1.0,
        exit_radius_px=2.0,
        persistence_s=1.5,
        fast_radius_px=5.0,
    ):
        self.enter_radius_px = float(enter_radius_px)
        self.exit_radius_px = float(exit_radius_px)
        self.persistence_s = float(persistence_s)
        self.fast_radius_px = float(fast_radius_px)
        if not (
            0.0 < self.enter_radius_px < self.exit_radius_px < self.fast_radius_px
            and self.persistence_s > 0.0
        ):
            raise ValueError("Parametros invalidos para a porta de correcao lenta.")
        self.reset()

    def reset(self):
        self._correction_active = False
        self._above_since = None

    def update(
        self,
        timestamp,
        *,
        fast_radius_px,
        fast_confirmed=False,
        fast_persistence_s=0.0,
        slow_radius_px=None,
        slow_ready=False,
    ) -> CorrectionDecision:
        timestamp = float(timestamp)
        fast_radius_px = float(fast_radius_px)
        slow_ready = bool(slow_ready and slow_radius_px is not None)
        slow_radius_px = float(slow_radius_px) if slow_ready else None

        if not np.isfinite(fast_radius_px) or (
            slow_ready and not np.isfinite(slow_radius_px)
        ):
            self.reset()
            return CorrectionDecision(True, "repouso", 0.0)

        if fast_confirmed:
            self._correction_active = True
            self._above_since = None
            return CorrectionDecision(
                False,
                "erro_grande_persistente",
                float(fast_persistence_s),
            )

        if self._correction_active:
            control_radius = slow_radius_px if slow_ready else fast_radius_px
            if control_radius <= self.enter_radius_px:
                self.reset()
                return CorrectionDecision(True, "repouso", 0.0)
            source = (
                "vies_lento" if slow_ready else "erro_grande_persistente"
            )
            return CorrectionDecision(False, source, 0.0)

        if not slow_ready or slow_radius_px < self.exit_radius_px:
            self._above_since = None
            if fast_radius_px >= self.fast_radius_px:
                return CorrectionDecision(
                    True,
                    "aguardando_erro_grande",
                    float(fast_persistence_s),
                )
            return CorrectionDecision(True, "repouso", 0.0)

        if self._above_since is None:
            self._above_since = timestamp
        elapsed = max(0.0, timestamp - self._above_since)
        if elapsed >= self.persistence_s:
            self._correction_active = True
            self._above_since = None
            return CorrectionDecision(False, "vies_lento", elapsed)
        return CorrectionDecision(True, "aguardando_vies", elapsed)


class FinePulseAxis:
    """Produz micropulsos separados pelo tempo de resposta da media temporal."""

    def __init__(
        self,
        min_rate,
        *,
        correction_fraction=0.35,
        min_pulse_s=0.02,
        max_pulse_s=0.12,
        settle_s=2.0,
    ):
        self.min_rate = abs(float(min_rate))
        self.correction_fraction = float(correction_fraction)
        self.min_pulse_s = float(min_pulse_s)
        self.max_pulse_s = float(max_pulse_s)
        self.settle_s = float(settle_s)
        if self.min_rate <= 0.0:
            raise ValueError("A velocidade minima do micropulso deve ser positiva.")
        if not (0.0 < self.correction_fraction <= 1.0):
            raise ValueError("A fracao de correcao deve estar entre 0 e 1.")
        if not (0.0 < self.min_pulse_s <= self.max_pulse_s):
            raise ValueError("Os limites de duracao do micropulso sao invalidos.")
        if self.settle_s < 0.0:
            raise ValueError("O tempo de acomodacao nao pode ser negativo.")
        self.reset()

    def reset(self):
        self._pulse_rate = 0.0
        self._pulse_until = 0.0
        self._settle_until = 0.0

    def command(self, now, error_deg, enabled=True):
        """Retorna velocidade minima durante o pulso e zero na acomodacao."""
        now = float(now)
        error_deg = float(error_deg)
        if not enabled or not np.isfinite(error_deg) or abs(error_deg) < 1e-12:
            self.reset()
            return 0.0
        if now < self._pulse_until:
            return self._pulse_rate
        if now < self._settle_until:
            return 0.0

        duration = float(
            np.clip(
                abs(error_deg) * self.correction_fraction / self.min_rate,
                self.min_pulse_s,
                self.max_pulse_s,
            )
        )
        self._pulse_rate = float(np.sign(error_deg) * self.min_rate)
        self._pulse_until = now + duration
        self._settle_until = self._pulse_until + self.settle_s
        return self._pulse_rate


class MeasurementPDTrim:
    """PD rapido com correcao lenta de vies persistente perto do centro."""

    def __init__(
        self,
        kp,
        kd,
        trim_gain,
        output_limits,
        derivative_alpha=0.70,
        trim_limit=0.18,
        trim_leak=0.995,
        trim_error_max=0.0025,
        trim_derivative_max=0.03,
        trim_same_sign_s=0.25,
        trim_sign_eps=0.00015,
        trim_sign_flip_damp=0.35,
    ):
        self.kp = kp
        self.kd = kd
        self.trim_gain = trim_gain
        self.min_output, self.max_output = output_limits
        self.derivative_alpha = derivative_alpha
        self.trim_limit = abs(float(trim_limit))
        self.trim_leak = float(trim_leak)
        self.trim_error_max = abs(float(trim_error_max))
        self.trim_derivative_max = abs(float(trim_derivative_max))
        self.trim_same_sign_s = float(trim_same_sign_s)
        self.trim_sign_eps = abs(float(trim_sign_eps))
        self.trim_sign_flip_damp = float(trim_sign_flip_damp)
        self.reset()

    def reset(self):
        self._last_error = None
        self._last_t = None
        self._d_filt = 0.0
        self.clear_trim()

    def clear_trim(self):
        self._trim_bias = 0.0
        self._held_sign = 0
        self._same_sign_elapsed = 0.0

    def _clip(self, value):
        if self.min_output is not None and value < self.min_output:
            return self.min_output
        if self.max_output is not None and value > self.max_output:
            return self.max_output
        return value

    def update(self, error, timestamp, trim_allowed):
        if self._last_t is None or timestamp <= self._last_t:
            self._last_error = error
            self._last_t = timestamp
            self._d_filt = 0.0
            self.clear_trim()
            return self._clip(self.kp * error), 0.0

        dt = max(timestamp - self._last_t, 1e-3)
        deriv = (error - self._last_error) / dt
        self._d_filt = (self.derivative_alpha * self._d_filt) + (
            (1.0 - self.derivative_alpha) * deriv
        )

        sign = 0 if abs(error) < self.trim_sign_eps else (1 if error > 0.0 else -1)
        if sign == 0:
            self._held_sign = 0
            self._same_sign_elapsed = 0.0
            self._trim_bias *= self.trim_leak
        else:
            if sign == self._held_sign:
                self._same_sign_elapsed += dt
            else:
                if self._held_sign != 0:
                    self._trim_bias *= self.trim_sign_flip_damp
                self._held_sign = sign
                self._same_sign_elapsed = 0.0

            trim_ready = (
                trim_allowed
                and self._same_sign_elapsed >= self.trim_same_sign_s
                and abs(error) <= self.trim_error_max
                and abs(self._d_filt) <= self.trim_derivative_max
            )
            if trim_ready:
                self._trim_bias = float(
                    np.clip(
                        self._trim_bias + (self.trim_gain * error * dt),
                        -self.trim_limit,
                        self.trim_limit,
                    )
                )
            else:
                self._trim_bias *= self.trim_leak

        output = self._clip(
            (self.kp * error) + (self.kd * self._d_filt) + self._trim_bias
        )
        self._last_error = error
        self._last_t = timestamp
        return output, float(self._trim_bias)


def pixel_error_to_mount_error(dx_px, dy_px, A_inv):
    """Converte erro visual no movimento angular que anula esse erro."""
    err_vec = np.asarray(A_inv, dtype=float) @ np.array([-dx_px, -dy_px], dtype=float)
    return float(err_vec[0]), float(err_vec[1])
