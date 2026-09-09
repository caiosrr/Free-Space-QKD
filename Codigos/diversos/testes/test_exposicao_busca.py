"""A exposicao nao pode congelar quando o alvo some com a cena escura.

Regressao da sessao 2026-09-06_01-32-35: o beacon enfraqueceu de 16 para 7
contagens, o alvo se perdeu e a exposicao ficou congelada em
`congelada_alvo_ausente`. So uma exposicao MAIOR traria o alvo de volta, entao
o congelamento criou um impasse e a sessao morreu no limite de 75 s.

Ao mesmo tempo, a busca nao pode disparar quando a cena esta CLARA (amanhecer):
ali o alvo some porque o fundo cresceu, e subir a exposicao piora.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.controle.tracker_exposicao import AutoExposureController


def cena(fundo: float, pico: float = 0.0, tamanho: int = 64) -> np.ndarray:
    """Frame sintetico com fundo uniforme e, opcionalmente, uma mancha."""
    quadro = np.full((tamanho, tamanho), float(fundo), dtype=np.float32)
    if pico > 0:
        yy, xx = np.indices((tamanho, tamanho))
        quadro += pico * np.exp(-(((xx - 32) ** 2 + (yy - 32) ** 2)) / (2 * 4.0**2))
    return quadro


def alimentar(ctrl, quadro, *, presente, confiavel, inicio=0.0, passo=0.1, ciclos=60):
    """Roda varias observacoes e devolve TODOS os motivos vistos.

    A busca dispara a cada `safety_update_s`; entre disparos a exposicao fica
    congelada, entao olhar so a ultima decisao nao diz se ela aconteceu.
    """
    motivos = []
    for i in range(ciclos):
        motivos.append(ctrl.observe(
            inicio + i * passo,
            quadro,
            target_center=(32.0, 32.0) if presente else None,
            target_diameter_px=8.0,
            trusted_target=confiavel,
            target_present=presente,
        ).reason)
    return motivos


class BuscaPorExposicaoTests(unittest.TestCase):
    def novo(self, exposicao=1000.0):
        return AutoExposureController(exposicao, enabled=True, started_at=0.0)

    def test_alvo_ausente_com_cena_escura_faz_a_exposicao_subir(self):
        ctrl = self.novo(1000.0)
        motivos = alimentar(ctrl, cena(fundo=2.0), presente=False, confiavel=False, ciclos=200)
        self.assertGreater(
            ctrl.current_exposure_us, 1000.0,
            "com o alvo ausente e a cena escura a exposicao precisa subir, nao congelar",
        )
        self.assertIn("busca_alvo_ausente_cena_escura", motivos)

    def test_a_busca_respeita_o_teto(self):
        ctrl = self.novo(1000.0)
        alimentar(ctrl, cena(fundo=2.0), presente=False, confiavel=False, ciclos=4000)
        self.assertLessEqual(ctrl.current_exposure_us, ctrl.maximum_us)

    def test_cena_clara_nao_dispara_busca(self):
        """Amanhecer: o alvo some porque o fundo subiu; aumentar seria pior."""
        ctrl = self.novo(4000.0)
        alimentar(ctrl, cena(fundo=245.0), presente=False, confiavel=False, ciclos=200)
        self.assertLessEqual(
            ctrl.current_exposure_us, 4000.0,
            "com a cena saturando a exposicao nunca deve subir",
        )

    def test_anomalia_optica_com_alvo_presente_continua_congelada(self):
        """Se o alvo esta la mas a aparencia mudou, exposicao nao e a resposta."""
        ctrl = self.novo(1000.0)
        motivos = alimentar(ctrl, cena(fundo=2.0, pico=40.0), presente=True, confiavel=False, ciclos=200)
        self.assertEqual(ctrl.current_exposure_us, 1000.0)
        self.assertIn("congelada_anomalia_optica", motivos)
        self.assertNotIn("busca_alvo_ausente_cena_escura", motivos)

    def test_a_rampa_cobre_a_faixa_dentro_do_limite_de_perda(self):
        """A busca so serve se chegar ao topo antes de a sessao ser encerrada.

        O limite de perda de sinal e 75 s. No ritmo do controle normal (10% a
        cada 5 s, com _untrusted_since zerado a cada mudanca) a exposicao levava
        64 s so para dobrar, e chegava tarde demais.
        """
        from modulos.configuracoes.tracker import SIGNAL_LOSS_LIMIT_SECONDS

        ctrl = self.novo(1000.0)
        quadro = cena(fundo=2.0)
        chegada = None
        for i in range(4000):
            agora = i * 0.05
            ctrl.observe(
                agora, quadro, target_center=None, target_diameter_px=8.0,
                trusted_target=False, target_present=False,
            )
            if chegada is None and ctrl.current_exposure_us >= ctrl.maximum_us:
                chegada = agora
                break
        self.assertIsNotNone(chegada, "a busca nunca alcancou o teto")
        self.assertLess(
            chegada, SIGNAL_LOSS_LIMIT_SECONDS * 0.7,
            f"varrer ate o teto levou {chegada:.0f}s, tarde demais para "
            f"o limite de {SIGNAL_LOSS_LIMIT_SECONDS:.0f}s",
        )

    def test_a_rampa_cobre_a_faixa_inteira_a_partir_do_piso(self):
        """A varredura precisa caber no limite partindo do PISO configurado.

        Baixar o piso alarga a faixa a percorrer: de 1000 us eram 18x, de 200 us
        sao 90x. O teste amarra a checagem ao piso real, para uma mudanca futura
        de configuracao nao deixar a busca lenta demais em silencio.
        """
        from modulos.configuracoes.tracker import (
            AUTO_EXPOSURE_MIN_US,
            SIGNAL_LOSS_LIMIT_SECONDS,
        )

        ctrl = self.novo(AUTO_EXPOSURE_MIN_US)
        quadro = cena(fundo=2.0)
        chegada = None
        for i in range(8000):
            agora = i * 0.05
            ctrl.observe(
                agora, quadro, target_center=None, target_diameter_px=8.0,
                trusted_target=False, target_present=False,
            )
            if ctrl.current_exposure_us >= ctrl.maximum_us:
                chegada = agora
                break
        self.assertIsNotNone(chegada, "a busca nunca alcancou o teto")
        self.assertLess(
            chegada, SIGNAL_LOSS_LIMIT_SECONDS * 0.75,
            f"do piso ({AUTO_EXPOSURE_MIN_US:.0f} us) ao teto levou {chegada:.0f}s, "
            f"apertado demais para o limite de {SIGNAL_LOSS_LIMIT_SECONDS:.0f}s",
        )

    def test_busca_nao_dispara_antes_do_tempo_de_espera(self):
        ctrl = self.novo(1000.0)
        alimentar(ctrl, cena(fundo=2.0), presente=False, confiavel=False, passo=0.05, ciclos=40)
        self.assertEqual(ctrl.current_exposure_us, 1000.0)


if __name__ == "__main__":
    unittest.main()
