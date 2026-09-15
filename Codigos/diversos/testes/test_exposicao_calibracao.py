"""Camera simulada: o ajuste de exposicao converge sem ceifar?"""
import pathlib
import sys
import unittest
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from modulos.calibracao import calibracao_continua_core as cal

class CameraFalsa:
    """Pico linear na exposicao, com cintilacao multiplicativa reprodutivel."""
    def __init__(self, contagens_por_s, cintilacao=1.0, semente=7):
        self.k = contagens_por_s
        self.cint = cintilacao
        self.rng = np.random.default_rng(semente)
        self.chamadas = 0
    def capture_frame(self, exposicao_s, light=True):
        self.chamadas += 1
        fator = 1.0 + (self.cint - 1.0) * self.rng.random()
        pico = self.k * exposicao_s * fator
        img = np.zeros((32, 32), dtype=np.float32)
        yy, xx = np.indices(img.shape)
        img = pico * np.exp(-(((xx-16)**2 + (yy-16)**2) / 8.0)) + 2.0
        bruto = np.clip(img, 0, 255)
        cal.foco.LAST_RAW_FRAME = bruto
        cal.foco.LAST_CAPTURE_STATS = {"raw_max": float(bruto.max()),
                                       "raw_median": float(np.median(bruto))}
        return (bruto / max(bruto.max(), 1) * 255).astype(np.uint8)

class Teste(unittest.TestCase):
    def setUp(self):
        self.orig_capture = cal.foco.capture_frame
        self.orig_raw = cal.foco.LAST_RAW_FRAME
        self.orig_stats = cal.foco.LAST_CAPTURE_STATS
        cal.CALIBRATION_EXPOSURE_SAMPLE_S = 0.02   # o teste nao espera 2 s
    def tearDown(self):
        cal.foco.capture_frame = self.orig_capture
        cal.foco.LAST_RAW_FRAME = self.orig_raw
        cal.foco.LAST_CAPTURE_STATS = self.orig_stats
        cal.CALIBRATION_EXPOSURE_SAMPLE_S = 2.0

    def _rodar(self, cam, partida):
        cal.foco.capture_frame = cam.capture_frame
        return cal._ajustar_exposicao_sem_ceifar(partida)

    def test_desce_de_saturado(self):
        """15000 us saturando de sobra tem que descer ate parar de ceifar."""
        cam = CameraFalsa(contagens_por_s=60000.0)    # 900 contagens a 15 ms
        r = self._rodar(cam, 0.015)
        self.assertEqual(r["clipped_pixels_max"], 0, "ainda ceifa")
        self.assertLessEqual(r["peak_max_observed"], cal.CALIBRATION_PEAK_CEILING)
        self.assertLess(r["exposure_seconds"], 0.015)

    def test_sobe_de_escuro(self):
        """Exposicao curta demais tem que subir ate o pico util."""
        cam = CameraFalsa(contagens_por_s=60000.0)
        r = self._rodar(cam, 0.0002)                  # 12 contagens
        self.assertGreater(r["exposure_seconds"], 0.0002)
        self.assertGreater(r["peak_max_observed"], 100)
        self.assertEqual(r["clipped_pixels_max"], 0)

    def test_cintilacao_nao_deixa_ceifar(self):
        """Com pico ate 2,6x a mediana, o maximo ainda tem que caber."""
        cam = CameraFalsa(contagens_por_s=60000.0, cintilacao=2.6)
        r = self._rodar(cam, 0.015)
        self.assertEqual(r["clipped_pixels_max"], 0)
        self.assertLessEqual(r["peak_max_observed"], cal.CALIBRATION_PEAK_CEILING)

    def test_respeita_o_limite_inferior(self):
        """Beacon absurdamente forte nao pode empurrar a exposicao a zero."""
        cam = CameraFalsa(contagens_por_s=5.0e7)
        r = self._rodar(cam, 0.015)
        self.assertGreaterEqual(r["exposure_seconds"], cal.CALIBRATION_EXPOSURE_MIN_S)

    def test_sinal_ausente_nao_trava(self):
        """Sem luz, devolve a exposicao de partida em vez de girar a toa."""
        cam = CameraFalsa(contagens_por_s=0.0)
        r = self._rodar(cam, 0.015)
        self.assertEqual(r["exposure_seconds"], 0.015)
        self.assertLessEqual(len(r["steps"]), 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
