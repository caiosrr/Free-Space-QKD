"""O piso absoluto de contagens nao pode cegar o tracker.

Regressao de 2026-09-06: ao unificar a captura do tracker com a da calibracao,
o piso RAW_SIGNAL_MIN=20 do detector passou a valer tambem para o tracker. Com
a autoexposicao em ~1150 us o pico bruto do beacon fica em ~15 contagens sobre
fundo 2, entao 93,9% dos frames de uma sessao real de 5,9 h seriam zerados.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.visao import detector_ilhas as foco


def frame_fraco(pico=15.0, fundo=2.0, tamanho=64):
    """Beacon tenue como o medido em bancada: pico 15, fundo 2."""
    yy, xx = np.indices((tamanho, tamanho), dtype=np.float32)
    spot = np.exp(-((xx - 32) ** 2 + (yy - 32) ** 2) / (2.0 * 2.5**2))
    return (fundo + (pico - fundo) * spot).astype(np.float32)


class PisoSinalBrutoTests(unittest.TestCase):
    def setUp(self):
        self.frame = frame_fraco()
        self.patcher = patch.object(foco, "capture_raw_frame", return_value=self.frame)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_o_piso_padrao_zera_um_beacon_tenue(self):
        """Confirma que o piso realmente descarta este frame (a premissa)."""
        norm = foco.capture_frame(0.001, min_raw_signal=20.0)
        self.assertEqual(int(norm.max()), 0)

    def test_o_tracker_enxerga_o_mesmo_beacon_tenue(self):
        from modulos.controle import tracker_camera

        norm = tracker_camera.capture_frame(0.001)
        self.assertGreater(int(norm.max()), 200)
        centro = foco.centro_massa(norm)
        self.assertIsNotNone(centro)
        self.assertAlmostEqual(centro[0], 32.0, delta=1.5)
        self.assertAlmostEqual(centro[1], 32.0, delta=1.5)

    def test_a_calibracao_mantem_o_piso(self):
        """Na calibracao a exposicao e alta; o piso deve continuar valendo."""
        self.assertGreater(foco.RAW_SIGNAL_MIN, 0.0)
        norm = foco.capture_frame(0.001)
        self.assertEqual(int(norm.max()), 0)


if __name__ == "__main__":
    unittest.main()
