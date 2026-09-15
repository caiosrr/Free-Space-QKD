"""A liberacao antecipada do aquecimento so sai quando o desvio e inequivoco."""
import pathlib
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from modulos.controle.tracker_controle import SlowBiasEstimator

JANELA, AQUECIMENTO = 120.0, 60.0
PASSO = 2.0     # as amostras sao medias temporais de 2 s
# A dispersao MEDIDA de uma media temporal de 2 s e 0,85 px no plano. Por eixo,
# portanto, 0,85/raiz(2). Injetar 0,85 em cada eixo daria raiz(2) vezes mais
# ruido do que o sistema tem, e foi o que reprovou estes testes na primeira
# versao.
TURBULENCIA = 0.85 / 2 ** 0.5


def alimentar(est, segundos, desvio, ruido, semente=3):
    rng = np.random.default_rng(semente)
    ultimo = None
    for i in range(int(segundos / PASSO)):
        t = i * PASSO
        ultimo = est.observe(t, desvio[0] + rng.normal(0, ruido),
                             desvio[1] + rng.normal(0, ruido))
    return ultimo


def novo():
    return SlowBiasEstimator(window_s=JANELA, warmup_s=AQUECIMENTO, min_samples=5)


class Teste(unittest.TestCase):
    def test_desvio_grande_libera_antes_do_aquecimento(self):
        """2 px de desvio: ~20 s bastam, contra os 60 s de aquecimento."""
        est = novo()
        e = alimentar(est, 30.0, (2.0, 0.0), TURBULENCIA)
        self.assertTrue(e.ready, "deveria liberar bem antes do aquecimento")
        self.assertLess(e.span_s, AQUECIMENTO)

    def test_desvio_enorme_libera_no_span_minimo(self):
        """5 px: so o span minimo de 8 s segura, e e isso que ele existe para fazer."""
        est = novo()
        self.assertFalse(alimentar(est, 6.0, (5.0, 0.0), TURBULENCIA).ready)
        est = novo()
        self.assertTrue(alimentar(est, 12.0, (5.0, 0.0), TURBULENCIA).ready)

    def test_sem_desvio_espera_o_aquecimento_inteiro(self):
        """So turbulencia em torno de zero: liberar seria perseguir ruido."""
        est = novo()
        e = alimentar(est, 40.0, (0.0, 0.0), TURBULENCIA)
        self.assertFalse(e.ready, f"liberou sem desvio real (raio {e.radius_px:.2f} px)")

    def test_desvio_pequeno_espera(self):
        """0,3 px e da ordem da incerteza: continua exigindo o aquecimento."""
        est = novo()
        e = alimentar(est, 40.0, (0.3, 0.0), TURBULENCIA)
        self.assertFalse(e.ready)

    def test_span_minimo_respeitado(self):
        """Poucos segundos nao bastam nem com desvio enorme."""
        est = novo()
        e = alimentar(est, 6.0, (5.0, 0.0), TURBULENCIA)
        self.assertFalse(e.ready, "6 s e menos que o span minimo de 8 s")

    def test_liberacao_e_trava_de_uma_via(self):
        """Depois de liberado nao volta atras, mesmo com o desvio corrigido."""
        est = novo()
        self.assertTrue(alimentar(est, 30.0, (2.0, 0.0), TURBULENCIA).ready)
        est.deslocar(-2.0, 0.0)          # o mount corrigiu
        e = est.observe(32.0, 0.0, 0.0)
        self.assertTrue(e.ready, "nao pode piscar entre pronto e nao pronto")

    def test_reset_rearma_a_espera(self):
        """Perda de sinal reinicia tudo, inclusive a liberacao."""
        est = novo()
        self.assertTrue(alimentar(est, 30.0, (2.0, 0.0), TURBULENCIA).ready)
        est.reset()
        e = alimentar(est, 10.0, (0.0, 0.0), TURBULENCIA, semente=9)
        self.assertFalse(e.ready)

    def test_turbulencia_alta_exige_mais_desvio(self):
        """O limiar segue a dispersao medida, nao um numero fixo de pixels."""
        calma, agitada = novo(), novo()
        self.assertTrue(alimentar(calma, 16.0, (1.5, 0.0), 0.15).ready)
        self.assertFalse(alimentar(agitada, 16.0, (1.5, 0.0), 2.0).ready)

    def test_aquecimento_normal_continua_valendo(self):
        """Passado o aquecimento, fica pronto mesmo sem desvio nenhum."""
        est = novo()
        e = alimentar(est, AQUECIMENTO + 10.0, (0.0, 0.0), TURBULENCIA)
        self.assertTrue(e.ready)


if __name__ == "__main__":
    unittest.main(verbosity=2)
