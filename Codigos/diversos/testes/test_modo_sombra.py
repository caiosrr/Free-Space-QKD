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


class RegimeLentoDeControleTests(unittest.TestCase):
    """O regime alternativo: janela longa, limiar baixo, ganho alto.

    Motivacao medida em 2026-09-09. De manha o mount pediu ~300 px de movimento
    para uma deriva liquida de 15,9 px (19:1 desperdicado, perseguindo
    turbulencia); a noite a correcao removeu 27% do erro e 96% dos pulsos
    deixaram o resto no MESMO sentido. Mesma raiz: atua sobre o vies de 8 s, que
    e turbulencia, com fracao de 0,35.
    """

    def cfg(self):
        from modulos.configuracoes import tracker

        return tracker

    def test_desligado_por_padrao(self):
        self.assertFalse(self.cfg().CONTROL_AB_TEST_ENABLED)

    def test_o_limiar_cabe_entre_o_menor_pulso_e_o_regime_atual(self):
        """Abaixo do menor pulso o mount nao consegue corrigir; acima do
        disparo atual nao seria experimento nenhum."""
        from modulos.controle.mount_ascom import VEL_MIN_LIMITE
        from modulos.controle.tracker_loop import CONTROL_HZ

        t = self.cfg()
        escala_px_por_grau = 9450.0  # calibracao de 2026-09-09 19:20, validada
        menor_pulso_px = (1.0 / CONTROL_HZ) * VEL_MIN_LIMITE * escala_px_por_grau
        self.assertGreater(
            t.CONTROL_SLOW_TRIGGER_PX, 2 * menor_pulso_px,
            f"o limiar de {t.CONTROL_SLOW_TRIGGER_PX} px esta perto demais do "
            f"menor pulso possivel ({menor_pulso_px:.2f} px): o mount nao "
            "consegue corrigir com essa resolucao e ficaria em catraca",
        )
        self.assertLess(
            t.CONTROL_SLOW_TRIGGER_PX, t.HOLD_EXIT_RADIUS_PX,
            "quem dispara hoje e HOLD_EXIT; o regime novo tem de disparar antes",
        )

    def test_a_janela_longa_promedia_a_turbulencia(self):
        """Curta demais e ela mede turbulencia, que e o defeito atual."""
        t = self.cfg()
        self.assertGreaterEqual(
            t.CONTROL_SLOW_WINDOW_SECONDS, 10 * t.SLOW_BIAS_WINDOW_SECONDS,
            "a janela do regime novo precisa ser muito maior que a de 8 s",
        )

    def test_o_bloco_do_ab_cabe_varias_janelas(self):
        t = self.cfg()
        self.assertGreaterEqual(
            t.CONTROL_AB_BLOCK_SECONDS, 2 * t.CONTROL_SLOW_WINDOW_SECONDS,
            "bloco curto demais mistura os regimes dentro de uma estimativa",
        )

    def test_a_telemetria_separa_os_regimes(self):
        from modulos.controle import tracker_telemetria

        self.assertIn(
            "regime_controle", tracker_telemetria.TrackerCsvLogger.FIELDNAMES,
            "sem a coluna nao da para separar os blocos na analise",
        )

    def test_o_pulso_entrega_o_que_a_fracao_pede_no_regime_novo(self):
        """Com 0,90 sobre um desvio de 0,6 px o pulso nao pode saturar no teto.

        No regime atual isso acontece: com erro de 2,2 px, uma fracao alta pede
        mais que os 120 ms de teto e o pulso entrega so 1,10 px.
        """
        from modulos.controle.mount_ascom import VEL_MIN_LIMITE
        from modulos.controle.tracker_controle import FinePulseAxis
        from modulos.controle.tracker_loop import (
            CONTROL_HZ,
            FINE_PULSE_MAX_S,
            FINE_PULSE_SETTLE_S,
        )

        t = self.cfg()
        escala = 9450.0
        eixo = FinePulseAxis(
            VEL_MIN_LIMITE,
            correction_fraction=t.CONTROL_SLOW_FRACTION,
            min_pulse_s=1.0 / CONTROL_HZ,
            max_pulse_s=FINE_PULSE_MAX_S,
            settle_s=FINE_PULSE_SETTLE_S,
        )
        erro_px = t.CONTROL_SLOW_TRIGGER_PX
        taxa = eixo.command(0.0, erro_px / escala, True)
        self.assertNotEqual(taxa, 0.0, "o pulso deveria ter disparado")
        entregue = (eixo._pulse_until - 0.0) * VEL_MIN_LIMITE * escala
        pedido = erro_px * t.CONTROL_SLOW_FRACTION
        self.assertAlmostEqual(
            entregue, pedido, delta=0.05,
            msg=f"o pulso pediu {pedido:.2f} px e entregou {entregue:.2f} px; "
                "o teto de duracao esta limitando o regime novo",
        )


class GanhoDoPulsoTests(unittest.TestCase):
    """Quem define a duracao do pulso e BoundedCorrectionCycle.fraction.

    O FinePulseAxis so entra como PROPOSTA, para conferir o sinal: a duracao
    real sai de min(|erro|, |erro_atual|) * fraction / velocidade_minima. Mexer
    so no FinePulseAxis deixa o ganho inalterado, e foi esse o engano na
    primeira versao do A/B de controle.
    """

    def ciclo(self, fraction):
        from modulos.controle.mount_ascom import VEL_MIN_LIMITE
        from modulos.controle.tracker_pulsos import BoundedCorrectionCycle

        return BoundedCorrectionCycle(
            VEL_MIN_LIMITE, min_s=1 / 34.0, fine_max_s=0.12,
            fraction=fraction, image_window_s=2.0,
        )

    def duracao_px(self, fraction, erro_px, escala=9450.0):
        """Quantos pixels o pulso entrega para um erro dado."""
        from modulos.controle.mount_ascom import VEL_MIN_LIMITE

        ciclo = self.ciclo(fraction)
        erro_deg = erro_px / escala
        ciclo.command(
            0.0, 0.0, (VEL_MIN_LIMITE, VEL_MIN_LIMITE),
            (erro_deg, erro_deg), (erro_deg, erro_deg), fine=True, enabled=True,
        )
        return float(ciclo.deadlines[0]) * VEL_MIN_LIMITE * escala

    def test_a_fracao_do_ciclo_muda_a_entrega(self):
        baixa = self.duracao_px(0.35, 1.5)
        alta = self.duracao_px(0.90, 1.5)
        self.assertGreater(
            alta, baixa * 2,
            "a fracao do BoundedCorrectionCycle e o ganho real; se mexer nela "
            "nao mudar a entrega, o A/B de controle nao esta testando nada",
        )

    def test_o_teto_de_duracao_limita_erros_grandes(self):
        """Com 9450 px/grau, 120 ms valem 1,18 px: nenhum pulso passa disso."""
        entregue = self.duracao_px(1.0, 5.0)
        self.assertLess(
            entregue, 1.2,
            f"o pulso entregou {entregue:.2f} px; o teto de 120 ms deveria "
            "limitar em ~1,10 px",
        )

    def test_no_regime_novo_o_pulso_nao_satura(self):
        """O limiar baixo existe para o pulso nunca encostar no teto."""
        from modulos.configuracoes import tracker

        entregue = self.duracao_px(
            tracker.CONTROL_SLOW_FRACTION, tracker.CONTROL_SLOW_TRIGGER_PX
        )
        pedido = tracker.CONTROL_SLOW_TRIGGER_PX * tracker.CONTROL_SLOW_FRACTION
        self.assertAlmostEqual(entregue, pedido, delta=0.06)

    def test_pulso_contra_o_erro_atual_e_bloqueado(self):
        """Estimativa velha nao pode comandar contra o que a camera ve agora."""
        from modulos.controle.mount_ascom import VEL_MIN_LIMITE

        ciclo = self.ciclo(0.90)
        erro = 1.5 / 9450.0
        cmd = ciclo.command(
            0.0, 0.0, (VEL_MIN_LIMITE, VEL_MIN_LIMITE),
            (erro, erro), (-erro, -erro), fine=True, enabled=True,
        )
        self.assertEqual(float(cmd[0]), 0.0)
        self.assertEqual(float(cmd[1]), 0.0)


class PainelIntegroTests(unittest.TestCase):
    """O painel quebra em silencio: um id que o JS pede e o HTML nao tem vira
    ``null.textContent`` e derruba o laco inteiro, sem nada no terminal."""

    def arquivos(self):
        from pathlib import Path

        from modulos.controle import tracker_dashboard

        base = Path(tracker_dashboard.ASSET_DIR)
        return (
            (base / "app.js").read_text(encoding="utf-8"),
            (base / "index.html").read_text(encoding="utf-8"),
            (base / "styles.css").read_text(encoding="utf-8"),
        )

    def test_todo_id_pedido_pelo_js_existe_no_html(self):
        import re

        js, html, _ = self.arquivos()
        inicio = js.index("const ui = {};")
        fim = js.index(".forEach((id) => { ui[id] = $(id); });")
        pedidos = re.findall(r'"([a-z0-9-]+)"', js[inicio:fim])
        existentes = set(re.findall(r'id="([^"]+)"', html))
        faltando = sorted(set(pedidos) - existentes)
        self.assertEqual(faltando, [], f"ids ausentes no HTML: {faltando}")
        self.assertGreater(len(pedidos), 20, "a lista de ids nao foi encontrada")

    def test_hidden_vence_os_display_flex(self):
        """Sem isto, `el.hidden = true` nao esconde nada.

        Regressao real: a faixa de revisao continuava na tela depois de
        "voltar ao vivo", porque `.review { display: flex }` sobrepoe o
        `[hidden] { display: none }` do navegador.
        """
        _, _, css = self.arquivos()
        self.assertIn("[hidden] { display: none !important; }", css)

    def test_a_reticula_tem_contorno(self):
        """Sobre o nucleo saturado do beacon so o contorno preto da contraste."""
        js, _, _ = self.arquivos()
        self.assertIn("function contornado(", js)
        self.assertNotIn(
            'strokeStyle = "rgba(250,243,230,.45)"', js,
            "sobrou traco sem contorno no visor",
        )


class DeslocamentoDaJanelaLongaTests(unittest.TestCase):
    """Depois de corrigir, a janela longa e deslocada em vez de descartada.

    Regressao do A/B de 2026-09-09 20:39. Descartar a janela apos cada pulso
    obrigava 60 s de aquecimento; com correcoes a cada ~80 s o controlador ficou
    77% do tempo em FORMANDO REFERENCIA LENTA, sem poder agir, e o erro mediano
    subiu de 1,100 para 1,567 px.
    """

    def estimador(self, window_s=120.0, warmup_s=60.0):
        from modulos.controle.tracker_controle import SlowBiasEstimator

        return SlowBiasEstimator(window_s=window_s, warmup_s=warmup_s)

    def encher(self, est, dx, dy, ate=70.0, passo=0.5):
        t = 0.0
        while t <= ate:
            e = est.observe(t, dx, dy)
            t += passo
        return e

    def test_deslocar_move_a_mediana_e_preserva_a_janela(self):
        est = self.estimador()
        antes = self.encher(est, 2.0, -1.0)
        self.assertTrue(antes.ready)
        est.deslocar(-1.5, 0.5)
        depois = est.observe(70.5, 0.5, -0.5)
        self.assertAlmostEqual(depois.dx_px, 0.5, delta=0.05)
        self.assertAlmostEqual(depois.dy_px, -0.5, delta=0.05)
        self.assertTrue(
            depois.ready,
            "deslocar nao pode custar aquecimento: era esse o defeito medido",
        )

    def test_descartar_custaria_o_aquecimento(self):
        """Contraprova: e o reset que cega o controlador."""
        est = self.estimador()
        self.encher(est, 2.0, -1.0)
        est.reset()
        depois = est.observe(70.5, 0.5, -0.5)
        self.assertFalse(depois.ready)

    def test_deslocar_ignora_valores_invalidos(self):
        est = self.estimador()
        antes = self.encher(est, 1.0, 1.0)
        est.deslocar(float("nan"), 0.0)
        depois = est.observe(70.5, 1.0, 1.0)
        self.assertAlmostEqual(depois.dx_px, antes.dx_px, delta=0.05)

    def test_o_sinal_da_conversao_cancela_o_erro(self):
        """m = A_inv @ (-e) e e = A @ m tem de fechar o ciclo.

        Um sinal trocado aqui faria a janela andar para o lado errado e o
        controlador insistir na direcao do erro, sem nada obvio no terminal.
        """
        import numpy as np

        from modulos.controle.tracker_controle import pixel_error_to_mount_error

        A_inv = np.array([[3.0e-4, 1.0e-5], [-8.0e-6, 2.7e-4]])
        A_direta = np.linalg.inv(A_inv)
        erro_px = (1.7, -0.9)
        movimento = pixel_error_to_mount_error(erro_px[0], erro_px[1], A_inv)
        variacao = A_direta @ np.array(movimento, dtype=float)
        # Aplicar esse movimento deve anular o erro: erro + variacao = 0.
        self.assertAlmostEqual(erro_px[0] + variacao[0], 0.0, places=9)
        self.assertAlmostEqual(erro_px[1] + variacao[1], 0.0, places=9)

    def test_a_janela_deslocada_libera_a_porta(self):
        """Corrigindo 90% do desvio, a mediana cai abaixo do limiar de soltura."""
        from modulos.configuracoes import tracker as T

        est = self.estimador()
        desvio = T.CONTROL_SLOW_TRIGGER_PX
        self.encher(est, desvio, 0.0)
        est.deslocar(-desvio * T.CONTROL_SLOW_FRACTION, 0.0)
        depois = est.observe(70.5, desvio * (1 - T.CONTROL_SLOW_FRACTION), 0.0)
        self.assertLess(
            depois.radius_px, T.CONTROL_SLOW_RELEASE_PX,
            "apos a correcao a porta precisa soltar, senao vira catraca",
        )


class ChavesDeExperimentoTests(unittest.TestCase):
    """As chaves de experimento saem do ambiente, com o arquivo de padrao.

    Editar o arquivo versionado na maquina da bancada para ligar um teste faz
    todo `git pull` conflitar -- aconteceu com camera_ids.py e de novo com
    tracker.py, no meio de uma noite de medicao.
    """

    def recarregar(self, **ambiente):
        import importlib
        import os

        from modulos.configuracoes import tracker

        antigos = {k: os.environ.get(k) for k in ambiente}
        os.environ.update({k: v for k, v in ambiente.items() if v is not None})
        for k, v in ambiente.items():
            if v is None:
                os.environ.pop(k, None)

        def restaurar():
            for k, v in antigos.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            importlib.reload(tracker)

        self.addCleanup(restaurar)
        return importlib.reload(tracker)

    def test_padrao_e_desligado(self):
        t = self.recarregar(QKD_AB_CONTROLE=None, QKD_AB_ZONA_REPOUSO=None)
        self.assertFalse(t.CONTROL_AB_TEST_ENABLED)
        self.assertFalse(t.HOLD_RADIUS_AB_TEST_ENABLED)

    def test_o_ambiente_liga_sem_tocar_no_arquivo(self):
        t = self.recarregar(QKD_AB_CONTROLE="1")
        self.assertTrue(t.CONTROL_AB_TEST_ENABLED)

    def test_aceita_as_formas_usuais_e_recusa_o_resto(self):
        for ligado in ("1", "true", "sim", "on", "YES"):
            self.assertTrue(
                self.recarregar(QKD_AB_CONTROLE=ligado).CONTROL_AB_TEST_ENABLED,
                f"{ligado!r} deveria ligar",
            )
        for desligado in ("0", "false", "nao", "", "talvez"):
            self.assertFalse(
                self.recarregar(QKD_AB_CONTROLE=desligado).CONTROL_AB_TEST_ENABLED,
                f"{desligado!r} nao deveria ligar",
            )

    def test_as_duas_chaves_sao_independentes(self):
        t = self.recarregar(QKD_AB_CONTROLE="1", QKD_AB_ZONA_REPOUSO="0")
        self.assertTrue(t.CONTROL_AB_TEST_ENABLED)
        self.assertFalse(t.HOLD_RADIUS_AB_TEST_ENABLED)


class ReproducaoDoPassadoTests(unittest.TestCase):
    """Clicar no grafico roda o passado dali para frente, nao congela um quadro.

    Congelar num instante mostra o resultado; ver a sequencia mostra o processo,
    que e o que responde "o que causou aquilo?". O servidor precisa devolver
    quadros DISTINTOS e em ordem para uma varredura de instantes.
    """

    def painel_com_historia(self, quantidade=10, passo=0.3):
        import time

        import numpy as np

        from modulos.controle.tracker_dashboard import TrackerDashboard

        painel = TrackerDashboard(frame_hz=4.0, open_browser=False)
        self.addCleanup(painel.close)
        marcas = []
        for i in range(quantidade):
            valor = 20 + i * 20
            painel.update(np.full((64, 64), valor, dtype=np.uint8), {"passo": i})
            marcas.append((time.time(), valor))
            time.sleep(passo)
        return painel, marcas

    def ler(self, painel, unix_s):
        import urllib.request

        import cv2
        import numpy as np

        resposta = urllib.request.urlopen(
            f"{painel.url}api/historico.jpg?t={unix_s:.3f}"
        )
        dados = resposta.read()
        img = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_GRAYSCALE)
        return float(resposta.headers["X-Frame-Unix-S"]), int(np.median(img))

    def test_uma_varredura_devolve_quadros_em_ordem(self):
        painel, marcas = self.painel_com_historia()
        inicio, fim = marcas[0][0], marcas[-1][0]
        vistos = []
        passos = 24
        for k in range(passos):
            t = inicio + (fim - inicio) * k / (passos - 1)
            vistos.append(self.ler(painel, t))
        instantes = [v[0] for v in vistos]
        self.assertEqual(
            instantes, sorted(instantes),
            "a reproducao andaria para tras: os quadros nao vem em ordem",
        )
        valores = [v[1] for v in vistos]
        self.assertEqual(
            valores, sorted(valores),
            "os quadros nao acompanham a linha do tempo",
        )
        distintos = len(set(valores))
        self.assertGreaterEqual(
            distintos, len(marcas) - 2,
            f"a varredura viu so {distintos} quadros distintos de {len(marcas)}: "
            "a reproducao ficaria travada no mesmo frame",
        )

    def test_pedir_o_presente_entrega_o_quadro_mais_novo(self):
        """E o que encerra a reproducao e devolve o painel ao vivo."""
        import time

        painel, marcas = self.painel_com_historia(quantidade=6)
        _, valor = self.ler(painel, time.time())
        self.assertEqual(valor, marcas[-1][1])

    def test_o_botao_de_pausa_existe_no_painel(self):
        from pathlib import Path

        from modulos.controle import tracker_dashboard

        html = (Path(tracker_dashboard.ASSET_DIR) / "index.html").read_text(
            encoding="utf-8"
        )
        js = (Path(tracker_dashboard.ASSET_DIR) / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="review-toggle"', html)
        self.assertIn("avancarReproducao", js)


class ResumoParcialTests(unittest.TestCase):
    """Uma morte subita nao pode levar junto os metadados da sessao.

    O resumo so era escrito no encerramento. Isso cobre Ctrl+C e excecoes, que
    passam pelo `finally`, mas nao um desligamento do Windows nem uma queda de
    energia: em 2026-09-10 as 03:44 a telemetria inteira sobreviveu e o resumo
    se perdeu, levando o motivo do fim, a faixa de exposicao e o retorno.
    """

    def logger(self):
        import tempfile
        from pathlib import Path

        from modulos.controle.tracker_telemetria import TrackerCsvLogger

        destino = Path(tempfile.mkdtemp())
        registrador = TrackerCsvLogger(
            destino, session_started=0.0,
            initial_az=10.0, initial_alt=20.0, max_hours=12.0,
        )
        return registrador, registrador.session_dir

    def test_o_resumo_parcial_e_gravado_e_marcado(self):
        import json

        registrador, destino = self.logger()
        registrador.resumo_parcial()
        caminho = destino / "resumo.json"
        self.assertTrue(caminho.exists(), "o resumo parcial nao foi para o disco")
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertTrue(dados["parcial"], "precisa vir marcado como parcial")
        self.assertEqual(dados["finish_reason"], "em_andamento")

    def test_close_sobrescreve_com_o_definitivo(self):
        import json

        registrador, destino = self.logger()
        registrador.resumo_parcial()
        registrador.close(reason="tempo_maximo_da_sessao", return_result={"ok": True})
        dados = json.loads((destino / "resumo.json").read_text(encoding="utf-8"))
        self.assertFalse(dados["parcial"])
        self.assertEqual(dados["finish_reason"], "tempo_maximo_da_sessao")
        self.assertEqual(dados["return_to_start"], {"ok": True})

    def test_a_gravacao_e_atomica(self):
        """JSON truncado e pior que nenhum: some o antigo e o novo nao presta."""
        import json

        registrador, destino = self.logger()
        registrador.resumo_parcial()
        for _ in range(5):
            registrador.resumo_parcial()
            json.loads((destino / "resumo.json").read_text(encoding="utf-8"))
        self.assertFalse(
            (destino / "resumo.json.tmp").exists(),
            "o temporario tem de ser renomeado, nao deixado para tras",
        )

    def test_o_periodo_cabe_numa_sessao_longa(self):
        from modulos.configuracoes import tracker

        self.assertGreater(tracker.SUMMARY_PARTIAL_SECONDS, tracker.CSV_FLUSH_SECONDS)
        self.assertLessEqual(
            tracker.SUMMARY_PARTIAL_SECONDS, 600.0,
            "periodo longo demais devolve pouco numa morte subita",
        )


class RegimePadraoTests(unittest.TestCase):
    """O regime padrao passou a ser lento_ganho_baixo.

    Medido no A/B de tres bracos de 2026-09-10 (7 blocos por braco, 3h21): erro
    mediano 0,939 contra 1,073 px, DC mediano por bloco 0,347 contra 0,647, e
    ainda MENOS correcoes (37,2/h contra 46,3). A vantagem cresce com a
    turbulencia: empate com ceu calmo, 23% melhor com turbulencia forte.
    """

    def cfg(self):
        from modulos.configuracoes import tracker

        return tracker

    def test_o_padrao_e_o_braco_vencedor(self):
        self.assertEqual(self.cfg().CONTROL_REGIME_PADRAO, "lento_ganho_baixo")

    def test_o_nome_do_padrao_e_valido(self):
        """Um nome errado cairia silenciosamente no regime antigo."""
        self.assertIn(
            self.cfg().CONTROL_REGIME_PADRAO,
            ("atual", "lento_ganho_alto", "lento_ganho_baixo"),
        )

    def test_os_freios_nao_encolhem_com_a_zona(self):
        """A porta ficou muito mais apertada; freio e autoteste NAO podem seguir.

        Os freios so podem agir acima de 2x TOLERANCIA_PX, que sai de
        HOLD_ENTER_RADIUS_PX. Se acompanhassem o novo limiar de soltura
        (0,25 px), agiriam a partir de 0,5 px -- abaixo do erro tipico de
        turbulencia -- e o tracker viveria freando.

        O outro lado tambem foi verificado, na telemetria de 2026-09-10: no
        regime novo o raio de controle ainda passa de 2,0 px em 1,2% do tempo,
        com maximo de 6,2 px, entao o freio NAO virou codigo morto. Ele age
        menos, que e o desejado, sem deixar de existir.
        """
        from modulos.controle.tracker_loop import TOLERANCIA_PX

        t = self.cfg()
        self.assertEqual(TOLERANCIA_PX, t.HOLD_ENTER_RADIUS_PX)
        self.assertGreaterEqual(
            2.0 * TOLERANCIA_PX, 3.0 * t.CONTROL_SLOW_TRIGGER_PX,
            "o limiar do freio ficou perto demais do limiar de correcao: ele "
            "dispararia durante correcoes normais",
        )

    def test_o_autoteste_mantem_o_criterio_antigo(self):
        """"Voltou ao alvo?" nao e a mesma pergunta que "vale corrigir?"."""
        import inspect

        from modulos.controle import tracker_autoteste

        fonte = inspect.getsource(tracker_autoteste)
        self.assertIn("HOLD_ENTER_RADIUS_PX", fonte)
        self.assertNotIn("CONTROL_SLOW_RELEASE_PX", fonte)

    def test_o_ab_continua_podendo_comparar_com_o_antigo(self):
        """Trocar o padrao nao pode tornar o regime antigo inalcancavel."""
        import inspect

        from modulos.controle import tracker_loop

        fonte = inspect.getsource(tracker_loop.executar_loop_controle)
        self.assertIn('"atual", "lento_ganho_alto", "lento_ganho_baixo"', fonte)
