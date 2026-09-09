"""O modo sombra observa e registra, mas nunca comanda.

Existe para decidir com dado se vale apertar a zona de repouso: nas sessoes de
04/09 e 06/09 sobrou um vies parado de 0,60 e 0,47 px, abaixo dos 2,0 px que
acordam o controle, custando 23% e 16% do erro mediano.
"""

import sys
import unittest
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.configuracoes import tracker as cfg
from modulos.controle import tracker_loop
from modulos.controle.tracker_controle import SlowBiasEstimator
from modulos.controle.tracker_estado import TrackerState


class ModoSombraTests(unittest.TestCase):
    def test_o_estado_expoe_os_campos_de_sombra(self):
        nomes = {campo.name for campo in fields(TrackerState)}
        for campo in (
            "shadow_bias_dx_px", "shadow_bias_dy_px", "shadow_bias_radius_px",
            "shadow_bias_ready", "shadow_bias_window_s", "shadow_corrections",
        ):
            self.assertIn(campo, nomes)

    def test_a_janela_da_sombra_e_mais_longa_que_a_do_controle(self):
        """Se fosse igual, nao mediria nada novo."""
        self.assertGreater(cfg.SHADOW_BIAS_WINDOW_SECONDS, cfg.SLOW_BIAS_WINDOW_SECONDS)

    def test_o_limiar_da_sombra_e_menor_que_o_que_acorda_o_controle(self):
        """A sombra existe justamente para enxergar o vies que o controle ignora."""
        self.assertLess(cfg.SHADOW_BIAS_TRIGGER_PX, cfg.HOLD_EXIT_RADIUS_PX)

    def test_nenhum_campo_de_sombra_entra_em_decisao_de_comando(self):
        """Garante que a sombra e so registro: nada de shadow_* fora do bloco
        de publicacao do estado."""
        fonte = Path(tracker_loop.__file__).read_text(encoding="utf-8")
        corpo = fonte.split("def executar_loop_controle")[1]
        for numero, linha in enumerate(corpo.splitlines(), 1):
            crua = linha.strip()
            if "shadow" not in crua or crua.startswith("#"):
                continue
            permitido = (
                crua.startswith("state.shadow_")      # publicacao
                or crua.startswith("shadow_bias")     # o proprio observador
                or crua.startswith("shadow_estimate") # leitura do observador
                or crua.startswith("shadow_corrections")
                or crua.startswith("shadow_armed")
                or crua.startswith("if shadow_")
                or crua.startswith("elif shadow_")
            )
            self.assertTrue(
                permitido,
                f"linha {numero} usa shadow_* fora de observacao/registro: {crua}",
            )

    def test_contagem_hipotetica_nao_conta_oscilacao(self):
        """Uma trava de rearme impede contar a mesma deriva varias vezes."""
        estimador = SlowBiasEstimator(
            window_s=cfg.SHADOW_BIAS_WINDOW_SECONDS,
            warmup_s=cfg.SHADOW_BIAS_WARMUP_SECONDS,
        )
        limiar = cfg.SHADOW_BIAS_TRIGGER_PX
        correcoes, armado = 0, True
        # Deriva sobe, fica, e oscila em torno do limiar sem voltar a metade.
        for i in range(600):
            estimativa = estimador.observe(i * 0.1, limiar * 1.4, 0.0)
            if estimativa.ready:
                if armado and estimativa.radius_px >= limiar:
                    correcoes += 1
                    armado = False
                elif estimativa.radius_px <= limiar * 0.5:
                    armado = True
        self.assertEqual(correcoes, 1)


if __name__ == "__main__":
    unittest.main()


class ABDaZonaDeRepousoTests(unittest.TestCase):
    """O A/B pareado da zona de repouso precisa estar DESLIGADO por padrao.

    Ele mexe em controle de verdade: aperta o raio de entrada e faz o mount
    corrigir mais. Uma sessao longa sem operador nao pode ganhar isso de
    surpresa por um commit.
    """

    def test_desligado_por_padrao(self):
        from modulos.configuracoes import tracker

        self.assertFalse(tracker.HOLD_RADIUS_AB_TEST_ENABLED)

    def test_o_raio_alternativo_e_mais_apertado_e_valido(self):
        from modulos.configuracoes import tracker

        self.assertLess(
            tracker.HOLD_RADIUS_AB_ALTERNATE_PX, tracker.HOLD_ENTER_RADIUS_PX,
            "o A/B so faz sentido comparando contra uma zona MAIS apertada",
        )
        self.assertGreater(tracker.HOLD_RADIUS_AB_ALTERNATE_PX, 0.0)
        self.assertLess(
            tracker.HOLD_RADIUS_AB_ALTERNATE_PX, tracker.HOLD_EXIT_RADIUS_PX,
            "entrar precisa continuar sendo mais restrito que sair",
        )

    def test_o_bloco_cobre_varias_janelas_do_vies(self):
        """Bloco curto demais mistura os dois regimes dentro de uma estimativa."""
        from modulos.configuracoes import tracker

        self.assertGreaterEqual(
            tracker.HOLD_RADIUS_AB_BLOCK_SECONDS,
            5 * tracker.SHADOW_BIAS_WINDOW_SECONDS,
            "cada bloco precisa conter varias janelas de vies para a "
            "comparacao entre regimes nao ficar contaminada",
        )

    def test_a_telemetria_registra_o_raio_ativo(self):
        """Sem a coluna nao da para separar os dois regimes na analise."""
        from modulos.controle import tracker_telemetria

        self.assertIn(
            "zona_parada_raio_px",
            tracker_telemetria.TrackerCsvLogger.FIELDNAMES,
        )


class HistoricoDeFramesTests(unittest.TestCase):
    """O grafico mostra QUE houve descontinuidade, nunca o que a causou.

    O painel ja codifica um JPEG por quadro exibido; guardar os mesmos bytes num
    anel de 120 s custa memoria e nada de CPU. Uma ROI de 256x256 em q82 da
    ~11 kB, entao 120 s a 4 Hz ficam em torno de 5 MB.
    """

    def dashboard(self, **kwargs):
        from modulos.controle.tracker_dashboard import TrackerDashboard

        painel = TrackerDashboard(open_browser=False, **kwargs)
        self.addCleanup(painel.close)
        return painel

    def alimentar(self, painel, quantidade, valor0=10, passo=15, intervalo=0.3):
        import time

        import numpy as np

        marcas = []
        for i in range(quantidade):
            valor = valor0 + i * passo
            painel.update(np.full((64, 64), valor, dtype=np.uint8), {"passo": i})
            marcas.append((time.time(), valor))
            time.sleep(intervalo)
        return marcas

    def test_o_anel_guarda_a_janela_pedida(self):
        painel = self.dashboard(frame_hz=4.0, history_seconds=120.0)
        self.assertEqual(painel._history.maxlen, 480)

    def test_devolve_o_frame_do_instante_pedido(self):
        import urllib.request

        import cv2
        import numpy as np

        painel = self.dashboard(frame_hz=4.0)
        marcas = self.alimentar(painel, 8)
        for quando, valor in (marcas[1], marcas[-1]):
            resposta = urllib.request.urlopen(
                f"{painel.url}api/historico.jpg?t={quando:.3f}"
            )
            img = cv2.imdecode(
                np.frombuffer(resposta.read(), np.uint8), cv2.IMREAD_GRAYSCALE
            )
            self.assertLessEqual(
                abs(int(np.median(img)) - valor), 3,
                f"o frame devolvido para t={quando:.2f} nao e o daquele instante",
            )

    def test_o_anel_descarta_o_que_saiu_da_janela(self):
        painel = self.dashboard(frame_hz=4.0, history_seconds=1.0)
        self.assertEqual(painel._history.maxlen, 4)
        # O intervalo tem de ser maior que o de codificacao (1/frame_hz): o anel
        # so recebe frames que o painel realmente codificou.
        self.alimentar(painel, 7, intervalo=0.3)
        self.assertEqual(len(painel._history), 4, "o anel nao pode crescer sem limite")

    def test_sem_instante_o_pedido_e_recusado(self):
        import urllib.error
        import urllib.request

        painel = self.dashboard()
        self.alimentar(painel, 2, intervalo=0.05)
        with self.assertRaises(urllib.error.HTTPError) as caso:
            urllib.request.urlopen(f"{painel.url}api/historico.jpg")
        self.assertEqual(caso.exception.code, 400)
