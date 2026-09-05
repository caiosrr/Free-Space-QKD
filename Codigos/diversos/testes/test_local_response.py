"""Sem hardware: deriva observada, repeticoes locais e parada dos micropulsos."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.calibracao import resposta_local as local
from modulos.calibracao import calibracao_continua_core as core


class LocalResponseTests(unittest.TestCase):
    A = np.array([[-7400., 400.], [120., 7800.]])

    def refs(self, axis=0, sign=1, *, drift=(1.2, -.7), step=.002, noise=0., seed=1):
        rng = np.random.default_rng(seed)
        result = []
        for t, level in [(0.,0.), (3.,0.), (7.,1.), (10.,1.)]:
            q = np.zeros(2); q[axis] = level*sign*step
            p = np.array([130.,120.]) + self.A @ q + t*np.array(drift) + rng.normal(0., noise, 2)
            result.append(dict(t=10000+t, center=p.tolist(), angle=q.tolist(),
                               block_spread_px=.8, frame_count=75))
        return result

    def test_linear_drift_separated_from_command_in_both_axes_and_signs(self):
        for axis in (0,1):
            for sign in (-1,1):
                r = local.measure_step(self.refs(axis,sign))
                np.testing.assert_allclose(r['displacement_px'], self.A[:,axis]*sign*.002, atol=1e-9)
                np.testing.assert_allclose(r['drift_px_s'], [1.2,-.7], atol=1e-9)
                self.assertTrue(local.matrix_step_usable(r,axis,sign)[0])

    def test_instantaneous_noise_not_treated_as_exact_motion(self):
        r = local.measure_step(self.refs(noise=.4))
        self.assertGreater(r['reference_noise_px'], .5)
        self.assertLess(np.linalg.norm(np.array(r['displacement_px'])-self.A[:,0]*.002), 2.)

    def test_small_pulse_not_used_as_angular_calibration(self):
        r = local.measure_step(self.refs(step=.000125))
        self.assertFalse(local.matrix_step_usable(r,0,1)[0])

    def test_unstable_drift_rejected_as_unresolved_step(self):
        refs = self.refs()
        refs[-1]['center'][0] += 40.
        r = local.measure_step(refs)
        self.assertFalse(local.matrix_step_usable(r,0,1)[0])

    def test_bad_times_and_nonfinite_rejected(self):
        refs = self.refs(); refs[1]['t']=refs[0]['t']
        with self.assertRaises(ValueError): local.measure_step(refs)
        refs = self.refs(); refs[0]['center'][0]=float('nan')
        with self.assertRaises(ValueError): local.measure_step(refs)

    def test_repeated_pairs_fit_and_independent_validation(self):
        runs=[]
        for j,spec in enumerate(core.calibration_profile('robusto').specs):
            samples=[]
            for i in range(3):
                r=local.measure_step(self.refs(spec.axis,spec.command_sign,noise=.25,seed=10*j+i))
                self.assertTrue(local.matrix_step_usable(r,spec.axis,spec.command_sign)[0])
                for factor in (-.5,.5):
                    q=factor*np.array(r['delta_deg']); p=factor*np.array(r['displacement_px'])
                    samples.append(core.SweepSample(spec.name,spec.axis,spec.command_sign,i,*q,*q,*p,
                                                   role=spec.role, quality_weight=1/r['reference_noise_px']**2,
                                                   sample_kind='paired_local_step_difference'))
            runs.append(samples)
        fit=core._robust_fit(*core._center_runs(runs[:4],include_weights=True))
        self.assertLess(np.linalg.norm(fit['A']-self.A)/np.linalg.norm(self.A),.05)
        self.assertTrue(core._validate_fit(runs[:4],fit,half_range_deg=.002)['ok'])
        self.assertTrue(core._validate_holdout(runs[4:],fit,label='local')['ok'])

    def holdout_runs(self, scales=(1.,1.,1.,1.)):
        runs=[]
        for scale,spec in zip(scales,core.calibration_profile('robusto').specs[4:]):
            q=np.zeros(2); q[spec.axis]=spec.command_sign*.002
            p=self.A@q*scale
            runs.append([core.SweepSample(spec.name,spec.axis,spec.command_sign,0.,
                         *(factor*q),*(factor*q),*(factor*p),role=spec.role,
                         sample_kind='paired_local_step_difference') for factor in (-.5,.5)])
        return runs

    def test_independent_directions_cannot_disagree_while_straddling_fit(self):
        # Alt + e -: cada um dentro de 35% do ajuste, mas divergem ~47% entre si.
        runs=self.holdout_runs((.85,1.,1.25,1.))
        result=core._validate_holdout(runs,dict(A=self.A,rms_residual_px=0.),label='local')
        self.assertFalse(result['ok'])
        self.assertTrue(any('escalas ida/volta' in s for s in result['failures']))

    def test_residual_is_full_step_not_half_virtual_point(self):
        runs=self.holdout_runs((1.1,)*4)
        result=core._validate_holdout(runs,dict(A=self.A,rms_residual_px=0.),label='local')
        expected=np.sqrt(np.mean(np.sum((self.A*.002*.1)**2,axis=0)))
        self.assertAlmostEqual(result['rms_residual_px'],expected)
        self.assertEqual(result['virtual_pair_residual_factor'],2.)

    def test_failed_reference_aborts_without_new_motion_and_saves_audit(self):
        spec=core.calibration_profile('robusto').specs[0]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(core,'_take_stationary_reference',side_effect=RuntimeError('sem luz')), \
                patch.object(core,'_run_one_sweep') as move, \
                patch.object(core,'timed_pulse') as pulse, \
                patch.object(core,'stop_axes_safely',return_value=True) as stop:
            with self.assertRaisesRegex(RuntimeError,'sem luz'):
                core._run_local_sequence(spec=spec,initial_az=0.,initial_alt=0.,signature={},
                                         center_anchor=(130.,120.),audit_dir=Path(tmp))
            move.assert_not_called();pulse.assert_not_called();stop.assert_called_once()
            self.assertIn('sem luz',(Path(tmp)/(spec.name+'_resposta_local.json')).read_text())

    def test_pulse_stops_even_if_start_command_fails(self):
        with patch.object(core,'move_axis',side_effect=RuntimeError('falha envio')), \
                patch.object(core,'stop_axes_safely',return_value=True) as stop:
            with self.assertRaisesRegex(RuntimeError,'falha envio'):
                local.timed_pulse(0,1,.001042,move=core.move_axis,stop=core.stop_axes_safely,
                                  clock=lambda:0.,sleep=lambda t:None)
            stop.assert_called_once()

    def test_slow_command_ack_does_not_add_another_full_pulse(self):
        now=[0.]; sleeps=[]
        def move(*args):now[0]=.2
        audit=local.timed_pulse(0,1,.001042,move=move,stop=lambda:True,
                                clock=lambda:now[0],sleep=sleeps.append)
        self.assertEqual(sleeps,[0.])
        self.assertEqual(audit['stop_requested_seconds'],.2)

    def test_stop_failure_is_fatal(self):
        with self.assertRaisesRegex(RuntimeError,'Parada nao confirmada'):
            local.timed_pulse(1,-1,.001042,move=lambda *a:None,stop=lambda:False,
                              clock=lambda:0.,sleep=lambda t:None)

    def test_sequence_keeps_unresolved_micropulses_out_of_matrix(self):
        spec=core.calibration_profile('robusto').specs[0]
        q=np.zeros(2);time=[0.]; references=[]
        def ref(*a,**k):
            time[0]+=3.
            p=np.array([130.,120.])+self.A@q+np.array([.2,-.1])*time[0]
            r=dict(t=time[0],angle=q.tolist(),center=p.tolist(),block_spread_px=.8,frame_count=75)
            references.append(r);return r
        def sweep(**k):
            q[0]+=.002
            raw=core.SweepSample('step',0,1,1.,*q,*q,130.,120.)
            return [],None,[raw],{}
        def pulse(*a,**k):q[0]-=.000125;return {'requested_seconds':.12}
        def restore(*a,**k):q[:]=0.;return {'success':True}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(core,'_take_stationary_reference',side_effect=ref), \
                patch.object(core,'read_altaz',side_effect=lambda:tuple(q)), \
                patch.object(core,'_run_one_sweep',side_effect=sweep), \
                patch.object(core,'timed_pulse',side_effect=pulse), \
                patch.object(core,'_return_to_absolute_start',side_effect=restore), \
                patch.object(core,'stop_axes_safely',return_value=True):
            samples,anchor,raw,audit=core._run_local_sequence(spec=spec,initial_az=0.,initial_alt=0.,
                              signature={},center_anchor=(130.,120.),audit_dir=Path(tmp))
            self.assertEqual(len(samples),6)
            self.assertEqual(len(audit['micropulses']),2)
            self.assertTrue(all(not p['optically_resolved'] for p in audit['micropulses']))
            self.assertEqual(audit['reference_count'],len(references))
            self.assertEqual(audit['valid_reference_frames'],75*len(references))
            fit_slope=core._direction_slope(samples,0)
            np.testing.assert_allclose(fit_slope,self.A[:,0],atol=1e-8)


if __name__=='__main__':unittest.main()
