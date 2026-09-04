import tempfile
import unittest
import sys
from pathlib import Path

import numpy as np

CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.calibracao import calibracao_continua_core as continuous


class ContinuousCalibrationTests(unittest.TestCase):
    @staticmethod
    def _spot_frame(x_px, y_px, size=96):
        yy, xx = np.indices((size, size), dtype=np.float32)
        spot = np.exp(
            -((xx - x_px) ** 2 + (yy - y_px) ** 2) / (2.0 * 2.5**2)
        )
        return np.clip(spot * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def _synthetic_runs(A, noise=0.35, reverse_bad_axis=None):
        rng = np.random.default_rng(1234)
        runs = []
        for axis in (0, 1):
            for command_sign in (+1, -1):
                samples = []
                intercept = rng.uniform(90.0, 150.0, size=2)
                for index, active in enumerate(np.linspace(0.0002, 0.0082, 80)):
                    delta = np.zeros(2, dtype=float)
                    delta[axis] = command_sign * active
                    pixels = intercept + (A @ delta) + rng.normal(0.0, noise, size=2)
                    if reverse_bad_axis == axis and command_sign < 0:
                        pixels = intercept - (A @ delta) + rng.normal(0.0, noise, size=2)
                    samples.append(
                        continuous.SweepSample(
                            run=f"axis{axis}_{command_sign:+d}",
                            axis=axis,
                            command_sign=command_sign,
                            elapsed_s=index * 0.03,
                            az_deg=10.0 + delta[0],
                            alt_deg=20.0 + delta[1],
                            delta_az_deg=delta[0],
                            delta_alt_deg=delta[1],
                            x_px=float(pixels[0]),
                            y_px=float(pixels[1]),
                        )
                    )
                runs.append(samples)
        return runs

    def test_robust_continuous_fit_recovers_matrix_and_passes_validation(self):
        expected = np.array([[5200.0, 650.0], [-450.0, 4700.0]])
        runs = self._synthetic_runs(expected)
        design, pixels = continuous._center_runs(runs)
        fit = continuous._robust_fit(design, pixels)
        validation = continuous._validate_fit(runs, fit)

        self.assertTrue(validation["ok"], validation["failures"])
        self.assertTrue(np.allclose(fit["A"], expected, rtol=0.03, atol=40.0))
        self.assertLess(fit["rms_residual_px"], 1.0)

    def test_opposite_direction_response_is_rejected(self):
        expected = np.array([[5000.0, 400.0], [250.0, 4500.0]])
        runs = self._synthetic_runs(expected, reverse_bad_axis=1)
        design, pixels = continuous._center_runs(runs)
        with self.assertRaisesRegex(RuntimeError, "mal condicionada"):
            continuous._robust_fit(design, pixels)

    def test_promotion_backs_up_old_active_matrices(self):
        old_A = np.array([[1.0, 2.0], [3.0, 4.0]])
        new_A = np.array([[10.0, 2.0], [1.0, 9.0]])
        new_inv = np.linalg.inv(new_A)
        prefix = "ids_raw_foco_temp"

        with tempfile.TemporaryDirectory() as tmp:
            matrix_dir = Path(tmp) / "matrizes"
            backup_dir = Path(tmp) / "backup"
            matrix_dir.mkdir()
            for regime in ("fine", "coarse"):
                np.save(matrix_dir / f"{prefix}_A_{regime}.npy", old_A)
                np.save(matrix_dir / f"{prefix}_A_inv_{regime}.npy", np.linalg.inv(old_A))

            copied = continuous._promote_matrices(
                matrix_dir,
                backup_dir,
                prefix,
                new_A,
                new_inv,
            )

            self.assertEqual(len(copied), 4)
            self.assertTrue(
                np.array_equal(np.load(backup_dir / f"{prefix}_A_fine.npy"), old_A)
            )
            self.assertTrue(
                np.array_equal(np.load(matrix_dir / f"{prefix}_A_fine.npy"), new_A)
            )

    def test_azimuth_offset_handles_wrap(self):
        daz, dalt = continuous._offsets_from_start(359.999, 10.0, 0.004, 9.998)
        self.assertAlmostEqual(daz, 0.005)
        self.assertAlmostEqual(dalt, -0.002)

    def test_robust_profile_separates_fit_local_holdout_and_wide_validation(self):
        profile = continuous.calibration_profile("robusto")
        roles = [spec.role for spec in profile.specs]

        self.assertEqual(roles.count("fit"), 4)
        self.assertEqual(roles.count("holdout_local"), 4)
        self.assertEqual(roles.count("holdout_amplo"), 4)
        self.assertTrue(
            all(
                spec.half_range_deg == continuous.LOCAL_HALF_RANGE_DEG
                for spec in profile.specs
                if spec.role == "fit"
            )
        )
        self.assertTrue(
            all(
                spec.half_range_deg > continuous.LOCAL_HALF_RANGE_DEG
                for spec in profile.specs
                if spec.role == "holdout_amplo"
            )
        )

    def test_angle_bins_stack_short_exposures_before_fitting(self):
        captures = []
        slope_px_per_deg = 5000.0
        rng = np.random.default_rng(42)
        for bin_index in range(20):
            for frame_index in range(4):
                active = (
                    bin_index * continuous.ANGLE_BIN_WIDTH_DEG
                    + frame_index * 1e-6
                )
                true_x = 32.0 + slope_px_per_deg * active
                jitter_x, jitter_y = rng.normal(0.0, 0.8, size=2)
                x_px = true_x + jitter_x
                y_px = 40.0 + jitter_y
                sample = continuous.SweepSample(
                    run="az_pos",
                    axis=0,
                    command_sign=1,
                    elapsed_s=len(captures) * 0.02,
                    az_deg=10.0 + active,
                    alt_deg=20.0,
                    delta_az_deg=active,
                    delta_alt_deg=0.0,
                    x_px=x_px,
                    y_px=y_px,
                )
                captures.append(
                    (sample, self._spot_frame(x_px, y_px), 1.0)
                )

        aggregated = continuous._aggregate_sweep_frames(captures)
        quality = continuous._validate_sweep_aggregation(aggregated, "az_pos")
        angles = np.asarray([sample.delta_az_deg for sample in aggregated])
        positions = np.asarray([sample.x_px for sample in aggregated])
        recovered_slope = np.polyfit(angles, positions, 1)[0]

        self.assertEqual(len(aggregated), 20)
        self.assertEqual(quality["median_frames_per_bin"], 4.0)
        self.assertLess(quality["median_centroid_spread_px"], 2.0)
        self.assertAlmostEqual(recovered_slope, slope_px_per_deg, delta=120.0)

    def test_excessive_within_bin_motion_rejects_sweep(self):
        samples = [
            continuous.SweepSample(
                run="turbulento",
                axis=0,
                command_sign=1,
                elapsed_s=index * 0.1,
                az_deg=10.0,
                alt_deg=20.0,
                delta_az_deg=index * continuous.ANGLE_BIN_WIDTH_DEG,
                delta_alt_deg=0.0,
                x_px=40.0,
                y_px=40.0,
                frames_combined=4,
                centroid_spread_px=6.0,
            )
            for index in range(continuous.MIN_VALID_SWEEP_BINS)
        ]
        with self.assertRaisesRegex(RuntimeError, "dispersao optica excessiva"):
            continuous._validate_sweep_aggregation(samples, "turbulento")


if __name__ == "__main__":
    unittest.main()
