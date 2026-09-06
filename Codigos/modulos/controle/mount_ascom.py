"""Camada minima de comandos do mount pelo ASCOM/Alpaca.

Objetivo: ler posicao, mover eixo e parar. Nada mais.
Entradas/saidas: graus e graus por segundo, sempre absolutos do driver.
Hardware: telescopio no ASCOM Remote Server.
Seguranca: ``stop_axes_safely`` tenta cada eixo de forma independente e
informa quando nao conseguiu confirmar a parada.

A malha PID e a interface de console ficam em ``mount_pid.py``. O tracker e o
watchdog importam somente este arquivo, sem carregar controlador nem console.
"""

from __future__ import annotations

from modulos.controle.ascom import telescope_device


# Tolerancia padrao de chegada e limites de velocidade aceitos pelo mount.
TOLERANCIA_GRAUS = 0.0005
VEL_MIN_LIMITE = 0.001042
VEL_MAX_LIMITE = 6.0


def call(method: str, command: str, timeout: float = 5.0, **extra_args):
    return telescope_device.call(method, command, timeout=timeout, **extra_args)


def set_mount_address(base_url: str) -> None:
    """Reaponta os comandos para outro servidor ASCOM.

    Usado pelo ``mount_agent`` e pelas ferramentas de dois telescopios, quando
    este processo precisa comandar o mount do emissor em vez do local.
    """
    telescope_device.base_url = str(base_url)


def mount_address() -> str:
    return telescope_device.base_url


def ensure_connected():
    if not call("GET", "connected"):
        call("PUT", "connected", data={"Connected": True})


def ensure_unparked():
    try:
        if call("GET", "atpark"):
            if call("GET", "canunpark"):
                call("PUT", "unpark", timeout=10)
    except Exception:
        pass


def ensure_not_tracking():
    try:
        if str(call("GET", "tracking")).lower() in {"true", "1"}:
            call("PUT", "tracking", data={"Tracking": False})
    except Exception:
        pass


def read_altaz():
    az = float(call("GET", "azimuth"))
    alt = float(call("GET", "altitude"))
    return az, alt


def move_axis(axis: int, rate_deg_per_s: float, mount: bool):
    if mount and axis == 0:
        rate_deg_per_s = -rate_deg_per_s  # Inverte o sinal do azimute no mount.
    call("PUT", "moveaxis", data={"Axis": axis, "Rate": float(rate_deg_per_s)})


def stop_axes_safely(attempts: int = 2, timeout: float = 2.0) -> bool:
    """Tenta zerar cada eixo de forma independente, mesmo se o outro falhar."""
    failed_axes = []
    for axis in (0, 1):
        stopped = False
        for _ in range(max(1, int(attempts))):
            try:
                call(
                    "PUT",
                    "moveaxis",
                    data={"Axis": axis, "Rate": 0.0},
                    timeout=timeout,
                )
                stopped = True
                break
            except Exception:
                continue
        if not stopped:
            failed_axes.append(axis)
    if failed_axes:
        print(f"ALERTA: nao consegui confirmar parada dos eixos {failed_axes}.")
        return False
    return True


def calc_error(axis: int, alvo: float, pos: float) -> float:
    """Erro assinado ate o alvo; o azimute usa o caminho curto em 0/360 graus."""
    if axis == 0:
        diff = (alvo - pos) % 360
        return diff - 360 if diff > 180 else diff
    return alvo - pos
