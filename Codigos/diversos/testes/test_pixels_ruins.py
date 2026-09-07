"""Mascara de pixels defeituosos e escala fisica do enlace.

O caso que motiva a mascara e o regime real medido em bancada: com a
autoexposicao perto de 1150 us o pico do beacon fica em ~15 contagens sobre um
fundo de 2. Nesse nivel um unico pixel quente nao e ruido, e a fonte mais
brilhante do recorte.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.configuracoes import optica
from modulos.visao import detector_ilhas as foco
from modulos.visao import pixels_ruins


def beacon(pico=15.0, fundo=2.0, cx=32.0, cy=32.0, tamanho=64, sigma=2.5):
    """Mancha fraca como a do enlace, em contagens absolutas."""
    yy, xx = np.indices((tamanho, tamanho), dtype=np.float64)
    return fundo + (pico - fundo) * np.exp(
        -((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma**2)
    )


def centro_de_massa(img, limiar_fracao=0.45):
    img = np.asarray(img, dtype=np.float64)
    pesos = img - np.median(img)
    pesos[pesos < pesos.max() * limiar_fracao] = 0.0
    total = pesos.sum()
    yy, xx = np.indices(img.shape, dtype=np.float64)
    return (xx * pesos).sum() / total, (yy * pesos).sum() / total


class MascaraTests(unittest.TestCase):
    def test_marca_quentes_e_frios_num_frame_escuro(self):
        rng = np.random.default_rng(7)
        escuros = [rng.normal(2.0, 0.5, (64, 64)) for _ in range(10)]
        for f in escuros:
            f[10, 20] += 60.0   # quente
            f[40, 50] -= 30.0   # frio
        mascara, stats = pixels_ruins.construir_mascara(escuros)
        self.assertTrue(mascara[10, 20])
        self.assertTrue(mascara[40, 50])
        self.assertEqual(stats["total_defeituosos"], int(mascara.sum()))
        self.assertTrue(stats["plausivel"])

    def test_avisa_quando_a_fracao_e_alta_demais(self):
        """Capturar com luz acesa gera mascara enorme; isso precisa ser sinalizado."""
        rng = np.random.default_rng(3)
        claros = [rng.normal(2.0, 0.5, (64, 64)) + beacon() for _ in range(5)]
        _, stats = pixels_ruins.construir_mascara(claros)
        self.assertFalse(stats["plausivel"])

    def test_um_pixel_quente_desloca_o_centroide_no_regime_fraco(self):
        """A premissa: 15 contagens de sinal, um defeito de 40 domina."""
        limpo = beacon(pico=15.0, fundo=2.0)
        sujo = limpo.copy()
        sujo[20, 50] = 40.0

        x_limpo, y_limpo = centro_de_massa(limpo)
        x_sujo, y_sujo = centro_de_massa(sujo)
        desvio = np.hypot(x_sujo - x_limpo, y_sujo - y_limpo)
        self.assertGreater(desvio, 5.0)

    def test_a_mascara_devolve_o_centroide_ao_lugar(self):
        limpo = beacon(pico=15.0, fundo=2.0)
        sujo = limpo.copy()
        sujo[20, 50] = 40.0
        mascara = np.zeros(limpo.shape, dtype=bool)
        mascara[20, 50] = True

        corrigido = pixels_ruins.corrigir(sujo, mascara)
        x, y = centro_de_massa(corrigido)
        self.assertAlmostEqual(x, 32.0, delta=0.5)
        self.assertAlmostEqual(y, 32.0, delta=0.5)

    def test_a_correcao_nao_mexe_em_pixel_bom(self):
        limpo = beacon()
        vazia = np.zeros(limpo.shape, dtype=bool)
        np.testing.assert_allclose(pixels_ruins.corrigir(limpo, vazia), limpo)

    def test_fatiar_recorta_a_mascara_do_sensor_para_a_roi(self):
        mascara = np.zeros((200, 300), dtype=bool)
        mascara[110, 160] = True
        recorte = pixels_ruins.fatiar(mascara, origem_xy=(150, 100), forma=(64, 64))
        self.assertEqual(recorte.shape, (64, 64))
        self.assertTrue(recorte[10, 10])
        self.assertEqual(int(recorte.sum()), 1)

    def test_fatiar_fora_do_sensor_nao_estoura(self):
        mascara = np.zeros((100, 100), dtype=bool)
        recorte = pixels_ruins.fatiar(mascara, origem_xy=(80, 80), forma=(64, 64))
        self.assertEqual(recorte.shape, (64, 64))
        self.assertFalse(recorte.any())

    def test_salvar_e_carregar(self):
        mascara = np.zeros((16, 16), dtype=bool)
        mascara[3, 4] = True
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "m.npy"
            pixels_ruins.salvar(caminho, mascara)
            np.testing.assert_array_equal(pixels_ruins.carregar(caminho), mascara)
            self.assertIsNone(pixels_ruins.carregar(Path(tmp) / "nao_existe.npy"))


class DetectorComMascaraTests(unittest.TestCase):
    """Ponta a ponta no detector real, nao numa reimplementacao do centroide.

    A normalizacao divide pelo maximo do frame. Um pixel quente acima do pico
    do beacon rebaixa a mancha inteira e ela deixa de sobreviver ao limiar: o
    detector nao erra o centro, ele PERDE o alvo. Isso e compativel com os 72
    episodios curtos de ausencia observados numa sessao de 5,9 h.
    """

    def setUp(self):
        self.limpo = beacon(pico=15.0, fundo=2.0, cx=64.0, cy=64.0, tamanho=128)
        self.mascara = np.zeros((128, 128), dtype=bool)
        self.mascara[40, 95] = True
        foco.set_focus_mode("dual")
        self.addCleanup(foco.definir_mascara_pixels_ruins, None)
        self.addCleanup(foco.reset_focus_lock)

    def _medir(self, frame, usar_mascara):
        foco.definir_mascara_pixels_ruins(self.mascara if usar_mascara else None)
        foco.reset_focus_lock()
        with patch.object(foco, "capture_raw_frame", return_value=frame):
            norm = foco.capture_frame(0.001, min_raw_signal=0.0)
        return foco.centro_massa(norm)

    def test_frame_limpo_e_detectado(self):
        centro = self._medir(self.limpo, usar_mascara=False)
        self.assertIsNotNone(centro)
        self.assertAlmostEqual(centro[0], 64.0, delta=1.0)

    def test_pixel_quente_modesto_ja_cega_o_detector(self):
        sujo = self.limpo.copy()
        sujo[40, 95] = 25.0  # so 10 contagens acima do pico do beacon
        self.assertIsNone(self._medir(sujo, usar_mascara=False))

    def test_a_mascara_recupera_a_deteccao(self):
        for contagens in (25.0, 45.0, 90.0):
            with self.subTest(pixel_quente=contagens):
                sujo = self.limpo.copy()
                sujo[40, 95] = contagens
                self.assertIsNone(self._medir(sujo, usar_mascara=False))
                centro = self._medir(sujo, usar_mascara=True)
                self.assertIsNotNone(centro)
                self.assertAlmostEqual(centro[0], 64.0, delta=1.0)
                self.assertAlmostEqual(centro[1], 64.0, delta=1.0)

    def test_o_detector_reporta_incerteza_do_centroide(self):
        self._medir(self.limpo, usar_mascara=False)
        selecionado = foco.get_focus_debug()["selected"]
        self.assertGreater(selecionado["sigma_centroide_px"], 0.0)
        self.assertGreater(selecionado["raio_rms_px"], 0.0)


class EscalaOpticaTests(unittest.TestCase):
    def test_escala_bate_com_a_geometria(self):
        esperado = optica.PIXEL_PITCH_M / optica.FOCAL_LENGTH_M
        self.assertAlmostEqual(optica.escala_rad_por_px(), esperado)
        self.assertAlmostEqual(
            optica.metros_por_px_no_alvo(1000.0), esperado * 1000.0
        )

    def test_ida_e_volta_entre_px_e_metros(self):
        self.assertAlmostEqual(optica.metros_para_px(optica.px_para_metros(3.7)), 3.7)

    def test_configuracao_atual_e_plausivel(self):
        self.assertEqual(optica.avisos(), [])

    def test_focal_em_milimetros_e_denunciada(self):
        """O erro de unidade que corrompe tudo em silencio."""
        original = optica.FOCAL_LENGTH_M
        try:
            optica.FOCAL_LENGTH_M = 700.4  # mm no lugar de m
            avisos = optica.avisos()
            self.assertTrue(any("FOCAL_LENGTH_M" in a for a in avisos))
            self.assertTrue(any("milimetros" in a for a in avisos))
        finally:
            optica.FOCAL_LENGTH_M = original


if __name__ == "__main__":
    unittest.main()
