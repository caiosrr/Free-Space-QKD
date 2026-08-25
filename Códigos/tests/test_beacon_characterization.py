import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


PROGRAM_PATH = (
    Path(__file__).resolve().parents[1]
    / "Link UFF"
    / "caracterizacao_beacon"
    / "caracterizar_beacon_ids.py"
)
SPEC = importlib.util.spec_from_file_location("beacon_characterization", PROGRAM_PATH)
beacon = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = beacon
SPEC.loader.exec_module(beacon)


class BeaconCharacterizationTests(unittest.TestCase):
    def test_spot_shape_recovers_gaussian_width(self):
        yy, xx = np.indices((101, 121), dtype=float)
        sigma_x = 7.0
        sigma_y = 4.0
        frame = 12.0 + 200.0 * np.exp(
            -0.5 * (((xx - 60.0) / sigma_x) ** 2 + ((yy - 50.0) / sigma_y) ** 2)
        )
        measured = beacon.measure_spot_shape(
            frame, 60.0, 50.0, pedestal=12.0, threshold_percent=0.02, radius_px=35
        )
        self.assertIsNotNone(measured)
        # O threshold de 2% remove as caudas e reduz levemente o sigma medido.
        self.assertAlmostEqual(measured["sigma_x_px"], sigma_x, delta=0.35)
        self.assertAlmostEqual(measured["sigma_y_px"], sigma_y, delta=0.25)

    def test_event_detector_marks_loss_recovery_and_jump(self):
        detector = beacon.EventDetector(baseline_frames=2, cooldown_s=0.0, position_event_px=5.0)
        self.assertEqual(
            detector.update(
                0.0,
                valid=True,
                touches_border=False,
                x_px=10.0,
                y_px=10.0,
                raw_total=100.0,
                sigma_major_px=5.0,
            ),
            [],
        )
        lost = detector.update(
            1.0,
            valid=False,
            touches_border=False,
            x_px=None,
            y_px=None,
            raw_total=None,
            sigma_major_px=None,
        )
        self.assertIn("signal_lost", lost)
        recovered = detector.update(
            2.0,
            valid=True,
            touches_border=False,
            x_px=20.0,
            y_px=10.0,
            raw_total=100.0,
            sigma_major_px=5.0,
        )
        self.assertIn("signal_recovered", recovered)
        self.assertIn("sudden_position_jump", recovered)

    def test_rotated_roi_coordinates_return_to_display_sensor(self):
        sensor_w, sensor_h = 1000, 800
        actual_roi = (200, 160, 304, 202)
        target_display = (600.0, 500.0)
        local = beacon.target_in_actual_roi(
            *target_display, actual_roi, sensor_w, sensor_h, True
        )
        restored = beacon.roi_to_displayed_sensor(
            *local, actual_roi, sensor_w, sensor_h, True
        )
        self.assertAlmostEqual(restored[0], target_display[0])
        self.assertAlmostEqual(restored[1], target_display[1])

    def test_event_image_budget_has_hard_limits(self):
        self.assertTrue(beacon.event_images_allowed(0, 0))
        self.assertFalse(beacon.event_images_allowed(beacon.MAX_EVENT_IMAGE_SETS, 0))
        self.assertFalse(
            beacon.event_images_allowed(
                0,
                int(beacon.MAX_EVENT_STORAGE_MB * 1024 * 1024),
            )
        )


if __name__ == "__main__":
    unittest.main()
