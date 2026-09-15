"""O canto diagonal da zona morta passa a ser corrigido, sem criar oscilacao."""
import pathlib
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from modulos.controle.tracker_pulsos import BoundedCorrectionCycle

TAXA = 0.001042          # deg/s, velocidade dos micropulsos
ESCALA = 9328.4          # px/deg da calibracao de 2026-09-14
MIN_S = 1.0 / 45.0
PX = 1.0 / ESCALA        # um pixel, em graus


def ciclo():
    c = BoundedCorrectionCycle(TAXA, min_s=MIN_S, fine_max_s=0.12,
                               large_max_s=0.25, fraction=0.35,
                               settle_s=0.3, image_window_s=2.0)
    c.phase = "pronto"
    return c


def comandar(c, ex_px, ey_px):
    """Erro em pixels nos dois eixos; a proposta segue o sinal do erro."""
    erro = np.array([ex_px * PX, ey_px * PX])
    return c.command(0.0, 0.0, np.sign(erro) * TAXA, erro, erro,
                     fine=True, enabled=True)


class Teste(unittest.TestCase):
    def setUp(self):
        self.passo_px = MIN_S * TAXA * ESCALA           # 0,216 px
        self.limiar_px = MIN_S * TAXA / 0.35 * ESCALA   # 0,617 px
        # O pulso forcado carrega ganho implicito passo/erro; a trava o limita
        # ao dobro do ganho de projeto.
        self.minimo_forcado_px = self.passo_px / (2 * 0.35)   # 0,309 px

    def test_canto_diagonal_agora_pulsa(self):
        """0,5 px em cada eixo: passa do raio 0,6 e nao alcancava nenhum eixo."""
        c = ciclo()
        cmd = comandar(c, 0.5, 0.5)
        self.assertEqual(np.count_nonzero(cmd), 1, "deve pulsar UM eixo, o dominante")
        self.assertAlmostEqual(float(max(c.deadlines)), MIN_S, places=6,
                               msg="o pulso forcado deve durar o minimo")

    def test_escolhe_o_eixo_dominante(self):
        c = ciclo()
        comandar(c, 0.30, 0.55)
        self.assertGreater(c.deadlines[1], 0.0, "o maior erro e o do eixo 1")
        self.assertEqual(c.deadlines[0], 0.0)

    def test_erro_menor_que_o_passo_nao_move(self):
        """Mover mais que o erro passaria do zero e criaria oscilacao."""
        c = ciclo()
        cmd = comandar(c, self.passo_px * 0.5, self.passo_px * 0.5)
        self.assertFalse(np.any(cmd), "nao pode pulsar abaixo do proprio passo")

    def test_ganho_implicito_limitado(self):
        """Entre o passo e a trava, o ganho passaria de 0,70: nao pode pulsar."""
        c = ciclo()
        meio = (self.passo_px + self.minimo_forcado_px) / 2
        self.assertFalse(np.any(comandar(c, meio, meio)),
                         f"erro de {meio:.3f} px daria ganho {self.passo_px/meio:.2f}")
        c = ciclo()
        acima = self.minimo_forcado_px * 1.05
        self.assertTrue(np.any(comandar(c, acima, acima)),
                        "acima da trava o pulso forcado deve sair")

    def test_ganho_efetivo_no_canto_tipico(self):
        """Raio 0,6 na diagonal: componentes 0,424 px, ganho 0,51."""
        componente = 0.6 / np.sqrt(2)
        c = ciclo()
        self.assertTrue(np.any(comandar(c, componente, componente)))
        ganho = self.passo_px / componente
        self.assertLess(ganho, 2 * 0.35, f"ganho efetivo {ganho:.2f} passou do teto")
        self.assertGreater(ganho, 0.35, "abaixo do ganho de projeto nao faria sentido")

    def test_repouso_continua_parado(self):
        """Sem proposta da porta radial, nada se move."""
        c = ciclo()
        erro = np.array([0.5 * PX, 0.5 * PX])
        cmd = c.command(0.0, 0.0, np.zeros(2), erro, erro, fine=True, enabled=True)
        self.assertFalse(np.any(cmd))

    def test_caso_normal_inalterado(self):
        """Erro grande nos dois eixos segue pulsando os dois, com a duracao usual."""
        c = ciclo()
        cmd = comandar(c, 3.0, 3.0)
        self.assertEqual(np.count_nonzero(cmd), 2)
        esperado = min(3.0 * PX * 0.35 / TAXA, 0.12)
        self.assertAlmostEqual(float(c.deadlines[0]), esperado, places=6)

    def test_um_eixo_acima_do_limiar_nao_forca_o_outro(self):
        c = ciclo()
        cmd = comandar(c, 1.2, 0.1)
        self.assertEqual(np.count_nonzero(cmd), 1)
        self.assertGreater(c.deadlines[0], MIN_S, "o eixo forte usa a duracao normal")

    def test_sinais_incoerentes_nao_forcam_pulso(self):
        """Historico contra o erro atual continua bloqueando tudo."""
        c = ciclo()
        erro = np.array([0.5 * PX, 0.5 * PX])
        cmd = c.command(0.0, 0.0, np.sign(erro) * TAXA, erro, -erro,
                        fine=True, enabled=True)
        self.assertFalse(np.any(cmd))

    def test_sai_do_canto_em_poucos_ciclos(self):
        """Simula so a GEOMETRIA: cada pulso forcado tira um passo do eixo."""
        ex = ey = 0.5
        for _ in range(4):
            if max(abs(ex), abs(ey)) < self.passo_px:
                break
            if abs(ex) >= abs(ey):
                ex -= np.sign(ex) * self.passo_px
            else:
                ey -= np.sign(ey) * self.passo_px
        raio = float(np.hypot(ex, ey))
        self.assertLess(raio, 0.6, f"deveria sair do gatilho radial, ficou em {raio:.3f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
