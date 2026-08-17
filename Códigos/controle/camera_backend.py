"""Interface minima de camera usada pelos fluxos de foco e calibracao.

O backend padrao continua sendo Alpaca. Os executaveis da pasta ``Link UFF``
selecionam IDS antes de importar os programas existentes.
"""

from __future__ import annotations

import os

import numpy as np


def backend_name() -> str:
    name = os.environ.get("QKD_CAMERA_BACKEND", "alpaca").strip().lower()
    if name not in {"alpaca", "ids"}:
        raise ValueError(f"Backend de camera invalido: {name!r}. Use 'alpaca' ou 'ids'.")
    return name


def _ids_camera():
    from controle.camera_ids_peak import camera

    return camera


def connect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().connect()
        return
    from controle.Center_of_Mass import connect_camera as alpaca_connect

    alpaca_connect()


def disconnect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().disconnect()
        return
    from controle.Center_of_Mass import disconnect_camera as alpaca_disconnect

    alpaca_disconnect()


def set_gain(gain: float) -> None:
    if backend_name() == "ids":
        digital_gain = float(os.environ.get("QKD_IDS_DIGITAL_GAIN", str(gain)))
        _ids_camera().set_gain(float(gain), digital_gain)
        return
    from controle.Center_of_Mass import set_gain as alpaca_set_gain

    alpaca_set_gain(int(gain))


def capture_raw_frame(exposure_seconds: float, light: bool = True) -> np.ndarray:
    if backend_name() == "ids":
        return _ids_camera().capture(float(exposure_seconds))

    from controle.Center_of_Mass import (
        fetch_image_array,
        start_exposure,
        wait_until_image_ready,
    )

    start_exposure(float(exposure_seconds), light=light)
    wait_until_image_ready()
    return fetch_image_array()
