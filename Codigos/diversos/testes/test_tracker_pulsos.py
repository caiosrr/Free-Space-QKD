import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.controle.tracker_pulsos import BoundedCorrectionCycle
from modulos.controle.tracker_estado import TrackerState
from modulos.controle import tracker_loop


class BoundedPulseTests(unittest.TestCase):
    def cycle(self):
        return BoundedCorrectionCycle(0.001042, min_s=0.02)

    def command(self, cycle, now, ts=None, enabled=True, fine=False, error=(0.01, -0.01), current=None):
        return cycle.command(now, now if ts is None else ts, error, error,
                             error if current is None else current, fine=fine, enabled=enabled)

    def test_large_error_is_bounded_even_without_new_frames(self):
        c = self.cycle()
        np.testing.assert_allclose(self.command(c, 1), [0.001042, -0.001042])
        np.testing.assert_array_equal(self.command(c, 1.26, ts=1), [0, 0])
        self.assertEqual(c.phase, "parando")
        self.assertFalse(c.ready(100))  # Tempo sozinho nao confirma parada.
        self.assertTrue(c.confirm_stopped(2))
        self.assertFalse(c.ready(4.29))
        self.assertTrue(c.ready(4.31))

    def test_signal_loss_cancels_and_still_requires_refresh(self):
        c = self.cycle()
        self.command(c, 1)
        np.testing.assert_array_equal(self.command(c, 1.05, enabled=False), [0, 0])
        c.confirm_stopped(1.1)
        np.testing.assert_array_equal(self.command(c, 1.2), [0, 0])

    def test_historical_error_cannot_command_opposite_direction(self):
        c = self.cycle()
        cmd = self.command(c, 1, current=(-0.01, -0.01))
        np.testing.assert_allclose(cmd, [0, -0.001042])

    def test_fraction_budget_uses_smaller_current_error_and_no_round_up(self):
        c = self.cycle()
        cmd = self.command(c, 1, current=(0.000001, -0.0002))
        self.assertEqual(cmd[0], 0)
        self.assertLessEqual((c.deadlines[1] - 1) * c.min_rate, 0.0002 * 0.35 + 1e-12)

    def test_fine_limit_and_independent_axis_deadlines(self):
        c = self.cycle()
        self.command(c, 1, fine=True, error=(0.01, -0.0001))
        self.assertAlmostEqual(c.deadlines[0], 1.12)
        cmd = self.command(c, 1.05, fine=True)
        self.assertGreater(cmd[0], 0)
        self.assertEqual(cmd[1], 0)

    def test_loop_stops_without_new_frames_and_renews_histories(self):
        # Integra o loop real com relogio, mount e camera simulados (sem hardware).
        state = TrackerState(has_signal=True, dx_filt_px=8., dy_filt_px=6.)
        clock = [1.0]
        commands = []
        first_move = [None]

        def move(axis, rate, unused):
            commands.append((clock[0], axis, rate))
            if rate and first_move[0] is None:
                first_move[0] = clock[0]

        def sleep(dt):
            clock[0] += max(dt, 0.001)
            with state.lock:
                # Congela frames por 0.35 s no primeiro movimento; o prazo
                # de 0.25 s deve parar antes do watchdog de sinal de 0.45 s.
                frozen = first_move[0] is not None and 0 < clock[0] - first_move[0] < 0.35
                if not frozen:
                    state.measurement_seq += 1
                    state.measurement_ts = clock[0]
                state.stop = clock[0] > 22

        with patch.object(tracker_loop.time, "perf_counter", side_effect=lambda: clock[0]), \
                patch.object(tracker_loop.time, "sleep", side_effect=sleep), \
                patch.object(tracker_loop, "move_axis", side_effect=move), \
                patch.object(tracker_loop, "stop_axes_safely"), \
                patch.object(tracker_loop, "CONTROL_HZ", 50):
            tracker_loop.executar_loop_controle(state, np.diag([-1/6200, 1/7300]))
        bursts = []
        for axis in (0, 1):
            start = None
            for t, ax, rate in commands:
                if ax != axis:
                    continue
                if rate and start is None:
                    start = t
                if not rate and start is not None:
                    bursts.append((start, t))
                    self.assertLessEqual(t - start, 0.28)
                    start = None
        self.assertGreaterEqual(len(bursts), 4)
        first_end = min(end for start, end in bursts)
        next_start = min(start for start, end in bursts if start > first_end)
        self.assertGreaterEqual(next_start - first_end, 2.3)
        self.assertGreaterEqual(state.correction_cycles, 2)

    def test_closed_loop_with_two_second_average_and_scale_error(self):
        # Planta simplificada: verifica a integracao, nao substitui teste fisico.
        from collections import deque
        state = TrackerState(has_signal=True, dx_filt_px=8., dy_filt_px=6.)
        clock = [1.0]
        position = np.array([8., 6.])
        velocity = np.zeros(2)
        frames = deque([(1.0, position.copy())])
        estimated = np.array([[-6200., 540.], [320., 7300.]])
        actual = estimated @ np.diag([1.53, 1.15])
        norms = []

        def move(axis, rate, unused):
            velocity[axis] = rate

        def sleep(dt):
            clock[0] += dt
            position[:] += actual @ velocity * dt
            frames.append((clock[0], position.copy()))
            while frames[0][0] < clock[0] - 2:
                frames.popleft()
            center = np.mean([p for _, p in frames], axis=0)
            with state.lock:
                state.measurement_seq += 1
                state.measurement_ts = clock[0]
                state.dx_filt_px, state.dy_filt_px = center
                state.stop = clock[0] > 100
            norms.append(float(np.linalg.norm(position)))

        with patch.object(tracker_loop.time, "perf_counter", side_effect=lambda: clock[0]), \
                patch.object(tracker_loop.time, "sleep", side_effect=sleep), \
                patch.object(tracker_loop, "move_axis", side_effect=move), \
                patch.object(tracker_loop, "stop_axes_safely"), \
                patch.object(tracker_loop, "CONTROL_HZ", 50):
            tracker_loop.executar_loop_controle(state, np.linalg.inv(estimated))
        self.assertLess(np.median(norms[-500:]), 2.0)
        self.assertLessEqual(max(norms), 10.1)
        self.assertGreater(state.correction_cycles, 1)


if __name__ == "__main__":
    unittest.main()
