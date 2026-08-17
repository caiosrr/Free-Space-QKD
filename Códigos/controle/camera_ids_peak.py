"""Backend persistente IDS peak para a U3-3680XCP-NIR.

A camera permanece em aquisicao continua entre chamadas, evitando reabrir o
USB e realocar buffers a cada frame.
"""

from __future__ import annotations

import ctypes
import os
from typing import Any

import numpy as np


DEFAULT_FPS = float(os.environ.get("QKD_IDS_FPS", "20"))
DEFAULT_EXPOSURE_US = float(os.environ.get("QKD_IDS_EXPOSURE_US", "7276"))
DEFAULT_ANALOG_GAIN = float(os.environ.get("QKD_IDS_ANALOG_GAIN", "1"))
DEFAULT_DIGITAL_GAIN = float(os.environ.get("QKD_IDS_DIGITAL_GAIN", "1"))
DEFAULT_DEVICE_INDEX = int(os.environ.get("QKD_IDS_DEVICE", "0"))
CAPTURE_TIMEOUT_MS = int(os.environ.get("QKD_IDS_TIMEOUT_MS", "5000"))


class IDSPeakCamera:
    def __init__(self) -> None:
        self.ids_peak = None
        self.device = None
        self.nodemap = None
        self.data_stream = None
        self.library_initialized = False
        self.acquisition_started = False
        self.params_locked = False
        self.pixel_format = None

    def _node(self, name: str) -> Any:
        if self.nodemap is None:
            raise RuntimeError("Camera IDS nao conectada.")
        try:
            return self.nodemap.FindNode(name)
        except Exception as exc:
            raise RuntimeError(f"Parametro IDS ausente: {name}.") from exc

    @staticmethod
    def _available_entries(node: Any) -> list[str]:
        return [entry.StringValue() for entry in node.AvailableEntries()]

    @staticmethod
    def _set_float(node: Any, requested: float) -> float:
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

    def _disable_auto(self, name: str) -> None:
        try:
            node = self._node(name)
            if "Off" in self._available_entries(node):
                node.SetCurrentEntry("Off")
        except Exception:
            pass

    def _set_full_sensor(self) -> None:
        for name in ("OffsetX", "OffsetY"):
            try:
                self._node(name).SetValue(self._node(name).Minimum())
            except Exception:
                pass
        for name in ("Width", "Height"):
            try:
                node = self._node(name)
                node.SetValue(node.Maximum())
            except Exception:
                pass

    def _set_pixel_format(self) -> str:
        node = self._node("PixelFormat")
        available = self._available_entries(node)
        preferred = ("Mono8", "BayerGR8", "BayerRG8", "BayerGB8", "BayerBG8")
        selected = next((value for value in preferred if value in available), None)
        if selected is None:
            raise RuntimeError(
                "A camera IDS nao ofereceu formato 8-bit compativel. "
                f"Disponiveis: {', '.join(available)}"
            )
        node.SetCurrentEntry(selected)
        return selected

    def _set_gain_selector(self, selector_name: str, requested: float) -> float | None:
        selector = self._node("GainSelector")
        if selector_name not in self._available_entries(selector):
            return None
        selector.SetCurrentEntry(selector_name)
        return self._set_float(self._node("Gain"), requested)

    def connect(self) -> None:
        if self.acquisition_started:
            return
        try:
            from ids_peak import ids_peak
        except ImportError as exc:
            raise RuntimeError(
                "Pacote ids_peak ausente. Execute: python -m pip install ids_peak ids_peak_ipl"
            ) from exc

        self.ids_peak = ids_peak
        ids_peak.Library.Initialize()
        self.library_initialized = True

        try:
            manager = ids_peak.DeviceManager.Instance()
            manager.Update()
            devices = manager.Devices()
            if len(devices) == 0:
                raise RuntimeError("Nenhuma camera IDS/GenTL encontrada.")
            if DEFAULT_DEVICE_INDEX < 0 or DEFAULT_DEVICE_INDEX >= len(devices):
                raise RuntimeError(f"Indice de camera IDS invalido: {DEFAULT_DEVICE_INDEX}.")

            descriptor = devices[DEFAULT_DEVICE_INDEX]
            if not descriptor.IsOpenable(ids_peak.DeviceAccessType_Control):
                raise RuntimeError(
                    "Camera IDS ocupada. Feche o IDS peak Cockpit e outros programas de camera."
                )

            self.device = descriptor.OpenDevice(ids_peak.DeviceAccessType_Control)
            self.nodemap = self.device.RemoteDevice().NodeMaps()[0]
            streams = self.device.DataStreams()
            if len(streams) == 0:
                raise RuntimeError("A camera IDS nao apresentou DataStream.")
            self.data_stream = streams[0].OpenDataStream()

            try:
                self._node("AcquisitionMode").SetCurrentEntry("Continuous")
            except Exception:
                pass
            try:
                self._node("TriggerMode").SetCurrentEntry("Off")
            except Exception:
                pass
            self._disable_auto("ExposureAuto")
            self._disable_auto("GainAuto")
            self._set_full_sensor()
            self.pixel_format = self._set_pixel_format()

            try:
                enable = self._node("AcquisitionFrameRateEnable")
                if enable.IsWriteable():
                    enable.SetValue(True)
            except Exception:
                pass
            fps = self._set_float(self._node("AcquisitionFrameRate"), DEFAULT_FPS)
            exposure = self._set_float(self._node("ExposureTime"), DEFAULT_EXPOSURE_US)
            analog = self._set_gain_selector("AnalogAll", DEFAULT_ANALOG_GAIN)
            digital = self._set_gain_selector("DigitalAll", DEFAULT_DIGITAL_GAIN)

            width = int(self._node("Width").Value())
            height = int(self._node("Height").Value())
            payload_size = int(self._node("PayloadSize").Value())
            for _ in range(self.data_stream.NumBuffersAnnouncedMinRequired()):
                buffer = self.data_stream.AllocAndAnnounceBuffer(payload_size)
                self.data_stream.QueueBuffer(buffer)

            try:
                self._node("TLParamsLocked").SetValue(1)
                self.params_locked = True
            except Exception:
                pass

            self.data_stream.StartAcquisition()
            start = self._node("AcquisitionStart")
            start.Execute()
            start.WaitUntilDone()
            self.acquisition_started = True

            print(
                f"Camera IDS conectada: {descriptor.DisplayName()} | {width}x{height} | "
                f"{self.pixel_format} | {fps:.3f} fps | exposicao={exposure:.3f} us"
            )
            if analog is not None:
                print(f"Ganho IDS AnalogAll={analog:.3f}")
            if digital is not None:
                print(f"Ganho IDS DigitalAll={digital:.3f}")
        except Exception:
            self.disconnect()
            raise

    def set_gain(self, analog: float, digital: float) -> None:
        if self.nodemap is None:
            raise RuntimeError("Conecte a camera IDS antes de ajustar o ganho.")
        if (
            self.acquisition_started
            and abs(analog - DEFAULT_ANALOG_GAIN) < 1e-9
            and abs(digital - DEFAULT_DIGITAL_GAIN) < 1e-9
        ):
            print(
                f"Ganhos IDS ja configurados: AnalogAll={analog:.3f}, "
                f"DigitalAll={digital:.3f}."
            )
            return
        analog_set = self._set_gain_selector("AnalogAll", analog)
        digital_set = self._set_gain_selector("DigitalAll", digital)
        if analog_set is not None:
            print(f"Ajustando ganho IDS analogico para {analog_set:.3f}...")
        if digital_set is not None:
            print(f"Ajustando ganho IDS digital para {digital_set:.3f}...")

    def _buffer_to_numpy(self, buffer: Any) -> np.ndarray:
        width = int(buffer.Width())
        height = int(buffer.Height())
        byte_count = width * height
        if int(buffer.Size()) < byte_count:
            raise RuntimeError(f"Buffer IDS menor que o frame: {buffer.Size()} < {byte_count}.")
        raw_type = ctypes.c_uint8 * byte_count
        address = int(buffer.BasePtr())
        return np.ctypeslib.as_array(raw_type.from_address(address)).reshape(height, width).copy()

    def capture(self, exposure_seconds: float) -> np.ndarray:
        if not self.acquisition_started or self.data_stream is None:
            raise RuntimeError("Camera IDS nao conectada.")

        requested_us = float(exposure_seconds) * 1e6
        try:
            current_us = float(self._node("ExposureTime").Value())
            if abs(current_us - requested_us) > 0.5:
                self._set_float(self._node("ExposureTime"), requested_us)
        except Exception:
            pass

        buffer = self.data_stream.WaitForFinishedBuffer(self.ids_peak.Timeout(CAPTURE_TIMEOUT_MS))
        try:
            if buffer.IsIncomplete():
                raise RuntimeError("A camera IDS entregou um frame incompleto.")
            return self._buffer_to_numpy(buffer)
        finally:
            self.data_stream.QueueBuffer(buffer)

    def disconnect(self) -> None:
        if self.ids_peak is None and not self.library_initialized:
            return
        if self.acquisition_started and self.nodemap is not None:
            try:
                stop = self._node("AcquisitionStop")
                stop.Execute()
                stop.WaitUntilDone()
            except Exception:
                pass
        if self.data_stream is not None:
            try:
                if self.data_stream.IsGrabbing():
                    self.data_stream.StopAcquisition(self.ids_peak.AcquisitionStopMode_Default)
            except Exception:
                pass
            try:
                self.data_stream.Flush(self.ids_peak.DataStreamFlushMode_DiscardAll)
                for buffer in self.data_stream.AnnouncedBuffers():
                    self.data_stream.RevokeBuffer(buffer)
            except Exception:
                pass
        if self.params_locked and self.nodemap is not None:
            try:
                self._node("TLParamsLocked").SetValue(0)
            except Exception:
                pass

        self.acquisition_started = False
        self.params_locked = False
        self.data_stream = None
        self.nodemap = None
        self.device = None
        if self.library_initialized:
            try:
                self.ids_peak.Library.Close()
            finally:
                self.library_initialized = False
        self.ids_peak = None
        print("Camera IDS desconectada.")


camera = IDSPeakCamera()
