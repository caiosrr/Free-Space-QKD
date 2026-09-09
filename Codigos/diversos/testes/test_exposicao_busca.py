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
        # Depois da rampa ainda e preciso reconstruir a media temporal antes de
        # o alvo voltar a ser aceito; o orcamento inteiro tem de caber no limite.
        from modulos.configuracoes.tracker import (
            AUTO_EXPOSURE_LOSS_SEARCH_SECONDS,
            TEMPORAL_WINDOW_SECONDS,
        )

        orcamento = chegada + TEMPORAL_WINDOW_SECONDS
        self.assertLess(
            orcamento, SIGNAL_LOSS_LIMIT_SECONDS,
            f"do piso ({AUTO_EXPOSURE_MIN_US:.0f} us) ao teto sao {chegada:.0f}s e "
            f"mais {TEMPORAL_WINDOW_SECONDS:.0f}s de media: {orcamento:.0f}s nao cabe "
            f"no limite de {SIGNAL_LOSS_LIMIT_SECONDS:.0f}s",
        )
        self.assertGreaterEqual(
            SIGNAL_LOSS_LIMIT_SECONDS - orcamento, AUTO_EXPOSURE_LOSS_SEARCH_SECONDS,
            "margem menor que a propria espera inicial da busca",
        )

    def test_busca_nao_dispara_antes_do_tempo_de_espera(self):
        ctrl = self.novo(1000.0)
        alimentar(ctrl, cena(fundo=2.0), presente=False, confiavel=False, passo=0.05, ciclos=40)
        self.assertEqual(ctrl.current_exposure_us, 1000.0)


if __name__ == "__main__":
    unittest.main()


class PisoDeSinalTests(unittest.TestCase):
    """A reducao nao pode descer em catraca ate o sinal virar quantizacao.

    Em 2026-09-09 a exposicao caiu de 788 para 426 us com o CNR parado entre 16
    e 17. Num sensor de 8 bits com poucas dezenas de contagens, um corte de 5%
    pode nao mudar nenhum inteiro lido: o CNR aparenta nao ter caido e o
    controlador corta de novo, indefinidamente.
    """

    def alimentar_com_mancha(self, ctrl, pico, fundo=3.0, ciclos=80, inicio=0.0):
        """Cena com uma mancha de amplitude conhecida sobre fundo baixo."""
        tamanho = 64
        yy, xx = np.indices((tamanho, tamanho))
        quadro = np.full((tamanho, tamanho), float(fundo), dtype=np.float32)
        quadro += (pico - fundo) * np.exp(-(((xx - 32) ** 2 + (yy - 32) ** 2)) / (2 * 4.0**2))
        motivos = []
        for i in range(ciclos):
            motivos.append(
                ctrl.observe(
                    inicio + i * 0.2, quadro,
                    target_center=(32.0, 32.0), target_diameter_px=10.0,
                    trusted_target=True, target_present=True,
                ).reason
            )
        return motivos

    def test_sinal_forte_ainda_permite_reduzir(self):
        ctrl = AutoExposureController(4000.0, enabled=True, started_at=0.0)
        motivos = self.alimentar_com_mancha(ctrl, pico=200.0)
        self.assertIn("reducao_cnr_com_folga", motivos)
        self.assertLess(ctrl.current_exposure_us, 4000.0)

    def test_sinal_fraco_trava_a_reducao(self):
        """Com o sinal ja perto da quantizacao, cortar mais e sempre errado."""
        ctrl = AutoExposureController(4000.0, enabled=True, started_at=0.0)
        motivos = self.alimentar_com_mancha(ctrl, pico=20.0)
        self.assertIn("piso_de_sinal_atingido", motivos)
        self.assertEqual(ctrl.current_exposure_us, 4000.0)

    def test_o_piso_e_maior_que_o_alvo_de_cnr(self):
        """Senao o CNR sozinho decidiria antes de o piso ter chance de agir."""
        from modulos.configuracoes.tracker import (
            AUTO_EXPOSURE_CNR_HIGH,
            AUTO_EXPOSURE_MIN_TARGET_LEVEL,
        )

        self.assertGreater(AUTO_EXPOSURE_MIN_TARGET_LEVEL, AUTO_EXPOSURE_CNR_HIGH)


class BuscaNaoPodeEstourarACenaTests(unittest.TestCase):
    """Regressao da sessao 2026-09-09_06-07-49.

    A busca criada para o impasse de 06/09 tinha o defeito espelhado. Ela olhava
    o fundo de AGORA para decidir se podia subir, mas a rampa e multiplicativa e
    leva o fundo junto: partindo de 764 us com fundo 23, oito degraus levaram a
    exposicao a 7584 us e o fundo a 255 contagens em 23 s. Com o quadro inteiro
    saturado o alvo nao tinha contraste em lugar nenhum, e a reducao nao roda sem
    alvo confiavel: a exposicao ficou presa no topo por 4 min ate a sessao morrer
    no limite de 90 s. Antes o impasse era no piso; depois passou a ser no teto.

    Estes testes usam uma cena cujo fundo ESCALA com a exposicao, que e o que os
    testes anteriores nao faziam: com fundo fixo em 2 contagens nenhuma rampa
    estoura nada e o defeito passava despercebido.
    """

    FUNDO_POR_US = 23.0 / 764.0  # medido na sessao: 23 contagens a 764 us

    def rodar(self, ctrl, *, ciclos, passo=0.1, inicio=0.0, presente=False):
        """Alimenta o controlador com a cena que a exposicao ATUAL produziria."""
        historico = []
        for i in range(ciclos):
            fundo = min(255.0, ctrl.current_exposure_us * self.FUNDO_POR_US)
            decisao = ctrl.observe(
                inicio + i * passo,
                cena(fundo=fundo),
                target_center=(32.0, 32.0) if presente else None,
                target_diameter_px=8.0,
                trusted_target=False,
                target_present=presente,
            )
            historico.append((ctrl.current_exposure_us, fundo, decisao.reason))
        return historico

    def test_a_rampa_nao_satura_a_cena_que_ela_vasculha(self):
        from modulos.configuracoes.tracker import (
            AUTO_EXPOSURE_LOSS_SEARCH_BACKGROUND_LIMIT,
        )

        ctrl = AutoExposureController(764.0, enabled=True, started_at=0.0)
        historico = self.rodar(ctrl, ciclos=600)
        fundo_maximo = max(fundo for _, fundo, _ in historico)
        self.assertLess(
            fundo_maximo,
            AUTO_EXPOSURE_LOSS_SEARCH_BACKGROUND_LIMIT * 1.35,
            f"a busca levou o fundo a {fundo_maximo:.0f} contagens; com a cena "
            "estourada nenhuma reaquisicao e possivel",
        )
        self.assertLess(fundo_maximo, 200.0, "cena praticamente saturada")

    def test_a_busca_desfaz_a_rampa_quando_falha(self):
        """A busca e uma hipotese com prazo, nao um caminho so de ida."""
        ctrl = AutoExposureController(764.0, enabled=True, started_at=0.0)
        historico = self.rodar(ctrl, ciclos=1500)
        pico = max(exp for exp, _, _ in historico)
        self.assertGreater(pico, 764.0, "a busca precisa ter subido de fato")
        self.assertIn("busca_alvo_ausente_sem_exito", [r for _, _, r in historico])
        self.assertAlmostEqual(
            ctrl.current_exposure_us, 764.0, delta=1.0,
            msg=f"apos falhar a busca subiu ate {pico:.0f} us e parou em "
                f"{ctrl.current_exposure_us:.0f} us, em vez de voltar a 764 us",
        )

    def test_a_busca_nao_reinicia_sozinha_depois_de_falhar(self):
        """Senao a rampa vira um ciclo sobe-desce que nunca termina."""
        ctrl = AutoExposureController(764.0, enabled=True, started_at=0.0)
        historico = self.rodar(ctrl, ciclos=3000)
        retornos = [r for _, _, r in historico].count("busca_alvo_ausente_sem_exito")
        self.assertEqual(
            retornos, 1,
            f"a busca falhou e recomecou {retornos} vezes; deve esperar um alvo "
            "confiavel antes de tentar de novo",
        )
        self.assertAlmostEqual(ctrl.current_exposure_us, 764.0, delta=1.0)

    def test_alvo_confiavel_rearma_a_busca(self):
        """Depois de reencontrar o alvo, uma nova perda merece nova tentativa."""
        ctrl = AutoExposureController(764.0, enabled=True, started_at=0.0)
        self.rodar(ctrl, ciclos=1500)
        self.assertTrue(ctrl._loss_search_exhausted)
        ctrl.observe(
            200.0, cena(fundo=23.0, pico=60.0),
            target_center=(32.0, 32.0), target_diameter_px=8.0,
            trusted_target=True, target_present=True,
        )
        self.assertFalse(ctrl._loss_search_exhausted)

    def test_cena_escura_ainda_varre_a_faixa_inteira(self):
        """A correcao nao pode desfazer o conserto de 06/09.

        Numa noite de verdade o fundo e de poucas contagens e a rampa continua
        podendo ir ate o teto: o limite e o fundo produzido, nao a exposicao.
        """
        ctrl = AutoExposureController(1000.0, enabled=True, started_at=0.0)
        alimentar(ctrl, cena(fundo=2.0), presente=False, confiavel=False, ciclos=4000)
        self.assertEqual(ctrl.current_exposure_us, ctrl.maximum_us)
