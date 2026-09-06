"""Controle e transferencia eficiente da camera ASI pelo ASCOM Alpaca.

Tenta ImageBytes (binario) primeiro. Se o Remote Server/driver ainda devolver
JSON, memoriza isso e usa o modo compativel nas capturas seguintes.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from alpaca.camera import Camera
from alpaca.exceptions import InvalidValueException

from modulos.configuracoes.alpaca import ALPACA_ADDRESS, CAMERA_DEVICE_NUMBER
from modulos.controle.ascom import camera_device


IMAGE_READY_POLL_S = 0.001
IMAGE_READY_SPIN_POLLS = 3

_camera = Camera(ALPACA_ADDRESS, CAMERA_DEVICE_NUMBER)
call = camera_device.call
_lock = threading.Lock()
_imagebytes_supported: bool | None = None
_stats = {
    "frames": 0,
    "imagebytes_frames": 0,
    "json_frames": 0,
    "transfer_seconds": 0.0,
    "transfer_max_seconds": 0.0,
    "capture_seconds": 0.0,
}


def connect() -> None:
    print("Conectando a camera ASI/Alpaca...")
    call("PUT", "connected", data={"Connected": True})


def disconnect() -> None:
    print("Desconectando a camera ASI/Alpaca...")
    call("PUT", "connected", data={"Connected": False})


def set_gain(gain: int) -> None:
    call("PUT", "gain", data={"Gain": int(gain)})


def start_exposure(duration_seconds: float, light: bool = True) -> None:
    call(
        "PUT",
        "startexposure",
        data={"Duration": float(duration_seconds), "Light": bool(light)},
    )


def wait_until_image_ready(
    poll_interval: float = IMAGE_READY_POLL_S,
    timeout: float = 5.0,
) -> None:
    """Consulta ImageReady sem dormir nas primeiras tentativas.

    Em exposicoes curtas o frame costuma ficar pronto antes do primeiro sleep;
    gastar alguns polls seguidos reduz a latencia de cada captura.
    """
    deadline = time.time() + timeout
    spin_polls = IMAGE_READY_SPIN_POLLS
    while time.time() < deadline:
        if bool(call("GET", "imageready")):
            return
        if spin_polls > 0:
            spin_polls -= 1
            continue
        time.sleep(poll_interval)
    raise TimeoutError("Tempo limite esperando ImageReady = True")


def _shape_from_metadata(info) -> tuple[int, ...]:
    rank = int(info.Rank)
    dimensions = [int(info.Dimension1), int(info.Dimension2)]
    if rank == 3:
        dimensions.append(int(info.Dimension3))
    if rank not in {2, 3} or any(size <= 0 for size in dimensions):
        raise RuntimeError(f"Metadados ImageBytes invalidos: rank={rank}, shape={dimensions}")
    return tuple(dimensions)


def fetch_image_array() -> np.ndarray:
    """Busca um frame preservando o mesmo formato usado pelo retorno JSON."""
    global _imagebytes_supported

    started = time.perf_counter()
    mode = "json"
    with _lock:
        if _imagebytes_supported is not False:
            try:
                raw = _camera.ImageArrayRaw
                shape = _shape_from_metadata(_camera.ImageArrayInfo)
                expected_size = int(np.prod(shape))
                if len(raw) != expected_size:
                    raise RuntimeError(
                        f"ImageBytes incompleto: {len(raw)} valores; esperados {expected_size}."
                    )
                frame = np.asarray(raw).reshape(shape)
                _imagebytes_supported = True
                mode = "imagebytes"
            except InvalidValueException as exc:
                if "application/imagebytes" not in str(exc).lower():
                    raise
                _imagebytes_supported = False

        if _imagebytes_supported is False:
            frame = np.asarray(_camera.ImageArray)

        elapsed = time.perf_counter() - started
        _stats["frames"] += 1
        _stats[f"{mode}_frames"] += 1
        _stats["transfer_seconds"] += elapsed
        _stats["transfer_max_seconds"] = max(_stats["transfer_max_seconds"], elapsed)
        return frame


def record_capture_time(seconds: float) -> None:
    with _lock:
        _stats["capture_seconds"] += max(0.0, float(seconds))


def reset_performance_stats() -> None:
    with _lock:
        for key in _stats:
            _stats[key] = 0 if key.endswith("frames") or key == "frames" else 0.0


def get_performance_stats() -> dict:
    with _lock:
        result = dict(_stats)
    frames = int(result["frames"])
    result["transfer_mean_seconds"] = (
        float(result["transfer_seconds"]) / frames if frames else 0.0
    )
    result["capture_mean_seconds"] = (
        float(result["capture_seconds"]) / frames if frames else 0.0
    )
    return result


def print_performance_summary() -> None:
    stats = get_performance_stats()
    if not stats["frames"]:
        return
    mode = "ImageBytes/binario" if stats["imagebytes_frames"] else "JSON/compatibilidade"
    print(
        "Desempenho ASI: "
        f"{stats['frames']} frames, modo={mode}, "
        f"transferencia media={stats['transfer_mean_seconds'] * 1000:.1f} ms, "
        f"captura completa media={stats['capture_mean_seconds'] * 1000:.1f} ms."
    )
