"""Testes do painel somente-leitura do tracker."""

import json
import sys
from pathlib import Path
import unittest
from urllib.request import urlopen

import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.tracker_dashboard import TrackerDashboard


class TrackerDashboardTests(unittest.TestCase):
    def test_publica_estado_frame_e_interface_sem_controlar_mount(self):
        dashboard = TrackerDashboard(open_browser=False)
        try:
            dashboard.update(
                np.zeros((32, 48), dtype=np.uint8),
                {"has_signal": True, "dx_px": np.float64(1.25)},
            )
            with urlopen(dashboard.url + "api/state", timeout=2) as response:
                state = json.load(response)
            with urlopen(dashboard.url + "api/frame.jpg", timeout=2) as response:
                frame = response.read()
            with urlopen(dashboard.url, timeout=2) as response:
                html = response.read().decode("utf-8")

            self.assertTrue(state["connected"])
            self.assertEqual(state["dx_px"], 1.25)
            self.assertGreater(len(frame), 100)
            self.assertIn("SOMENTE LEITURA", html)
        finally:
            dashboard.close()


if __name__ == "__main__":
    unittest.main()
