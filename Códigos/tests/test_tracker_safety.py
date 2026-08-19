import csv
import inspect
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import controle.Tracker as tracker
import controle.mount_control as mount_control
import foco_multiplos.calibracao_foco as calibracao


class TrackerSafetyTests(unittest.TestCase):
    def test_hold_zone_uses_three_frame_hysteresis(self):
        active, count = tracker._update_hold_state(False, 0, 3.0)
        self.assertTrue(active)
        active, count = tracker._update_hold_state(active, count, 7.0)
        self.assertTrue(active)
        active, count = tracker._update_hold_state(active, count, 7.0)
        self.assertTrue(active)
        active, count = tracker._update_hold_state(active, count, 7.0)
        self.assertFalse(active)
        self.assertEqual(count, 0)

    def test_azimuth_offset_handles_zero_degree_wrap(self):
        offset_az, offset_alt = tracker._mount_offsets_from_start(
            359.0,
            10.0,
            4.0,
            9.5,
        )
        self.assertAlmostEqual(offset_az, 5.0)
        self.assertAlmostEqual(offset_alt, -0.5)

    def test_csv_writes_variance_and_forced_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = time.perf_counter()
            logger = tracker.TrackerCsvLogger(Path(tmp), started, 359.0, 10.0, 2.0)
            state = tracker._state_snapshot(
                tracker.SharedState(has_signal=True, dx_filt_px=1.0, dy_filt_px=2.0)
            )
            logger.write(
                started + 0.01,
                state_values=state,
                status="RASTREANDO",
                x_cm=10.0,
                y_cm=20.0,
                target_x=9.0,
                target_y=18.0,
                dx=1.0,
                dy=2.0,
            )
            logger.write(
                started + 0.02,
                state_values=state,
                status="PARADA",
                x_cm=11.0,
                y_cm=19.0,
                target_x=9.0,
                target_y=18.0,
                dx=2.0,
                dy=1.0,
                event="teste",
            )
            logger.close(reason="teste")

            with logger.csv_path.open(encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["evento_seguranca"], "teste")
            self.assertTrue(logger.summary_path.exists())

    def test_multiscale_fit_uses_every_radius(self):
        expected = np.array([[2000.0, 2500.0], [2600.0, -2000.0]])
        records = []
        for radius in (0.004, 0.008, 0.016):
            for label, sign_az, sign_alt in calibracao.DIRECTIONS:
                delta_az = radius * sign_az
                delta_alt = radius * sign_alt
                pixels = expected @ np.array([delta_az, delta_alt])
                records.append(
                    calibracao.RegistroDual(
                        "fine",
                        f"{label}@{radius}",
                        radius,
                        delta_az,
                        delta_alt,
                        0, 0, 1, 1,
                        0, 0, 1, 1,
                        pixels[0], pixels[1], 1, 1,
                        pixels[0], pixels[1],
                        0, 1, 0.5,
                    )
                )

        selected, rejected, excluded = calibracao._prepare_fit_records(records, "fine")
        self.assertEqual(len(selected), 24)
        self.assertFalse(rejected)
        self.assertFalse(excluded)
        fitted = calibracao._fit_robusto_sem_intercepto(selected, "fine")
        self.assertTrue(np.allclose(fitted["A"], expected))

    def test_return_pid_accepts_a_separate_speed_limit(self):
        parameters = inspect.signature(mount_control.move_axes_pid_2d).parameters
        self.assertIn("max_velocity_deg_s", parameters)

    def test_return_to_start_is_limited_and_verified(self):
        positions = iter([(1.0, 2.0), (0.0, 0.0), (0.0, 0.0)])
        movements = []

        def fake_move(mount, delta_az, delta_alt, max_velocity_deg_s=None):
            movements.append((mount, delta_az, delta_alt, max_velocity_deg_s))

        with (
            patch.object(tracker, "read_altaz", side_effect=lambda: next(positions)),
            patch.object(tracker, "move_axes_pid_2d", side_effect=fake_move),
            patch.object(tracker, "stop_axes_safely", return_value=True),
        ):
            result = tracker._return_to_initial_position(0.0, 0.0)

        self.assertTrue(result["success"])
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0][3], tracker.RETURN_MAX_RATE_DEG_S)

    def test_observation_mode_never_sends_axis_velocity(self):
        state = tracker.SharedState()

        def stop_after_first_cycle(_seconds):
            with state.lock:
                state.stop = True

        with (
            patch.object(tracker, "move_axis") as move_axis,
            patch.object(tracker, "stop_axes_safely", return_value=True),
            patch.object(tracker.time, "sleep", side_effect=stop_after_first_cycle),
        ):
            tracker.control_loop_continuo(
                state,
                np.eye(2),
                np.eye(2),
                usar_mount=False,
            )

        move_axis.assert_not_called()

    def test_ids_roi_rounding_recalculates_local_target(self):
        local_x, local_y = tracker._target_local_in_actual_roi(
            2592,
            1944,
            1175.57,
            963.16,
            (252, 256, 1096, 866),
            "direct",
        )
        self.assertAlmostEqual(local_x, 79.57)
        self.assertAlmostEqual(local_y, 97.16)

    def test_runtime_target_selection_does_not_replace_calibration_target(self):
        selection = {
            "x_px": 320.0,
            "y_px": 240.0,
            "signature": {"version": 2, "primary": {"raw_peak": 1}},
        }
        with (
            patch("builtins.input", return_value="1"),
            patch.object(tracker, "reset_camera_roi"),
            patch.object(tracker, "carregar_alvo_salvo") as load_saved,
            patch.object(
                tracker,
                "capture_frame",
                return_value=np.zeros((480, 640), dtype=np.uint8),
            ),
            patch.object(
                tracker.foco_temp,
                "escolher_ilha_manualmente",
                return_value=selection,
            ),
            patch.object(tracker.foco_temp, "reset_focus_lock"),
        ):
            target = tracker.escolher_referencia_tracker(640, 480, "dual")

        self.assertEqual(target.source, "manual_tracker_session")
        self.assertEqual((target.x_px, target.y_px), (320.0, 240.0))
        load_saved.assert_called_once()


if __name__ == "__main__":
    unittest.main()
