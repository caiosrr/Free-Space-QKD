"""A fisica do holograma do DMD, conferida contra a teoria."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.dmd.holograma import (  # noqa: E402
    TelaKolmogorov,
    coordenadas,
    fase_oam,
    funcao_de_estrutura,
    holograma_lee,
    portadora,
)

N = 480


def ordem(ligados: np.ndarray, car: np.ndarray, m: int) -> complex:
    """Amplitude complexa da ordem m: demodula o padrao pela portadora."""
    return complex(np.mean(ligados * np.exp(-1j * m * car)))


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.x, self.y = coordenadas(N, N, N / 2, N / 2)

    def test_so_fase_liga_metade_dos_espelhos_sem_empates(self):
        # Periodo par e o caso dos empates: pixels exatamente onde o cosseno
        # zera. Com 480 multiplo de 8, a fracao tem de ser 1/2 exata.
        car = portadora(self.x, self.y, 8, 0)
        ligados = holograma_lee(np.zeros((N, N)), car)
        self.assertEqual(ligados.mean(), 0.5)

    def test_ordem_mais_um_carrega_a_fase_e_a_menos_um_a_conjugada(self):
        car = portadora(self.x, self.y, 8, 30)
        for fase in (0.4, 1.3, 2.9):
            lig = holograma_lee(np.full((N, N), fase), car).astype(float)
            mais1 = np.angle(ordem(lig, car, 1))
            menos1 = np.angle(ordem(lig, car, -1))
            self.assertAlmostEqual(np.angle(np.exp(1j * (mais1 - fase))), 0.0, delta=0.01)
            self.assertAlmostEqual(np.angle(np.exp(1j * (menos1 + fase))), 0.0, delta=0.01)
            self.assertAlmostEqual(np.angle(ordem(lig, car, 0)), 0.0, delta=1e-9)

    def test_amplitude_da_ordem_mais_um_e_a_sobre_pi(self):
        # Com a fracao acesa d = 1 - arcsin(A)/pi, o primeiro coeficiente de
        # Fourier da onda quadrada vale sin(pi d)/pi = A/pi.
        car = portadora(self.x, self.y, 8, 30)
        for a in (0.3, 0.6, 1.0):
            lig = holograma_lee(np.zeros((N, N)), car, amplitude=a).astype(float)
            self.assertAlmostEqual(abs(ordem(lig, car, 1)), a / np.pi, delta=0.01)

    def test_grade_alinhada_aos_pixels_quantiza_a_fase(self):
        # Registro de uma limitacao real: com a grade a 0 graus, a fase so
        # anda em passos de 2 pi / periodo. Inclinada, o erro some.
        car0 = portadora(self.x, self.y, 8, 0)
        lig = holograma_lee(np.full((N, N), 1.0), car0).astype(float)
        erro = abs(np.angle(np.exp(1j * (np.angle(ordem(lig, car0, 1)) - 1.0))))
        self.assertGreater(erro, 0.1)
        self.assertLess(erro, np.pi / 8 + 1e-6)


class OamTests(unittest.TestCase):
    def test_a_fase_da_ell_voltas_em_torno_do_centro(self):
        x, y = coordenadas(201, 201, 100, 100)
        for ell in (1, 3, -2):
            fase = fase_oam(x, y, ell)
            anel = [(100 + 60 * np.cos(t), 100 + 60 * np.sin(t))
                    for t in np.linspace(0, 2 * np.pi, 721)]
            valores = [fase[int(round(py)), int(round(px))] for px, py in anel]
            saltos = np.angle(np.exp(1j * np.diff(valores)))
            self.assertAlmostEqual(saltos.sum() / (2 * np.pi), ell, delta=0.01)


class TelaKolmogorovTests(unittest.TestCase):
    def test_funcao_de_estrutura_em_r0(self):
        # Teoria: D(r0) = 6,88 rad^2. Com sub-harmonicos, o metodo de FFT fica
        # tipicamente 10 a 20% abaixo; sem eles, bem mais (medido: 46% em 2 r0).
        r0 = 16.0
        medidas = [funcao_de_estrutura(TelaKolmogorov(256, r0, semente=s).janela(0, 0, 256, 256), 16)
                   for s in range(20)]
        razao = np.mean(medidas) / 6.88
        self.assertGreater(razao, 0.7)
        self.assertLess(razao, 1.1)

    def test_fase_escala_com_r0_elevado_a_menos_cinco_sextos(self):
        a = TelaKolmogorov(256, 1.0, semente=3).janela(0, 0, 128, 128)
        b = TelaKolmogorov(256, 10.0, semente=3).janela(0, 0, 128, 128)
        np.testing.assert_allclose(b, a * 10.0 ** (-5.0 / 6.0), atol=1e-9)

    def test_translacao_e_continua_com_o_vento(self):
        tela = TelaKolmogorov(256, 8.0, semente=1)
        a = tela.janela(10, 0, 50, 50)
        b = tela.janela(12, 0, 50, 50)
        # Descontando o piston de cada janela, deslocar 2 px e ler a mesma tela.
        np.testing.assert_allclose(a[:, 2:] - a[:, 2:].mean(), b[:, :-2] - b[:, :-2].mean(), atol=1e-9)


class ProgramaTests(unittest.TestCase):
    def test_nada_acende_fora_da_abertura(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ferramentas"))
        import dmd_holograma as h

        tela = TelaKolmogorov(512, 1.0, semente=0)
        for modo in "gotc":
            e = h.Estado(modo=modo, cx=300, cy=200, raio=80, r0=20.0)
            quadro, *_ = h.montar_holograma(e, 640, 400, tela)
            yy, xx = np.nonzero(quadro)
            self.assertTrue(np.all(np.hypot(xx - 300, yy - 200) <= 80.0 + 1e-9), modo)
        e = h.Estado(modo="p")
        quadro, *_ = h.montar_holograma(e, 640, 400, tela)
        self.assertEqual(int(quadro.max()), 0)


if __name__ == "__main__":
    unittest.main()
