import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.cameras.ids_peak import IDSPeakCamera


class IdsCameraRecoveryTests(unittest.TestCase):
    def _connected_camera(self) -> IDSPeakCamera:
        camera = IDSPeakCamera()
        camera.acquisition_started = True
        camera.data_stream = object()
        camera.current_roi = (256, 256, 100, 200)
        return camera

    def test_timeout_restarts_stream_and_returns_next_frame(self):
        camera = self._connected_camera()
        expected = np.full((4, 5), 7, dtype=np.uint8)
        with (
            mock.patch.object(camera, "_node", side_effect=RuntimeError("sem nodemap fake")),
            mock.patch.object(camera, "_frame_wait_timeout_ms", return_value=500),
            mock.patch.object(
                camera,
                "_capture_once",
                side_effect=[RuntimeError("GC_ERR_TIMEOUT"), expected],
            ) as capture_once,
            mock.patch.object(camera, "_restart_stream_after_failure") as restart,
        ):
            result = camera.capture(0.001)

        restart.assert_called_once_with()
        self.assertEqual(capture_once.call_count, 2)
        np.testing.assert_array_equal(result, expected)

    def test_failed_restart_reports_capture_and_recovery_errors(self):
        camera = self._connected_camera()
        with (
            mock.patch.object(camera, "_node", side_effect=RuntimeError("sem nodemap fake")),
            mock.patch.object(camera, "_frame_wait_timeout_ms", return_value=500),
            mock.patch.object(camera, "_capture_once", side_effect=RuntimeError("timeout")),
            mock.patch.object(
                camera,
                "_restart_stream_after_failure",
                side_effect=RuntimeError("restart falhou"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "nao foi possivel reiniciar"):
                camera.capture(0.001)

    def test_second_timeout_reconnects_device_and_preserves_roi(self):
        camera = self._connected_camera()
        expected = np.full((3, 3), 9, dtype=np.uint8)
        with (
            mock.patch.object(camera, "_node", side_effect=RuntimeError("sem nodemap fake")),
            mock.patch.object(camera, "_frame_wait_timeout_ms", return_value=500),
            mock.patch.object(
                camera,
                "_capture_once",
                side_effect=[RuntimeError("timeout 1"), RuntimeError("timeout 2"), expected],
            ),
            mock.patch.object(camera, "_restart_stream_after_failure"),
            mock.patch.object(camera, "_reconnect_device_preserving_roi") as reconnect,
        ):
            result = camera.capture(0.001)

        reconnect.assert_called_once_with((256, 256, 100, 200))
        np.testing.assert_array_equal(result, expected)

    def test_wait_timeout_is_three_frames_with_safe_minimum(self):
        camera = self._connected_camera()
        frame_rate_node = mock.Mock()
        frame_rate_node.Value.return_value = 30.0
        with mock.patch.object(camera, "_node", return_value=frame_rate_node):
            self.assertEqual(camera._frame_wait_timeout_ms(), 500)

    def test_set_roi_reapplies_requested_frame_rate(self):
        camera = IDSPeakCamera()
        camera.nodemap = object()
        camera.data_stream = object()
        camera.acquisition_started = True

        with (
            mock.patch.object(camera, "_stop_acquisition"),
            mock.patch.object(camera, "_node", side_effect=lambda name: name),
            mock.patch.object(
                camera,
                "_set_integer",
                side_effect=[0, 0, 516, 512, 1080, 866],
            ),
            mock.patch.object(camera, "_configure_frame_rate", return_value=50.0) as configure,
            mock.patch.object(camera, "_start_acquisition"),
        ):
            roi = camera.set_roi(512, 512, 1080, 866)

        configure.assert_called_once_with()
        self.assertEqual(roi, (516, 512, 1080, 866))


if __name__ == "__main__":
    unittest.main()
