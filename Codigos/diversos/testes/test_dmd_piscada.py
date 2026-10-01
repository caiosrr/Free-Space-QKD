"""Medida da piscada dos espelhos pelo obturador rolante."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "diversos" / "ferramentas"))
import dmd_piscada  # noqa: E402


def quadro(fracao_ligada, periodo=215, linhas=2160, colunas=400, fase=0):
    """Mancha larga e lisa, modulada no tempo (= nas linhas) por pulsos."""
    y = np.arange(linhas)[:, None] / 2  # linhas do vermelho, como a medida ve
    envoltoria = 200 * np.exp(-((y - linhas / 4) / (linhas / 3)) ** 2)
    ligado = ((y + fase) % periodo) < fracao_ligada * periodo
    img = (envoltoria * ligado) * np.ones((1, colunas))
    return np.clip(img, 0, 255).astype(np.uint8)


class PiscadaTests(unittest.TestCase):
    def test_mais_tempo_ligado_mede_mais(self):
        cheio = dmd_piscada.medir_quadro(quadro(0.95))
        metade = dmd_piscada.medir_quadro(quadro(0.5))
        pouco = dmd_piscada.medir_quadro(quadro(0.2))
        self.assertGreater(cheio["ligado"], metade["ligado"])
        self.assertGreater(metade["ligado"], pouco["ligado"])
        # A razao entre grupos e o que se usa; ela segue a fracao verdadeira.
        self.assertAlmostEqual(pouco["ligado"] / cheio["ligado"], 0.2 / 0.95, delta=0.08)

    def test_pulsos_curtos_aumentam_as_trocas(self):
        poucas = dmd_piscada.medir_quadro(quadro(0.5, periodo=215))
        muitas = dmd_piscada.medir_quadro(quadro(0.5, periodo=40))
        self.assertGreater(muitas["trocas"], 3 * poucas["trocas"])


if __name__ == "__main__":
    unittest.main()
