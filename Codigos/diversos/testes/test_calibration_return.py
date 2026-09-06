import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.calibracao import calibracao_continua_core as core
from modulos.calibracao import referencias_estaticas as refs
from modulos.controle import mount_pid as mount


class CalibrationReturnTests(unittest.TestCase):
    def test_single_good_read_then_drift_requires_second_attempt(self):
        now = [0.]
        corrected = [False]
        def read():
            return (0. if now[0] < .1 or corrected[0] else .001, 0.)
        def move(*args, **kwargs):
            self.assertEqual(kwargs["absolute_target"], (0., 0.))
            corrected[0] = True
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(core.time, "perf_counter", side_effect=lambda: now[0]), \
                patch.object(core.time, "sleep", side_effect=lambda dt: now.__setitem__(0, now[0]+dt)), \
                patch.object(core, "read_altaz", side_effect=read), \
                patch.object(core, "stop_axes_safely", return_value=True), \
                patch.object(core, "move_axes_pid_2d", side_effect=move) as mover:
            path = Path(tmp)/"retorno.json"
            result = core._return_to_absolute_start(0., 0., audit_path=path)
            self.assertTrue(result["success"])
            self.assertEqual(result["attempts"], 2)
            self.assertGreaterEqual(result["stable_seconds"], 1.5)
            mover.assert_called_once()
            self.assertTrue(json.loads(path.read_text())["readings"])

    def test_unstable_within_tolerance_is_not_accepted(self):
        now = [0.]
        count = [0]
        def read():
            count[0] += 1
            return (.0004 * (-1 if count[0] % 2 else 1), 0.)
        with patch.object(core.time, "perf_counter", side_effect=lambda: now[0]), \
                patch.object(core.time, "sleep", side_effect=lambda dt: now.__setitem__(0, now[0]+dt)), \
                patch.object(core, "read_altaz", side_effect=read), \
                patch.object(core, "stop_axes_safely", return_value=True), \
                patch.object(core, "move_axes_pid_2d") as mover:
            result = core._return_to_absolute_start(0., 0.)
            self.assertFalse(result["success"])
            mover.assert_not_called()
            self.assertLess(now[0], 9.)

    def test_unconfirmed_stop_never_starts_return(self):
        with patch.object(core, "stop_axes_safely", return_value=False), \
                patch.object(core, "move_axes_pid_2d") as mover:
            self.assertFalse(core._return_to_absolute_start(0., 0.)["success"])
            mover.assert_not_called()

    def test_stable_return_handles_azimuth_wrap(self):
        now = [0.]
        with patch.object(core.time, "perf_counter", side_effect=lambda: now[0]), \
                patch.object(core.time, "sleep", side_effect=lambda dt: now.__setitem__(0, now[0]+dt)), \
                patch.object(core, "read_altaz", return_value=(0.0001, 10.)), \
                patch.object(core, "stop_axes_safely", return_value=True), \
                patch.object(core, "move_axes_pid_2d") as mover:
            result = core._return_to_absolute_start(359.9997, 10.)
            self.assertTrue(result["success"])
            mover.assert_not_called()

    def test_reference_stable_but_off_target_is_rejected(self):
        now = [0.]
        def capture():
            now[0] += .05
            return np.zeros((8,8), dtype=np.uint8), (4.,4.,10.,False), {}
        audit = {}
        with self.assertRaisesRegex(RuntimeError, "erro_alvo_angular"):
            refs.collect_reference(capture, lambda:(.001,0.), lambda *args:(4.,4.),
                                   clock=lambda:now[0], quality=core._frame_quality,
                                   audit=audit, expected_angle=(0.,0.))
        self.assertAlmostEqual(audit["last_target_error_deg"], .001)

    def test_absolute_pid_target_does_not_shift_with_second_position_read(self):
        records=[]
        def pid(**kwargs):
            target=kwargs["setpoint"]
            return SimpleNamespace(update=lambda axis, value:(.001,target-value), reset=lambda:None)
        with patch.object(mount,"read_altaz",side_effect=[(10.001,20.),(10.002,20.),(10.,20.),(10.,20.)]), \
                patch.object(mount,"PID",side_effect=pid), \
                patch.object(mount,"move_axis"), \
                patch.object(mount,"stop_axes_safely",return_value=True), \
                patch.object(mount.time,"sleep"), \
                patch.object(mount,"_status_write"), patch.object(mount,"_status_clear"), patch("builtins.print"):
            mount.move_axes_pid_2d(True,-.005,0.,absolute_target=(10.,20.),telemetry_callback=records.append)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]["target_az_deg"],10.)
        self.assertAlmostEqual(records[0]["error_az_deg"],-.002)


if __name__ == "__main__":
    unittest.main()
