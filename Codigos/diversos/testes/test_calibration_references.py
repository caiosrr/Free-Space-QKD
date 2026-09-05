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

    def test_aba_removes_linear_drift_with_actual_return_angle(self):
        A = np.array([[-6200., 500.], [300., 7500.]])
        qa, qb, qc = np.array([0., 0.]), np.array([0.008, 0.]), np.array([0.00028, 0.])
        a, b, c = [self.reference(t, q, A) for t, q in [(1., qa), (8., qb), (15., qc)]]
        d = refs.reference_difference(a, b, c, axis=0, amplitude=0.008)
        np.testing.assert_allclose(d["displacement_px"], A @ np.array(d["delta_deg"]), atol=1e-9)
        self.assertGreater(np.linalg.norm(np.array(d["displacement_px"]) - d["uncorrected_displacement_px"]), 0.1)

    def test_large_return_discrepancy_is_not_assumed_to_be_atmosphere(self):
        A = np.diag([6000., 7000.])
        a, b, c = [self.reference(t, np.array(q), A) for t, q in [(0., [0., 0.]), (8., [.008, 0.]), (16., [0., 0.])]]
        c["center"][0] += 25
        with self.assertRaisesRegex(RuntimeError, "Retorno optico nao repetivel"):
            refs.reference_difference(a, b, c, axis=0, amplitude=0.008)

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

    def test_complete_static_fit_is_independent_of_sweep_transient(self):
        A = np.array([[-6200., 500.], [300., 7500.]])
        runs = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, spec in enumerate(core.calibration_profile("robusto").specs[:4]):
                q = np.zeros(2); q[spec.axis] = spec.command_sign * spec.half_range_deg
                references = [self.reference(0., np.zeros(2), A, drift=(0., 0.))]
                references += [self.reference(4.*j, fraction*q, A, drift=(0., 0.))
                               for j, fraction in enumerate(core.STATIONARY_FRACTIONS, 1)]
                references += [self.reference(25., np.zeros(2), A, drift=(0., 0.))]
                # Mesmo com fechamento optico ruim, a escala vem dos patamares.
                references[-1]["center"][0] += 20.
                raw = core.SweepSample(spec.name, spec.axis, spec.command_sign, 4., 0., 0., 0., 0., 100., 100.)
                # Dados em movimento deliberadamente sem resposta optica util.
                with patch.object(core, "_take_stationary_reference", side_effect=references), \
                        patch.object(core, "_run_one_sweep", return_value=([], (100., 100.), [raw], {})), \
                        patch.object(core, "_return_to_absolute_start", return_value={"success": True}):
                    samples, anchor, _, stats = core._run_reference_sweep(
                        spec=spec, initial_az=0., initial_alt=0., signature={},
                        center_anchor=(100.,100.), audit_dir=Path(tmp),
                    )
                runs.append(samples)
                np.testing.assert_allclose(anchor, references[-1]["center"])
                self.assertEqual(len(samples), 4)
                self.assertFalse(stats['return_used_for_fit'])
                self.assertEqual(stats['optical_return_px'], 20.)
            fit = core._robust_fit(*core._center_runs(runs, include_weights=True))
            np.testing.assert_allclose(fit["A"], A, atol=1e-8)
            self.assertTrue(core._validate_fit(runs, fit)["ok"])


if __name__ == "__main__":
    unittest.main()
