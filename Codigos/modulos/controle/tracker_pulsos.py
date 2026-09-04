"""Limita cada correcao e exige uma media inteiramente posterior a parada."""

import numpy as np


class BoundedCorrectionCycle:
    """Dois eixos podem pulsar juntos; nenhum reinicia antes da nova medicao.

    Os prazos sao conferidos pelo loop de controle, mesmo sem um novo frame.
    A espera comeca APOS os comandos zero serem confirmados pelo driver.
    """

    def __init__(self, min_rate, *, min_s, fine_max_s=0.12, large_max_s=0.25,
                 fraction=0.35, settle_s=0.3, image_window_s=2.0):
        values = [min_rate, min_s, fine_max_s, large_max_s, fraction, settle_s, image_window_s]
        if not np.all(np.isfinite(values)) or not (
            min_rate > 0 and 0 < min_s <= fine_max_s <= large_max_s
            and 0 < fraction <= 1 and settle_s >= 0 and image_window_s > 0
        ):
            raise ValueError("Limites invalidos para a correcao por pulsos.")
        self.min_rate = float(min_rate)
        self.min_s, self.fine_max_s, self.large_max_s = min_s, fine_max_s, large_max_s
        self.fraction = fraction
        self.refresh_s = settle_s + image_window_s
        self.phase = "pronto"
        self.accept_after = 0.0
        self.rates = np.zeros(2)
        self.deadlines = np.zeros(2)
        self.completed = 0

    def ready(self, measurement_ts):
        if self.phase == "acomodacao" and measurement_ts >= self.accept_after:
            self.phase = "pronto"
        return self.phase == "pronto"

    def command(self, now, measurement_ts, proposed, error, current_error, *, fine, enabled):
        if not enabled:
            if self.phase == "pulso":
                self.phase = "parando"
            return np.zeros(2)
        if self.phase == "pulso":
            commands = np.where(now < self.deadlines, self.rates, 0.0)
            if not np.any(commands):
                self.phase = "parando"
            return commands
        if not self.ready(measurement_ts):
            return np.zeros(2)
        proposed, error, current_error = map(lambda a: np.asarray(a, dtype=float),
                                             (proposed, error, current_error))
        # Um historico antigo nao pode comandar contra o erro angular atual.
        eligible = (np.isfinite(error) & np.isfinite(current_error) & np.isfinite(proposed)
                    & (error * current_error > 0) & (proposed * error > 0))
        budget = np.minimum(np.abs(error), np.abs(current_error)) * self.fraction
        duration = np.minimum(budget / self.min_rate, self.fine_max_s if fine else self.large_max_s)
        # Nao arredondar um erro minusculo para um pulso maior que seu orcamento.
        eligible &= duration >= self.min_s
        self.rates = np.where(eligible, np.sign(error) * self.min_rate, 0.0)
        if not np.any(self.rates):
            return np.zeros(2)
        self.deadlines = now + np.where(eligible, duration, 0.0)
        self.phase = "pulso"
        return self.rates.copy()

    def confirm_stopped(self, now):
        """Chamar somente depois de ambos os envios de velocidade zero concluirem."""
        if self.phase != "parando":
            return False
        self.phase = "acomodacao"
        self.accept_after = now + self.refresh_s
        self.completed += 1
        return True
