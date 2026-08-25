import sys
import unittest
from pathlib import Path

import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[1]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from controle.cameras.zwo_sdk import ZwoSdkCamera


class _FakeAsi:
    ASI_IMG_RAW8 = 0
    ASI_GAIN = 1
    ASI_EXPOSURE = 2


class _FakeDevice:
    def __init__(self):
        self.roi = [0, 0, 640, 480]
        self.controls = []
        self.running = False

    def get_roi(self):
        return self.roi

    def set_roi(self, *, start_x, start_y, width, height, bins, image_type):
        self.roi = [start_x, start_y, width, height]

    def start_video_capture(self):
        self.running = True

    def stop_video_capture(self):
        self.running = False

    def set_control_value(self, control, value, auto=False):
        self.controls.append((control, value, auto))

    def capture_video_frame(self, timeout):
        return np.full((self.roi[3], self.roi[2]), 7, dtype=np.uint8)


class ZwoSdkCameraTests(unittest.TestCase):
    def setUp(self):
        self.camera = ZwoSdkCamera()
        self.camera.asi = _FakeAsi()
        self.camera.device = _FakeDevice()
        self.camera.info = {"MaxWidth": 640, "MaxHeight": 480}
        self.camera.video_started = True

    def test_roi_is_clamped_and_rounded_for_sdk_requirements(self):
        roi = self.camera.set_roi(259, 195, 500, 400)

        self.assertEqual(roi, (256, 194, 384, 286))
        self.assertTrue(self.camera.device.running)

    def test_video_capture_uses_microsecond_exposure_and_returns_2d_frame(self):
        frame = self.camera.capture(0.0008)

        self.assertEqual(frame.shape, (480, 640))
        self.assertIn((_FakeAsi.ASI_EXPOSURE, 800, False), self.camera.device.controls)


if __name__ == "__main__":
    unittest.main()
