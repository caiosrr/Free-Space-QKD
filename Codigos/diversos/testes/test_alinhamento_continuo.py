import csv
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.visao import alinhamento_continuo as alinhamento
from modulos.controle.mapa_jacobianas import MapaJacobianas, NoJacobiana


class FocoFalso:
    EXPOSURE_SECONDS = 0.001

    def __init__(self, medidas):
        self.medidas = iter(medidas)

    def capture_frame(self, exposure_seconds, light=True):
        return np.zeros((20, 30), dtype=np.uint8)

    def centro_massa(self, frame):
        return next(self.medidas)


class AlinhamentoContinuoTests(unittest.TestCase):
    def test_mediana_rejeita_perda_borda_e_outlier(self):
        foco = FocoFalso([
            None,
            (10.0, 20.0, 100.0, False),
            (500.0, 600.0, 120.0, True),
            (11.0, 19.0, 101.0, False),
            (10.5, 20.5, 99.0, False),
        ])
        medicao, frame = alinhamento._medir_ilha(foco, quantidade=5)
        self.assertEqual(frame.shape, (20, 30))
        self.assertIsNotNone(medicao)
        self.assertAlmostEqual(medicao.x_px, 10.5)
        self.assertAlmostEqual(medicao.y_px, 20.0)
        self.assertEqual(medicao.amostras_validas, 3)

    def test_medicao_insuficiente_retorna_none(self):
        foco = FocoFalso([None, (10.0, 20.0, 100.0, False), None, None, None])
        medicao, _ = alinhamento._medir_ilha(foco, quantidade=5)
        self.assertIsNone(medicao)

    def test_telemetria_e_resumo_sao_persistidos(self):
        with tempfile.TemporaryDirectory() as tmp:
            inicio = time.monotonic()
            log = alinhamento.Telemetria(Path(tmp), inicio, {"mode": "observe_only"})
            log.escrever(
                inicio + 0.1,
                estado="DESVIO",
                sinal=1,
                x_cm_px=10.0,
                y_cm_px=20.0,
                dalt_comando_deg=0.001,
            )
            log.fechar("teste")
            with log.csv_path.open(encoding="utf-8", newline="") as fp:
                linhas = list(csv.DictReader(fp))
            resumo = json.loads(log.resumo_path.read_text(encoding="utf-8"))
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["estado"], "DESVIO")
        self.assertEqual(resumo["mount_movements"], 1)
        self.assertEqual(resumo["stopped_by"], "teste")

    def test_passo_nao_atravessa_buraco_entre_jacobianas(self):
        A = np.eye(2)
        nos = [
            NoJacobiana("inicio", 10.0, 20.0, A, A, 0.1, 1.0, 0.05),
            NoJacobiana("distante", 10.2, 20.0, A, A, 0.1, 1.0, 0.05),
        ]
        mapa = MapaJacobianas(nos)
        passo = alinhamento._limitar_passo_a_cobertura(
            mapa, 10.0, 20.0, 0.2, 0.0, amostras_trajeto=20
        )
        self.assertIsNotNone(passo)
        self.assertLess(passo[0], 0.06)

    def test_passo_e_reduzido_antes_do_limite_absoluto(self):
        passo = alinhamento._limitar_passo_por_offset(
            0.02, 0.0, 0.045, 0.0, limite_deg=0.05
        )
        self.assertIsNotNone(passo)
        self.assertLessEqual(0.045 + passo[0], 0.05)


if __name__ == "__main__":
    unittest.main()
