import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.calibracao import referencias_estaticas as refs
from modulos.calibracao import calibracao_continua_core as core


class ReferenceCalibrationTests(unittest.TestCase):
    @staticmethod
    def reference(t, q, A, drift=(0.15, -0.1)):
        return dict(t=t, angle=list(q), center=(np.array([100., 100.]) + A @ q + np.array(drift) * t).tolist(),
                    block_spread_px=0.8, frame_count=60, span_s=2.0)

    def test_collect_averages_fast_motion_and_waits_through_occlusion(self):
        now = [0.]
        audit = {}
        yy, xx = np.indices((64, 64))

        def capture():
            now[0] += 0.02
            x = 32 + 7 * np.sin(2 * np.pi * 10 * now[0])
            frame = np.clip(220 * np.exp(-((xx-x)**2 + (yy-32)**2) / 12), 0, 255).astype(np.uint8)
            cm = None if now[0] < 3 else (x, 32., 30., False)
            return frame, cm, {}

        result = refs.collect_reference(capture, lambda: (0., 0.), core._centroid_from_stacked_frames,
                                        clock=lambda: now[0], quality=core._frame_quality, audit=audit)
        self.assertGreater(now[0], 5)
        self.assertLess(now[0], refs.REFERENCE_TIMEOUT_S)
        self.assertGreaterEqual(result["span_s"], 2)
        self.assertLess(abs(result["center"][0]-32), 1)
        self.assertGreater(audit["rejections"]["sem_candidato"], 100)

    def test_failed_reference_records_why_and_does_not_move_mount(self):
        now = [0.]
        def capture():
            now[0] += .1
            return np.zeros((16, 16), dtype=np.uint8), None, {}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(core, "stop_axes_safely"), \
                patch.object(core.time, "sleep"), \
                patch.object(core.time, "perf_counter", side_effect=lambda: now[0]), \
                patch.object(core.foco, "initialize_focus_lock", return_value=True), \
                patch.object(core, "_capture_valid_cm", side_effect=capture), \
                patch.object(core, "move_axis") as move:
            path = Path(tmp) / "ref.json"
            with self.assertRaisesRegex(RuntimeError, "sem_candidato"):
                core._take_stationary_reference({}, (8., 8.), 0., 0., path)
            move.assert_not_called()
            self.assertIn('"status": "erro"', path.read_text(encoding="utf-8"))

    def test_high_fps_preserves_two_second_coverage_with_bounded_storage(self):
        now = [0.]
        def capture():
            now[0] += .001
            return np.zeros((8, 8), dtype=np.uint8), (4., 4., 10., False), {}
        result = refs.collect_reference(capture, lambda: (0., 0.),
                                        lambda frames, centers, weights: (4., 4.),
                                        clock=lambda: now[0], quality=core._frame_quality, audit={})
        self.assertGreaterEqual(result["span_s"], 2)
        self.assertLess(result["frame_count"], 300)
        self.assertGreater(result["captured_count"], 2000)


if __name__ == "__main__":
    unittest.main()
