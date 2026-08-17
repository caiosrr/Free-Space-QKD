"""Teste isolado de aquisicao para a IDS U3-3680XCP via IDS peak.

Nao usa ASCOM e nao envia nenhum comando ao mount.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from ids_peak import ids_peak
except ImportError as exc:
    raise SystemExit(
        "Nao encontrei o pacote ids_peak. Ative o .venv e execute: "
        "python -m pip install ids_peak ids_peak_ipl"
    ) from exc


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "resultados" / "teste_ids.png"
DEFAULT_EXPOSURE_US = 7276.0
DEFAULT_FPS = 20.0
DEFAULT_ANALOG_GAIN = 1.0
DEFAULT_DIGITAL_GAIN = 1.0
PREFERRED_8BIT_FORMATS = (
    "Mono8",
    "BayerGR8",
    "BayerRG8",
    "BayerGB8",
    "BayerBG8",
)


def find_node(nodemap: Any, name: str) -> Any:
    try:
        return nodemap.FindNode(name)
    except Exception as exc:
        raise RuntimeError(f"A camera nao disponibilizou o parametro {name!r}.") from exc


def clamp_float_node(node: Any, requested: float) -> float:
    minimum = float(node.Minimum())
    maximum = float(node.Maximum())
    value = float(np.clip(requested, minimum, maximum))
    try:
        increment = float(node.Increment())
    except Exception:
        increment = 0.0
    if increment > 0:
        value = minimum + round((value - minimum) / increment) * increment
        value = float(np.clip(value, minimum, maximum))
    node.SetValue(value)
    return float(node.Value())


def configure_pixel_format(nodemap: Any) -> str:
    node = find_node(nodemap, "PixelFormat")
    available = [entry.StringValue() for entry in node.AvailableEntries()]
    selected = next((name for name in PREFERRED_8BIT_FORMATS if name in available), None)
    if selected is None:
        raise RuntimeError(
            "Nao encontrei formato Mono8/Bayer8. Formatos disponiveis: "
            + ", ".join(available)
        )
    node.SetCurrentEntry(selected)
    return selected


def disable_auto_feature(nodemap: Any, name: str) -> None:
    try:
        node = find_node(nodemap, name)
        available = [entry.StringValue() for entry in node.AvailableEntries()]
        if "Off" in available:
            node.SetCurrentEntry("Off")
            print(f"{name}: Off")
    except Exception:
        print(f"Aviso: nao consegui desligar {name}; continuando.")


def configure_frame_rate(nodemap: Any, requested_fps: float) -> float:
    try:
        enable_node = find_node(nodemap, "AcquisitionFrameRateEnable")
        if enable_node.IsWriteable():
            enable_node.SetValue(True)
    except Exception:
        pass
    return clamp_float_node(find_node(nodemap, "AcquisitionFrameRate"), requested_fps)


def configure_gain(nodemap: Any, selector_name: str, requested: float) -> float | None:
    selector = find_node(nodemap, "GainSelector")
    available = [entry.StringValue() for entry in selector.AvailableEntries()]
    if selector_name not in available:
        print(
            f"Aviso: GainSelector={selector_name} nao esta disponivel. "
            f"Opcoes: {', '.join(available)}"
        )
        return None
    selector.SetCurrentEntry(selector_name)
    return clamp_float_node(find_node(nodemap, "Gain"), requested)


def buffer_to_numpy(buffer: Any) -> np.ndarray:
    """Copia um buffer 8-bit antes de devolve-lo para a fila da camera."""
    width = int(buffer.Width())
    height = int(buffer.Height())
    image_bytes = width * height
    if int(buffer.Size()) < image_bytes:
        raise RuntimeError(
            f"Buffer menor que a imagem: {buffer.Size()} < {image_bytes} bytes."
        )
    address = int(buffer.BasePtr())
    raw_type = ctypes.c_uint8 * image_bytes
    return np.ctypeslib.as_array(raw_type.from_address(address)).reshape(height, width).copy()


def print_devices(devices: Any) -> None:
    print(f"Dispositivos IDS/GenTL encontrados: {len(devices)}")
    for index, descriptor in enumerate(devices):
        status = "disponivel" if descriptor.IsOpenable(ids_peak.DeviceAccessType_Control) else "ocupado"
        print(f"  [{index}] {descriptor.DisplayName()} | {descriptor.ModelName()} | {status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Captura frames da IDS U3-3680XCP sem usar ASCOM ou mover o mount."
    )
    parser.add_argument("--device", type=int, default=0, help="Indice da camera (padrao: 0).")
    parser.add_argument("--frames", type=int, default=50, help="Quantidade de frames (padrao: 50).")
    parser.add_argument(
        "--exposure-us",
        type=float,
        default=DEFAULT_EXPOSURE_US,
        help=f"Exposicao em microssegundos (padrao: {DEFAULT_EXPOSURE_US:g}).",
    )
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="FPS solicitado (padrao: 20).")
    parser.add_argument(
        "--analog-gain",
        type=float,
        default=DEFAULT_ANALOG_GAIN,
        help="Ganho AnalogAll (padrao: 1).",
    )
    parser.add_argument(
        "--digital-gain",
        type=float,
        default=DEFAULT_DIGITAL_GAIN,
        help="Ganho DigitalAll (padrao: 1).",
    )
    parser.add_argument("--timeout-ms", type=int, default=5000, help="Timeout por frame.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="PNG de saida.")
    parser.add_argument("--list-only", action="store_true", help="Somente lista as cameras.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.frames <= 0:
        raise SystemExit("--frames deve ser maior que zero.")

    ids_peak.Library.Initialize()
    data_stream = None
    nodemap = None
    acquisition_started = False
    params_locked = False

    try:
        manager = ids_peak.DeviceManager.Instance()
        manager.Update()
        devices = manager.Devices()
        print_devices(devices)

        if args.list_only:
            return 0
        if len(devices) == 0:
            raise RuntimeError(
                "Nenhuma camera foi encontrada. Feche o IDS peak Cockpit e confira o cabo USB 3."
            )
        if args.device < 0 or args.device >= len(devices):
            raise RuntimeError(f"Indice --device {args.device} invalido.")

        descriptor = devices[args.device]
        if not descriptor.IsOpenable(ids_peak.DeviceAccessType_Control):
            raise RuntimeError(
                "A camera esta ocupada. Feche o IDS peak Cockpit ou outro programa que a esteja usando."
            )

        device = descriptor.OpenDevice(ids_peak.DeviceAccessType_Control)
        nodemap = device.RemoteDevice().NodeMaps()[0]
        streams = device.DataStreams()
        if len(streams) == 0:
            raise RuntimeError("A camera nao apresentou nenhum DataStream.")
        data_stream = streams[0].OpenDataStream()

        try:
            find_node(nodemap, "AcquisitionMode").SetCurrentEntry("Continuous")
        except Exception:
            pass
        try:
            find_node(nodemap, "TriggerMode").SetCurrentEntry("Off")
        except Exception:
            pass

        disable_auto_feature(nodemap, "ExposureAuto")
        disable_auto_feature(nodemap, "GainAuto")

        pixel_format = configure_pixel_format(nodemap)
        frame_rate = configure_frame_rate(nodemap, args.fps)
        exposure = clamp_float_node(find_node(nodemap, "ExposureTime"), args.exposure_us)
        print(f"Formato: {pixel_format}")
        print(f"Frame rate configurado: {frame_rate:.3f} fps")
        print(f"Exposicao aplicada: {exposure:.3f} us")

        analog_gain = configure_gain(nodemap, "AnalogAll", args.analog_gain)
        if analog_gain is not None:
            print(f"Ganho analogico aplicado: {analog_gain:.3f}")
        digital_gain = configure_gain(nodemap, "DigitalAll", args.digital_gain)
        if digital_gain is not None:
            print(f"Ganho digital aplicado: {digital_gain:.3f}")

        width = int(find_node(nodemap, "Width").Value())
        height = int(find_node(nodemap, "Height").Value())
        payload_size = int(find_node(nodemap, "PayloadSize").Value())
        print(f"Imagem: {width} x {height} px | payload: {payload_size} bytes")

        for _ in range(data_stream.NumBuffersAnnouncedMinRequired()):
            data_stream.QueueBuffer(data_stream.AllocAndAnnounceBuffer(payload_size))

        try:
            find_node(nodemap, "TLParamsLocked").SetValue(1)
            params_locked = True
        except Exception:
            pass

        data_stream.StartAcquisition()
        start_node = find_node(nodemap, "AcquisitionStart")
        start_node.Execute()
        start_node.WaitUntilDone()
        acquisition_started = True

        last_frame = None
        complete_frames = 0
        incomplete_frames = 0
        started_at = time.perf_counter()

        for _ in range(args.frames):
            buffer = data_stream.WaitForFinishedBuffer(ids_peak.Timeout(args.timeout_ms))
            try:
                if buffer.IsIncomplete():
                    incomplete_frames += 1
                    continue
                last_frame = buffer_to_numpy(buffer)
                complete_frames += 1
            finally:
                data_stream.QueueBuffer(buffer)

        elapsed = time.perf_counter() - started_at
        fps = complete_frames / elapsed if elapsed > 0 else 0.0
        print(
            f"Captura concluida: {complete_frames} completos, {incomplete_frames} incompletos, "
            f"{fps:.2f} fps medidos."
        )

        if last_frame is None:
            raise RuntimeError("Nenhum frame completo foi recebido.")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.output), last_frame):
            raise RuntimeError(f"Nao consegui salvar {args.output}.")
        print(
            f"Ultimo frame: min={int(last_frame.min())}, max={int(last_frame.max())}, "
            f"media={float(last_frame.mean()):.2f}"
        )
        print(f"Imagem salva em: {args.output.resolve()}")
        return 0

    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    finally:
        if acquisition_started and nodemap is not None:
            try:
                find_node(nodemap, "AcquisitionStop").Execute()
            except Exception:
                pass
        if data_stream is not None:
            try:
                if data_stream.IsGrabbing():
                    data_stream.StopAcquisition(ids_peak.AcquisitionStopMode_Default)
            except Exception:
                pass
            try:
                data_stream.Flush(ids_peak.DataStreamFlushMode_DiscardAll)
                for announced in data_stream.AnnouncedBuffers():
                    data_stream.RevokeBuffer(announced)
            except Exception:
                pass
        if params_locked and nodemap is not None:
            try:
                find_node(nodemap, "TLParamsLocked").SetValue(0)
            except Exception:
                pass
        ids_peak.Library.Close()


if __name__ == "__main__":
    raise SystemExit(main())
