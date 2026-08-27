"""Autoteste TEMPORARIO de recuperacao antes de uma sessao do tracker.

O alvo visual permanece fixo. O mount produz um pequeno deslocamento conhecido
e a malha normal precisa levar a mesma ilha de volta a zona de repouso.
"""

from dataclasses import dataclass
import time

import numpy as np

from modulos.configuracoes.tracker import (
    HOLD_ENTER_RADIUS_PX,
    PREFLIGHT_MAX_AXIS_STEP_DEG,
    PREFLIGHT_MAX_RATE_DEG_S,
    PREFLIGHT_MIN_CONFIRMED_ERROR_PX,
    PREFLIGHT_RECOVERY_CONFIRM_FRAMES,
    PREFLIGHT_RECOVERY_TIMEOUT_SECONDS,
    PREFLIGHT_SHIFT_X_PX,
    PREFLIGHT_SHIFT_Y_PX,
)
from modulos.controle.mount_control import move_axes_pid_2d, stop_axes_safely
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_seguranca import solicitar_parada


@dataclass
class VerificadorRecuperacao:
    """Confirma primeiro o deslocamento e depois o retorno persistente."""

    deslocamento_confirmado: bool = False
    frames_recuperados: int = 0

    def observar(self, tem_sinal: bool, dx_px: float, dy_px: float) -> bool:
        if not tem_sinal:
            self.frames_recuperados = 0
            return False

        raio_px = float(np.hypot(dx_px, dy_px))
        if raio_px >= PREFLIGHT_MIN_CONFIRMED_ERROR_PX:
            self.deslocamento_confirmado = True
            self.frames_recuperados = 0
            return False

        if self.deslocamento_confirmado and raio_px <= HOLD_ENTER_RADIUS_PX:
            self.frames_recuperados += 1
        else:
            self.frames_recuperados = 0
        return self.frames_recuperados >= PREFLIGHT_RECOVERY_CONFIRM_FRAMES


def calcular_deslocamento_mount(A_inv: np.ndarray) -> tuple[float, float]:
    """Converte o deslocamento visual desejado em um passo angular limitado."""
    matrix = np.asarray(A_inv, dtype=float)
    if matrix.shape != (2, 2) or not np.all(np.isfinite(matrix)):
        raise ValueError("A matriz do autoteste precisa ser 2x2 e finita.")

    desired_pixels = np.array(
        [PREFLIGHT_SHIFT_X_PX, PREFLIGHT_SHIFT_Y_PX],
        dtype=float,
    )
    delta_az, delta_alt = matrix @ desired_pixels
    if not np.all(np.isfinite([delta_az, delta_alt])):
        raise ValueError("A matriz produziu um deslocamento angular invalido.")
    if max(abs(delta_az), abs(delta_alt)) > PREFLIGHT_MAX_AXIS_STEP_DEG:
        raise ValueError(
            "O passo calculado para o autoteste excedeu "
            f"{PREFLIGHT_MAX_AXIS_STEP_DEG:g} deg por eixo."
        )
    return float(delta_az), float(delta_alt)


def aplicar_deslocamento_autoteste(
    state: TrackerState,
    A_inv: np.ndarray,
) -> tuple[float, float]:
    """Move o mount com o controle principal ainda pausado."""
    with state.lock:
        state.preflight_active = True
        state.preflight_passed = False

    try:
        delta_az, delta_alt = calcular_deslocamento_mount(A_inv)
        print(
            "\nAUTOTESTE TEMPORARIO: deslocando a ilha em "
            f"({PREFLIGHT_SHIFT_X_PX:+.0f}, {PREFLIGHT_SHIFT_Y_PX:+.0f}) px."
        )
        print(
            f"Passo calculado: dAz={delta_az:+.6f} deg | "
            f"dAlt={delta_alt:+.6f} deg"
        )
        move_axes_pid_2d(
            True,
            delta_az,
            delta_alt,
            max_velocity_deg_s=PREFLIGHT_MAX_RATE_DEG_S,
        )
        stop_axes_safely()
        return delta_az, delta_alt
    except Exception:
        solicitar_parada(state, "autoteste_falhou_ao_aplicar_deslocamento")
        raise


def monitorar_recuperacao(state: TrackerState) -> None:
    """Aprova o teste ou encerra e deixa o retorno absoluto para o orquestrador."""
    deadline = time.perf_counter() + PREFLIGHT_RECOVERY_TIMEOUT_SECONDS
    last_seq = -1
    checker = VerificadorRecuperacao()

    while time.perf_counter() < deadline:
        with state.lock:
            if state.stop:
                return
            seq = state.measurement_seq
            has_signal = state.has_signal
            dx_px = state.dx_filt_px
            dy_px = state.dy_filt_px

        if seq != last_seq:
            last_seq = seq
            if checker.observar(has_signal, dx_px, dy_px):
                with state.lock:
                    state.preflight_active = False
                    state.preflight_passed = True
                print(
                    "\nAUTOTESTE APROVADO: a ilha retornou para a zona "
                    f"de {HOLD_ENTER_RADIUS_PX:g} px. Sessao normal continua."
                )
                return
        time.sleep(0.01)

    reason = (
        "autoteste_recuperacao_timeout"
        if checker.deslocamento_confirmado
        else "autoteste_deslocamento_nao_confirmado"
    )
    solicitar_parada(state, reason)
