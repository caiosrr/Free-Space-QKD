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


class OrientacaoDaMascaraTests(unittest.TestCase):
    """A mascara precisa viver no mesmo espaco em que e aplicada.

    `LAST_RAW_FRAME` ja passou pela rotacao de exibicao, mas
    `aplicar_pixels_ruins` age no frame CRU, antes dela. Sem desfazer a
    rotacao, a mascara sairia espelhada em 180 graus e corrigiria o canto
    oposto ao dos defeitos reais.
    """

    TAMANHO = 32
    DEFEITO = (4, 7)  # linha, coluna: assimetrico de proposito

    def _mascara_construida(self, rotacionar: bool):
        """Roda a calibracao e devolve a mascara que ela mandou salvar."""
        from modulos.visao import diagnostico

        cru = np.full((self.TAMANHO, self.TAMANHO), 2.0, dtype=np.float32)
        cru[self.DEFEITO] = 200.0
        capturadas = {}

        def capturar(caminho, mascara):
            capturadas["mascara"] = np.array(mascara, copy=True)
            return caminho

        with patch.object(foco, "capture_raw_frame", return_value=cru),                 patch.object(foco, "ROTATE_IMAGE_180", rotacionar),                 patch.object(diagnostico.pixels_ruins, "salvar", capturar),                 patch("builtins.input", return_value=""):
            diagnostico.calibrar_pixels_ruins(0.001, 5)

        self.assertIn("mascara", capturadas, "a calibracao nao salvou mascara")
        return capturadas["mascara"]

    def _espelhado(self):
        linha, coluna = self.DEFEITO
        return (self.TAMANHO - 1 - linha, self.TAMANHO - 1 - coluna)

    def test_com_rotacao_a_mascara_volta_para_o_sensor(self):
        mascara = self._mascara_construida(rotacionar=True)
        self.assertTrue(
            mascara[self.DEFEITO],
            "com rotacao ligada o defeito precisa ficar na posicao do SENSOR",
        )
        self.assertFalse(
            mascara[self._espelhado()],
            "a posicao espelhada nao pode estar marcada",
        )

    def test_sem_rotacao_a_mascara_nao_e_mexida(self):
        mascara = self._mascara_construida(rotacionar=False)
        self.assertTrue(mascara[self.DEFEITO])
        self.assertFalse(mascara[self._espelhado()])
