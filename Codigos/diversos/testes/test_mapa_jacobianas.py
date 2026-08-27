import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mapa_jacobianas import (
    MapaJacobianas,
    NoJacobiana,
    diferenca_az_graus,
    registrar_no,
)


def no(nome, az, alt, escala, raio=0.1):
    A = np.array([[escala, 100.0], [-50.0, escala * 0.9]], dtype=float)
    return NoJacobiana(
        nome=nome,
        azimute_deg=az,
        altitude_deg=alt,
        A=A,
        A_inv=np.linalg.inv(A),
        rms_residual_px=0.5,
        condition_number=float(np.linalg.cond(A)),
        raio_validado_deg=raio,
    )


class MapaJacobianasTests(unittest.TestCase):
    def test_distancia_de_azimute_respeita_passagem_359_0(self):
        self.assertAlmostEqual(diferenca_az_graus(0.02, 359.98), 0.04, places=9)

    def test_recusa_extrapolacao_fora_de_todos_os_nos(self):
        mapa = MapaJacobianas([no("origem", 10.0, 20.0, 5000.0, raio=0.05)])
        self.assertIsNone(mapa.selecionar(10.2, 20.0))

    def test_no_exato_preserva_a_matriz(self):
        origem = no("origem", 10.0, 20.0, 5000.0)
        selecao = MapaJacobianas([origem]).selecionar(10.0, 20.0)
        self.assertIsNotNone(selecao)
        self.assertEqual(selecao.nos_usados, ("origem",))
        self.assertTrue(np.allclose(selecao.A, origem.A))

    def test_interpola_somente_em_sobreposicao_validada(self):
        esquerda = no("esquerda", 10.0, 20.0, 4000.0, raio=0.2)
        direita = no("direita", 10.2, 20.0, 6000.0, raio=0.2)
        selecao = MapaJacobianas([esquerda, direita]).selecionar(10.1, 20.0)
        self.assertIsNotNone(selecao)
        self.assertEqual(selecao.nos_usados, ("esquerda", "direita"))
        self.assertTrue(np.allclose(selecao.A, 0.5 * (esquerda.A + direita.A)))

    def test_carregamento_confere_a_inversa(self):
        A = np.array([[5000.0, 100.0], [-50.0, 4700.0]])
        payload = {
            "version": 1,
            "nodes": [{
                "name": "n1",
                "azimuth_deg": 1.0,
                "altitude_deg": 2.0,
                "A": A.tolist(),
                "A_inv": np.linalg.inv(A).tolist(),
                "validated_radius_deg": 0.01,
                "rms_residual_px": 0.8,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapa.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            mapa = MapaJacobianas.carregar(path)
        self.assertEqual(len(mapa.nos), 1)

    def test_registro_preserva_repeticoes_para_reprodutibilidade(self):
        A = np.array([[5000.0, 100.0], [-50.0, 4700.0]])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapa.json"
            for nome in ("ensaio_1", "ensaio_2"):
                registrar_no(
                    path,
                    nome=nome,
                    azimute_deg=359.99,
                    altitude_deg=2.0,
                    A=A,
                    A_inv=np.linalg.inv(A),
                    rms_residual_px=0.5,
                    raio_validado_deg=0.01,
                )
            mapa = MapaJacobianas.carregar(path)
        self.assertEqual(len(mapa.nos), 2)
        selecao = mapa.selecionar(359.99, 2.0)
        self.assertEqual(selecao.nos_usados, ("ensaio_1", "ensaio_2"))
        self.assertTrue(np.allclose(selecao.pesos, (0.5, 0.5)))


if __name__ == "__main__":
    unittest.main()
