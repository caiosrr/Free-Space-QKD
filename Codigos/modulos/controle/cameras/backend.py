"""Interface unica para cameras Alpaca, IDS peak e ZWO SDK."""

from __future__ import annotations

import os
import time

import numpy as np


def backend_name() -> str:
    name = os.environ.get("QKD_CAMERA_BACKEND", "alpaca").strip().lower()
    aliases = {"zwo": "zwo_sdk", "asi_sdk": "zwo_sdk"}
    name = aliases.get(name, name)
    if name not in {"alpaca", "ids", "zwo_sdk"}:
        raise ValueError(
            f"Backend de camera invalido: {name!r}. "
            "Use 'alpaca', 'ids' ou 'zwo_sdk'."
        )
    return name


def _ids_camera():
    from modulos.controle.cameras.ids_peak import camera

    return camera


def _zwo_camera():
    from modulos.controle.cameras.zwo_sdk import camera

    return camera


def direct_camera():
    """Retorna a camera com ROI nativa usada nas calibracoes continuas."""
    name = backend_name()
    if name == "ids":
        return _ids_camera()
    if name == "zwo_sdk":
        return _zwo_camera()
    raise RuntimeError("ROI nativa requer backend 'ids' ou 'zwo_sdk'.")


def connect_camera() -> None:
    if backend_name() in {"ids", "zwo_sdk"}:
        direct_camera().connect()
        return
    from modulos.controle.cameras.alpaca import connect

    connect()


def disconnect_camera() -> None:
    if backend_name() in {"ids", "zwo_sdk"}:
        direct_camera().disconnect()
        return
    from modulos.controle.cameras.alpaca import disconnect

    disconnect()


def set_gain(gain: float) -> None:
    if backend_name() == "ids":
        digital_gain = float(os.environ.get("QKD_IDS_DIGITAL_GAIN", str(gain)))
        _ids_camera().set_gain(float(gain), digital_gain)
        return
    if backend_name() == "zwo_sdk":
        _zwo_camera().set_gain(float(gain))
        return
    from modulos.controle.cameras.alpaca import set_gain as alpaca_set_gain

    alpaca_set_gain(int(gain))


def capture_raw_frame(exposure_seconds: float, light: bool = True) -> np.ndarray:
    if backend_name() in {"ids", "zwo_sdk"}:
        return direct_camera().capture(float(exposure_seconds))

    from modulos.controle.cameras.alpaca import (
        fetch_image_array,
        record_capture_time,
        start_exposure,
        wait_until_image_ready,
    )

    capture_started = time.perf_counter()
    start_exposure(float(exposure_seconds), light=light)
    wait_until_image_ready()
    frame = fetch_image_array()
    record_capture_time(time.perf_counter() - capture_started)
    return frame
