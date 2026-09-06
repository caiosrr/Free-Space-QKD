import tempfile
import unittest
import sys
from unittest.mock import patch
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

    def test_asymmetric_azimuth_scale_is_not_promoted_as_valid(self):
        expected = np.array([[6200.0, 540.0], [320.0, 7330.0]])
        runs = self._synthetic_runs(expected, noise=0.0)
        # Reproduz a discrepancia de aproximadamente 53% da sessao real.
        for sample in runs[0]:
            delta = expected[:, 0] * sample.delta_az_deg * (1 / 1.53 - 1)
            sample.x_px += delta[0]
            sample.y_px += delta[1]
        fit = continuous._robust_fit(*continuous._center_runs(runs))
        validation = continuous._validate_fit(runs, fit)
        self.assertFalse(validation["ok"])
        self.assertTrue(any("escalas ida/volta" in x for x in validation["failures"]))

    def test_noisy_fit_does_not_relax_independent_validation(self):
        expected = np.array([[5000., 0.], [0., 5000.]])
        runs = self._synthetic_runs(expected, noise=0.)
        for run in runs:
            for i, sample in enumerate(run):
                sample.y_px += 6.0 * (-1 if i % 2 else 1)
        result = continuous._validate_holdout(
            runs, {"A": expected, "rms_residual_px": 10.0}, label="holdout"
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("residuo alto" in x for x in result["failures"]))

    def test_clean_independent_validation_still_passes(self):
        expected = np.array([[5000., 500.], [300., 7000.]])
        result = continuous._validate_holdout(
            self._synthetic_runs(expected), {"A": expected, "rms_residual_px": 0.5},
            label="holdout",
        )
        self.assertTrue(result["ok"], result["failures"])

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

    def test_robust_profile_separates_fit_and_local_holdout_without_wide_motion(self):
        profile = continuous.calibration_profile("robusto")
        roles = [spec.role for spec in profile.specs]

        self.assertEqual(roles.count("fit"), 4)
        self.assertEqual(roles.count("holdout"), 4)
        self.assertEqual(set(roles), {"fit", "holdout"})
        self.assertTrue(
            all(
                spec.half_range_deg == continuous.SWEEP_HALF_RANGE_DEG
                for spec in profile.specs
            )
        )

    def test_angle_bins_stack_short_exposures_before_fitting(self):
        captures = []
        # Escala baixa de proposito: 20 bins de 7.2 arcsec precisam caber no
        # frame sintetico sem encostar na borda.
        slope_px_per_deg = 1500.0
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
                    (sample, self._spot_frame(x_px, y_px, size=224), 1.0)
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
        with self.assertRaisesRegex(RuntimeError, "dispersao residual excessiva"):
            continuous._validate_sweep_aggregation(samples, "turbulento")

    # Passo real medido em bancada: o driver so atualiza a posicao a cada 0.5 s
    # e, a 0.004 deg/s, cada atualizacao anda 0.002 deg (7.2 arcsec).
    TELEMETRY_STEP_DEG = 0.002
    TELEMETRY_PERIOD_S = 0.5
    OPTICAL_SPEED_PX_S = 30.0

    def _stepped_telemetry_captures(self, sign=1, oscillation=0.0):
        captures = []
        dt = 0.05
        for index in range(120):
            elapsed = index * dt
            plateau = index // int(self.TELEMETRY_PERIOD_S / dt)
            active = sign * plateau * self.TELEMETRY_STEP_DEG
            x_px = 160.0 + sign * self.OPTICAL_SPEED_PX_S * (elapsed - 3.0)
            y_px = 110.0 + oscillation * (-1 if index % 2 else 1)
            sample = continuous.SweepSample(
                "fit_az_pos", 0, sign, elapsed, active, 0.0,
                active, 0.0, x_px, y_px,
            )
            captures.append((sample, self._spot_frame(x_px, y_px, size=320), 1.0))
        return captures

    def test_stepped_angles_do_not_turn_smooth_sweep_into_optical_noise(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                samples = continuous._aggregate_sweep_frames(self._stepped_telemetry_captures(sign))
                stats = continuous._validate_sweep_aggregation(samples, "smooth")
                self.assertGreater(stats["median_raw_centroid_spread_px"], 5.0)
                self.assertLess(stats["median_centroid_spread_px"], 0.01)
                self.assertAlmostEqual(stats["trend_px_s"][0], sign * 30.0)
                # O desconto da tendencia nao remove o sinal que calibra a matriz.
                esperado = self.OPTICAL_SPEED_PX_S / (
                    self.TELEMETRY_STEP_DEG / self.TELEMETRY_PERIOD_S
                )
                slope = np.polyfit([s.delta_az_deg for s in samples], [s.x_px for s in samples], 1)[0]
                self.assertAlmostEqual(slope, esperado, delta=150.0)

    def test_detrending_does_not_hide_fast_oscillation(self):
        samples = continuous._aggregate_sweep_frames(self._stepped_telemetry_captures(oscillation=8.0))
        with self.assertRaisesRegex(RuntimeError, "dispersao residual excessiva"):
            continuous._validate_sweep_aggregation(samples, "oscillating")

    def test_constant_timestamps_have_no_motion_discount(self):
        trend = continuous._sweep_motion_trend(np.zeros(4), np.array([[1., 2.], [2., 8.], [3., 1.], [4., 9.]]))
        np.testing.assert_array_equal(trend, [0.0, 0.0])

    def test_sweep_saves_raw_frames_only_after_stopping_the_axes(self):
        """Os frames crus vao para o disco depois da parada, nunca antes."""
        frame = self._spot_frame(40, 40)
        positions = [(0., 0.)] * 47 + [(0.008, 0.)] * 2
        spec = continuous.SweepSpec("fit_az_pos", 0, 1, 0.008, "fit")
        original_write = continuous._write_csv
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(continuous, "move_axis"),                 patch.object(continuous, "stop_axes_safely", return_value=True) as stop,                 patch.object(continuous, "read_altaz", side_effect=positions),                 patch.object(continuous, "_capture_valid_cm", return_value=(frame, (40., 40., 10., False), {})):
            def write_after_stop(path, runs):
                stop.assert_called_once()
                original_write(path, runs)

            with patch.object(continuous, "_write_csv", side_effect=write_after_stop):
                continuous._run_one_sweep(
                    spec=spec, initial_az=0., initial_alt=0., signature={},
                    center_anchor=(40., 40.), audit_dir=Path(tmp), baseline=False,
                )
            raw = (Path(tmp) / "fit_az_pos_frames.csv").read_text(encoding="utf-8-sig")
            self.assertEqual(len(raw.splitlines()), 25)

    def test_sweep_refuses_too_few_valid_samples(self):
        """Uma varredura curta demais nao pode alimentar a matriz."""
        frame = self._spot_frame(40, 40)
        # Uma leitura inicial mais duas por iteracao; para em ~16 amostras,
        # abaixo do minimo exigido para alimentar a matriz.
        positions = [(0., 0.)] * 30 + [(0.008, 0.)] * 10
        spec = continuous.SweepSpec("fit_az_pos", 0, 1, 0.008, "fit")
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(continuous, "move_axis"),                 patch.object(continuous, "stop_axes_safely", return_value=True),                 patch.object(continuous, "read_altaz", side_effect=positions),                 patch.object(continuous, "_capture_valid_cm", return_value=(frame, (40., 40., 10., False), {})):
            with self.assertRaisesRegex(RuntimeError, "amostras validas"):
                continuous._run_one_sweep(
                    spec=spec, initial_az=0., initial_alt=0., signature={},
                    center_anchor=(40., 40.), audit_dir=Path(tmp), baseline=False,
                )

    def test_capture_failure_preserves_partial_frames(self):
        frame = self._spot_frame(40, 40)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(continuous, "_baseline_anchor", return_value=(40., 40.)), \
                patch.object(continuous, "move_axis"), \
                patch.object(continuous, "stop_axes_safely") as stop, \
                patch.object(continuous, "read_altaz", return_value=(0., 0.)), \
                patch.object(continuous, "_capture_valid_cm", side_effect=[
                    (frame, (40., 40., 10., False), {}), RuntimeError("camera indisponivel"),
                ]):
            with self.assertRaisesRegex(RuntimeError, "camera indisponivel"):
                continuous._run_one_sweep(
                    spec=continuous.SweepSpec("fit_az_pos", 0, 1, 0.008, "fit"),
                    initial_az=0., initial_alt=0., signature={},
                    center_anchor=(40., 40.), audit_dir=Path(tmp),
                )
            stop.assert_called_once()
            raw = (Path(tmp) / "fit_az_pos_frames.csv").read_text(encoding="utf-8-sig")
            self.assertEqual(len(raw.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
