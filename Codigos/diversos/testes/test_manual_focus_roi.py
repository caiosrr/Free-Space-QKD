import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.visao import detector_ilhas as focus


class ManualFocusRoiTests(unittest.TestCase):
    def setUp(self):
        self.previous_raw_frame = focus.LAST_RAW_FRAME
        self.previous_focus_mode = focus.FOCUS_MODE
        focus.LAST_RAW_FRAME = None
        focus.reset_focus_lock()

    def tearDown(self):
        focus.LAST_RAW_FRAME = self.previous_raw_frame
        focus.FOCUS_MODE = self.previous_focus_mode
        focus.reset_focus_lock()

    def test_local_roi_ignores_brighter_region_and_keeps_absolute_coordinates(self):
        frame = np.zeros((160, 300), dtype=np.float32)
        cv2.rectangle(frame, (10, 15), (100, 145), 255.0, -1)
        cv2.circle(frame, (225, 90), 5, 55.0, -1)

        global_candidates = focus._find_focus_candidates(frame, 0.25)
        self.assertFalse(
            any(np.hypot(item["x_cm"] - 225, item["y_cm"] - 90) < 5 for item in global_candidates)
        )

        local_candidates = focus._find_focus_candidates(
            frame,
            0.25,
            search_roi=(195, 60, 60, 60),
        )
        self.assertTrue(local_candidates)
        selected = min(
            local_candidates,
            key=lambda item: np.hypot(item["x_cm"] - 225, item["y_cm"] - 90),
        )
        self.assertAlmostEqual(selected["x_cm"], 225.0, delta=1.0)
        self.assertAlmostEqual(selected["y_cm"], 90.0, delta=1.0)
        self.assertTrue(all(195 <= item["x_cm"] < 255 for item in local_candidates))

    def test_search_roi_is_clamped_to_image(self):
        self.assertEqual(
            focus._clamp_search_roi((-20, 80, 80, 50), 100, 100),
            (0, 80, 60, 20),
        )

    def test_manual_threshold_is_saved_and_reused_by_focus_lock(self):
        candidate = {
            "x_cm": 100.0,
            "y_cm": 80.0,
            "raw_peak": 200.0,
            "raw_total": 1500.0,
            "area": 20,
            "bbox_w": 5,
            "bbox_h": 4,
            "compactness": 0.8,
            "toca_borda": False,
        }
        signature = focus._lock_manual_candidate(candidate, 80.0, 0.45)
        self.assertEqual(signature["version"], 2)
        self.assertAlmostEqual(signature["threshold_percent"], 0.45)

        focus.reset_focus_lock()
        self.assertTrue(focus.initialize_focus_lock(signature, 100.0, 80.0))
        focus.FOCUS_MODE = "dual"
        with mock.patch.object(focus, "_centro_foco_principal", return_value=None) as detector:
            focus.centro_massa(np.zeros((20, 20), dtype=np.uint8))
        self.assertAlmostEqual(detector.call_args.args[1], 0.45)

    def test_center_anchor_rejects_similar_light_at_roi_border(self):
        reference = {
            "x_cm": 96.0,
            "y_cm": 96.0,
            "raw_peak": 200.0,
            "raw_total": 1500.0,
            "area": 20,
            "bbox_w": 5,
            "bbox_h": 4,
            "compactness": 0.8,
            "toca_borda": False,
        }
        signature = focus._lock_manual_candidate(reference, 86.0, 0.45)
        self.assertTrue(focus.initialize_focus_lock(signature, 96.0, 96.0))
        self.assertTrue(focus.set_focus_expected_position(96.0, 96.0, 55.0))

        wrong_border_light = dict(reference, x_cm=115.0, y_cm=186.0, toca_borda=True)
        self.assertIsNone(focus._select_focus_candidate([wrong_border_light]))

        correct_light = dict(reference, x_cm=104.0, y_cm=101.0)
        self.assertIs(focus._select_focus_candidate([correct_light]), correct_light)

    def test_tracker_jump_override_rejects_distant_wall(self):
        reference = {
            "x_cm": 104.0,
            "y_cm": 130.0,
            "raw_peak": 40.0,
            "raw_total": 14000.0,
            "area": 560,
            "bbox_w": 28,
            "bbox_h": 31,
            "compactness": 0.84,
            "toca_borda": False,
        }
        signature = focus._lock_manual_candidate(reference, 118.0, 0.45)
        self.assertTrue(
            focus.initialize_focus_lock(
                signature,
                104.0,
                130.0,
                max_jump_px=45.0,
            )
        )

        wall = dict(reference, x_cm=134.0, y_cm=245.0, toca_borda=True)
        self.assertIsNone(focus._select_focus_candidate([wall]))

        next_light_frame = dict(reference, x_cm=110.0, y_cm=128.0)
        self.assertIs(
            focus._select_focus_candidate([next_light_frame]),
            next_light_frame,
        )

    def test_nearby_light_survives_large_shape_change(self):
        reference = {
            "x_cm": 100.0,
            "y_cm": 100.0,
            "raw_peak": 20.0,
            "raw_total": 1000.0,
            "area": 100,
            "bbox_w": 12,
            "bbox_h": 18,
            "compactness": 0.8,
            "toca_borda": False,
        }
        signature = focus._lock_manual_candidate(reference, 45.0, 0.45)
        self.assertTrue(focus.initialize_focus_lock(signature, 100.0, 100.0))

        changed_shape = dict(
            reference,
            x_cm=106.0,
            y_cm=96.0,
            raw_peak=2.0,
            raw_total=100.0,
            area=10,
            bbox_w=4,
            bbox_h=5,
            compactness=0.25,
        )
        self.assertLess(
            focus._similarity(changed_shape, focus.FOCUS_LOCK["primary"]),
            focus.LOCK_MIN_SIMILARITY,
        )
        self.assertIs(
            focus._select_focus_candidate([changed_shape]),
            changed_shape,
        )

    def test_locked_detector_thresholds_only_near_last_position(self):
        reference = {
            "x_cm": 100.0,
            "y_cm": 80.0,
            "raw_peak": 20.0,
            "raw_total": 1000.0,
            "area": 100,
            "bbox_w": 12,
            "bbox_h": 18,
            "compactness": 0.8,
            "toca_borda": False,
        }
        signature = focus._lock_manual_candidate(reference, 45.0, 0.45)
        self.assertTrue(focus.initialize_focus_lock(signature, 100.0, 80.0))

        with mock.patch.object(focus, "_find_focus_candidates", return_value=[]) as find:
            result = focus._centro_foco_principal(
                np.zeros((200, 240), dtype=np.float32),
                0.45,
            )

        self.assertIsNone(result)
        self.assertEqual(find.call_args.kwargs["search_roi"], (35, 15, 131, 131))


if __name__ == "__main__":
    unittest.main()
