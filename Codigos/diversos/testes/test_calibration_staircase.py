"""Escadas simuladas: offsets de retorno, ruido e validacao fora do ajuste."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.calibracao import calibracao_continua_core as core


class StaircaseTests(unittest.TestCase):
    A = np.array([[-6200., 500.], [300., 7500.]])

    def make_runs(self, *, noise=0., holdout_scale=1., drift=0.):
        rng = np.random.default_rng(902)
        runs = []
        for spec in core.calibration_profile('robusto').specs:
            # Uma referencia diferente em cada retorno nao altera a derivada.
            origin = rng.uniform(100., 160., 2)
            samples = []
            for j, fraction in enumerate(core.STATIONARY_FRACTIONS):
                q = np.zeros(2)
                q[spec.axis] = fraction * spec.half_range_deg * spec.command_sign
                scale = holdout_scale if spec.role != 'fit' else 1.
                p = origin + scale * self.A @ q + rng.normal(0., noise, 2)
                p += drift * j * np.array([1., 0.])
                samples.append(core.SweepSample(
                    spec.name, spec.axis, spec.command_sign, 4.*j, *q, *q, *p,
                    role=spec.role, half_range_deg=spec.half_range_deg,
                    frames_combined=80, centroid_spread_px=1.,
                    sample_kind='stationary_monotonic_plateau'))
            core._validate_stationary_staircase(samples, spec)
            runs.append(samples)
        return runs

    def test_independent_return_offsets_do_not_bias_matrix(self):
        runs = self.make_runs()
        fit = core._robust_fit(*core._center_runs(runs[:4], include_weights=True))
        np.testing.assert_allclose(fit['A'], self.A, atol=1e-8)
        self.assertTrue(core._validate_fit(runs[:4], fit)['ok'])
        self.assertTrue(core._validate_holdout(runs[4:8], fit, label='local')['ok'])

    def test_noisy_temporal_references_and_holdout(self):
        runs = self.make_runs(noise=0.8)
        fit = core._robust_fit(*core._center_runs(runs[:4], include_weights=True))
        self.assertLess(np.linalg.norm(fit['A']-self.A)/np.linalg.norm(self.A), .08)
        self.assertTrue(core._validate_fit(runs[:4], fit)['ok'])
        self.assertTrue(core._validate_holdout(runs[4:8], fit, label='local')['ok'])

    def test_bad_independent_response_still_rejects(self):
        runs = self.make_runs(holdout_scale=1.6)
        fit = core._robust_fit(*core._center_runs(runs[:4], include_weights=True))
        self.assertFalse(core._validate_holdout(runs[4:8], fit, label='local')['ok'])

    def test_large_slow_drift_is_not_claimed_to_be_removed(self):
        runs = self.make_runs(drift=6.)
        fit = core._robust_fit(*core._center_runs(runs[:4], include_weights=True))
        self.assertFalse(core._validate_fit(runs[:4], fit)['ok'])

    def test_stationary_outlier_is_not_hidden_by_return_removal(self):
        run = self.make_runs()[0]
        run[2].y_px += 30.
        with self.assertRaisesRegex(RuntimeError, 'nao lineares'):
            core._validate_stationary_staircase(run, core.calibration_profile('robusto').specs[0])

    def test_wrong_order_and_other_axis_motion_rejected(self):
        spec = core.calibration_profile('robusto').specs[0]
        run = self.make_runs()[0]
        run[2].delta_az_deg = run[1].delta_az_deg
        with self.assertRaisesRegex(RuntimeError, 'nao monotonicos'):
            core._validate_stationary_staircase(run, spec)
        run = self.make_runs()[0]
        run[-1].delta_alt_deg = .001
        with self.assertRaisesRegex(RuntimeError, 'ortogonal'):
            core._validate_stationary_staircase(run, spec)

    def test_short_movement_failure_stops_before_audit(self):
        spec = core.SweepSpec('step', 0, 1, .002, 'fit')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(core, 'read_altaz', return_value=(0., 0.)), \
                patch.object(core, 'move_axis'), \
                patch.object(core, '_capture_valid_cm', side_effect=RuntimeError('camera desconectada')), \
                patch.object(core, 'stop_axes_safely', return_value=True) as stop:
            with self.assertRaisesRegex(RuntimeError, 'camera desconectada'):
                core._run_one_sweep(spec=spec, initial_az=0., initial_alt=0., signature={},
                                    center_anchor=(100.,100.), audit_dir=Path(tmp),
                                    baseline=False, validate_dynamic=False)
            stop.assert_called_once()
            self.assertTrue((Path(tmp)/'step_frames.csv').exists())

    def test_cannot_start_checkpoint_already_passed(self):
        spec = core.SweepSpec('step', 0, 1, .002, 'fit')
        with patch.object(core, 'read_altaz', return_value=(.003, 0.)), \
                patch.object(core, 'move_axis') as move, \
                patch.object(core, 'stop_axes_safely', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'ja atingido'):
                core._run_one_sweep(spec=spec, initial_az=0., initial_alt=0., signature={},
                                    center_anchor=(100.,100.), baseline=False, validate_dynamic=False)
            move.assert_not_called()

    def test_short_step_does_not_require_full_sweep_bins(self):
        spec = core.SweepSpec('step', 0, 1, .002, 'fit')
        positions = [(0., 0.)] + [(float(q), 0.) for q in np.linspace(0., .002, 11) for _ in range(2)]
        frame = np.zeros((8,8), dtype=np.uint8)
        with patch.object(core, 'read_altaz', side_effect=positions), \
                patch.object(core, 'move_axis'), \
                patch.object(core, 'stop_axes_safely', return_value=True) as stop, \
                patch.object(core, '_capture_valid_cm', return_value=(frame, (4.,4.,10.,False), {})):
            _, _, raw, stats = core._run_one_sweep(
                spec=spec, initial_az=0., initial_alt=0., signature={}, center_anchor=(4.,4.),
                baseline=False, validate_dynamic=False)
            self.assertEqual(len(raw), 11)
            self.assertIn('dynamic_warning', stats)
            stop.assert_called_once()

    def test_opposite_motion_aborts_and_stops(self):
        spec = core.SweepSpec('step', 0, 1, .002, 'fit')
        with patch.object(core, 'read_altaz', side_effect=[(0.,0.),(-.002,0.),(-.002,0.)]), \
                patch.object(core, 'move_axis'), \
                patch.object(core, 'stop_axes_safely', return_value=True) as stop, \
                patch.object(core, '_capture_valid_cm', return_value=(np.zeros((8,8)), None, {})):
            with self.assertRaisesRegex(RuntimeError, 'sentido angular oposto'):
                core._run_one_sweep(spec=spec, initial_az=0., initial_alt=0., signature={},
                                    center_anchor=(4.,4.), baseline=False, validate_dynamic=False)
            stop.assert_called_once()

    def test_failed_stop_is_not_silently_accepted(self):
        spec = core.SweepSpec('step', 0, 1, .002, 'fit')
        with patch.object(core, 'read_altaz', return_value=(.003,0.)), \
                patch.object(core, 'move_axis') as move, \
                patch.object(core, 'stop_axes_safely', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Parada nao confirmada'):
                core._run_one_sweep(spec=spec, initial_az=0., initial_alt=0., signature={},
                                    center_anchor=(4.,4.), baseline=False, validate_dynamic=False)
            move.assert_not_called()


if __name__ == '__main__':
    unittest.main()
