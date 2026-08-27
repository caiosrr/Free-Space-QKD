"""Controlador matematico do tracker, sem camera ou interface grafica."""

import numpy as np


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
