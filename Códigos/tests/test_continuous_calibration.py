import tempfile
import unittest
from pathlib import Path

import numpy as np

from foco_multiplos import calibracao_varredura_continua as continuous


class ContinuousCalibrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
