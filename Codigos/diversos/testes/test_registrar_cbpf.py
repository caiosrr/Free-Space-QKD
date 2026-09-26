"""A medida do ponto no registrador do CBPF."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from programas_principais.registrar_cbpf import medir_ponto  # noqa: E402


def gaussiana(x0, y0, pico, sigma, forma=(300, 400)):
    y, x = np.mgrid[0:forma[0], 0:forma[1]]
    return pico * np.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * sigma ** 2))


class MedirPontoTests(unittest.TestCase):
    def test_acha_o_centro_com_fundo(self):
        quadro = (gaussiana(212.3, 141.7, 150, 6) + 20).astype(np.uint8)
        m = medir_ponto(quadro)
        self.assertAlmostEqual(m["x_px"], 212.3, delta=0.2)
        self.assertAlmostEqual(m["y_px"], 141.7, delta=0.2)
        self.assertAlmostEqual(m["fundo"], 20, delta=1)
        self.assertEqual(m["saturados"], 0)

    def test_reflexo_separado_nao_puxa_o_centroide(self):
        # Um fantasma a 60 px com 70% do pico passa da meia altura, mas nao
        # esta conectado a mancha principal.
        quadro = gaussiana(150, 150, 200, 5) + gaussiana(210, 150, 140, 5)
        m = medir_ponto(quadro.astype(np.uint8))
        self.assertAlmostEqual(m["x_px"], 150, delta=0.3)

    def test_sem_ponto_devolve_none(self):
        rng = np.random.default_rng(0)
        quadro = rng.integers(0, 4, (300, 400)).astype(np.uint8)
        self.assertIsNone(medir_ponto(quadro))

    def test_conta_saturados(self):
        quadro = np.clip(gaussiana(200, 150, 400, 6), 0, 255).astype(np.uint8)
        self.assertGreater(medir_ponto(quadro)["saturados"], 0)


if __name__ == "__main__":
    unittest.main()
