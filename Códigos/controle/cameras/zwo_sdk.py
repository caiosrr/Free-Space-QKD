"""Backend persistente para cameras ZWO ASI usando o SDK nativo.

O pacote Python ``zwoasi`` e apenas a ligacao com ``ASICamera2.dll``. A
captura fica em modo de video durante toda a calibracao, evitando o ciclo
StartExposure/ImageReady do ASCOM a cada frame.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np


class ZwoSdkCamera:
    def __init__(self) -> None:
        self.asi: Any | None = None
        self.device: Any | None = None
        self.info: dict[str, Any] | None = None
        self.video_started = False
        self.current_roi: tuple[int, int, int, int] | None = None
        self.exposure_us: int | None = None

    def _require_device(self) -> Any:
        if self.device is None:
            raise RuntimeError("Camera ZWO SDK nao conectada.")
        return self.device

    @staticmethod
    def _load_binding():
        try:
            import zwoasi as asi
        except ImportError as exc:
            raise RuntimeError(
                "Pacote zwoasi ausente. Execute: "
                "python -m pip install -r requirements-zwo.txt"
            ) from exc

        sdk_path = os.environ.get("QKD_ZWO_SDK_PATH", "").strip()
        try:
            if sdk_path:
                library = Path(sdk_path).expanduser().resolve()
                if not library.is_file():
                    raise RuntimeError(
                        f"ASICamera2.dll nao encontrada em QKD_ZWO_SDK_PATH={library}"
                    )
                asi.init(str(library))
            else:
                asi.init()
        except Exception as exc:
            raise RuntimeError(
                "Nao consegui carregar o SDK da ZWO. Instale o ASI Camera SDK e "
                "defina QKD_ZWO_SDK_PATH com o caminho completo de ASICamera2.dll. "
                f"Detalhe: {exc}"
            ) from exc
        return asi

    def connect(self) -> None:
        if self.device is not None:
            return
        self.asi = self._load_binding()
        count = int(self.asi.get_num_cameras())
        if count <= 0:
            raise RuntimeError("Nenhuma camera ZWO ASI encontrada pelo SDK.")
        index = int(os.environ.get("QKD_ZWO_CAMERA_INDEX", "0"))
        if not 0 <= index < count:
            raise RuntimeError(f"Indice ZWO invalido: {index}; cameras encontradas={count}.")

        try:
            self.device = self.asi.Camera(index)
            self.info = dict(self.device.get_camera_property())
            self.reset_roi()
            self.set_gain(float(os.environ.get("QKD_ZWO_GAIN", "100")))
            self._set_exposure_us(
                int(round(float(os.environ.get("QKD_ZWO_EXPOSURE_US", "700"))))
            )
            bandwidth = os.environ.get("QKD_ZWO_USB_BANDWIDTH", "").strip()
            if bandwidth:
                self.device.set_control_value(
                    self.asi.ASI_BANDWIDTHOVERLOAD, int(float(bandwidth)), auto=False
                )
            self._start_video()
            width, height, offset_x, offset_y = self.get_roi()
            name = self.info.get("Name", f"camera {index}")
            print(
                f"Camera ZWO SDK conectada: {name} | {width}x{height} "
                f"em ({offset_x}, {offset_y}) | RAW8 | "
                f"exposicao={self.exposure_us} us"
            )
        except Exception:
            self.disconnect()
            raise

    def _start_video(self) -> None:
        device = self._require_device()
        if not self.video_started:
            device.start_video_capture()
            self.video_started = True

    def _stop_video(self) -> None:
        if self.device is not None and self.video_started:
            try:
                self.device.stop_video_capture()
            finally:
                self.video_started = False

    def get_sensor_size(self) -> tuple[int, int]:
        if self.info is None:
            raise RuntimeError("Camera ZWO SDK nao conectada.")
        return int(self.info["MaxWidth"]), int(self.info["MaxHeight"])

    def get_roi(self) -> tuple[int, int, int, int]:
        device = self._require_device()
        start_x, start_y, width, height = [int(value) for value in device.get_roi()]
        self.current_roi = (width, height, start_x, start_y)
        return self.current_roi

    def set_roi(
        self,
        width: int,
        height: int,
        offset_x: int,
        offset_y: int,
    ) -> tuple[int, int, int, int]:
        device = self._require_device()
        sensor_width, sensor_height = self.get_sensor_size()
        actual_w = max(8, min(int(width), sensor_width))
        actual_h = max(2, min(int(height), sensor_height))
        actual_w -= actual_w % 8
        actual_h -= actual_h % 2
        actual_x = max(0, min(int(offset_x), sensor_width - actual_w))
        actual_y = max(0, min(int(offset_y), sensor_height - actual_h))

        was_running = self.video_started
        self._stop_video()
        try:
            device.set_roi(
                start_x=actual_x,
                start_y=actual_y,
                width=actual_w,
                height=actual_h,
                bins=1,
                image_type=self.asi.ASI_IMG_RAW8,
            )
            self.current_roi = (actual_w, actual_h, actual_x, actual_y)
        finally:
            if was_running:
                self._start_video()
        return self.get_roi()

    def reset_roi(self) -> tuple[int, int, int, int]:
        width, height = self.get_sensor_size()
        width -= width % 8
        height -= height % 2
        return self.set_roi(width, height, 0, 0)

    def set_gain(self, gain: float) -> None:
        device = self._require_device()
        device.set_control_value(self.asi.ASI_GAIN, int(round(gain)), auto=False)

    def _set_exposure_us(self, exposure_us: int) -> None:
        device = self._require_device()
        exposure_us = max(1, int(exposure_us))
        if exposure_us != self.exposure_us:
            device.set_control_value(self.asi.ASI_EXPOSURE, exposure_us, auto=False)
            self.exposure_us = exposure_us

    def capture(self, exposure_seconds: float) -> np.ndarray:
        device = self._require_device()
        self._set_exposure_us(int(round(float(exposure_seconds) * 1e6)))
        self._start_video()
        timeout_floor = int(os.environ.get("QKD_ZWO_TIMEOUT_MS", "1000"))
        timeout_ms = max(timeout_floor, int(2 * self.exposure_us / 1000 + 500))
        frame = np.asarray(device.capture_video_frame(timeout=timeout_ms))
        if frame.ndim != 2:
            raise RuntimeError(f"Frame ZWO inesperado: shape={frame.shape}.")
        return frame

    def disconnect(self) -> None:
        try:
            self._stop_video()
        finally:
            if self.device is not None:
                try:
                    self.device.close()
                except Exception:
                    pass
            self.device = None
            self.info = None
            self.current_roi = None
            self.exposure_us = None


camera = ZwoSdkCamera()
