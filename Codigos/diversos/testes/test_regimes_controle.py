"""Bracos do A/B de controle, em especial o de janela curta com limiar em arcsec."""

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.controle.tracker_controle import px_por_arcsec, regime_de_controle  # noqa: E402

# Matriz da calibracao de 2026-09-29 22:58 (px por grau de mount).
A_22H58 = np.array([[-1189.4356, -212.8711], [216.5046, -1267.2980]])

PARAMETROS = dict(
    atual={"entrar_px": 1.0, "sair_px": 2.0, "fracao": 0.35},
    lento={"entrar_px": 0.25, "sair_px": 0.6, "fracao_alta": 0.9, "fracao_baixa": 0.35},
    curta={"soltar_arcsec": 0.42, "disparar_arcsec": 1.0, "fracao": 0.9},
)


class EscalaTests(unittest.TestCase):
    def test_escala_da_calibracao_nova(self):
        # 2,98"/px em az e 2,80"/px em alt; a media das escalas em px/grau
        # da 2,89"/px.
        self.assertAlmostEqual(1.0 / px_por_arcsec(A_22H58), 2.89, delta=0.01)

    def test_matriz_degenerada_e_recusada(self):
        with self.assertRaises(ValueError):
            px_por_arcsec(np.zeros((2, 2)))


class RegimeTests(unittest.TestCase):
    def test_janela_curta_converte_arcsec_pela_escala(self):
        escala = px_por_arcsec(A_22H58)
        r = regime_de_controle("lento_janela_curta", px_arcsec=escala, **PARAMETROS)
        self.assertEqual(r.janela, "janela_curta")
        self.assertAlmostEqual(r.sair_px, 1.0 * escala)
        self.assertAlmostEqual(r.entrar_px, 0.42 * escala)
        self.assertAlmostEqual(r.fracao, 0.9)
        # Na optica nova, 1" e cerca de um terco de pixel.
        self.assertAlmostEqual(r.sair_px, 0.346, delta=0.005)

    def test_na_escala_antiga_o_mesmo_limiar_vira_mais_pixels(self):
        antiga = 1 / 0.386
        r = regime_de_controle("lento_janela_curta", px_arcsec=antiga, **PARAMETROS)
        self.assertAlmostEqual(r.sair_px, 2.59, delta=0.01)

    def test_bracos_antigos_seguem_em_pixels(self):
        baixo = regime_de_controle("lento_ganho_baixo", px_arcsec=0.3, **PARAMETROS)
        self.assertEqual((baixo.sair_px, baixo.entrar_px, baixo.fracao, baixo.janela),
                         (0.6, 0.25, 0.35, "longa"))
        alto = regime_de_controle("lento_ganho_alto", px_arcsec=0.3, **PARAMETROS)
        self.assertEqual(alto.fracao, 0.9)
        atual = regime_de_controle("atual", px_arcsec=0.3, **PARAMETROS)
        self.assertEqual(atual.janela, "curta_8s")

    def test_nome_desconhecido_e_recusado(self):
        with self.assertRaises(ValueError):
            regime_de_controle("lento_qualquer", px_arcsec=0.3, **PARAMETROS)


class ConfiguracaoDoABTests(unittest.TestCase):
    def tearDown(self):
        import modulos.configuracoes.tracker as cfg
        importlib.reload(cfg)

    def _recarregar(self, **env):
        import modulos.configuracoes.tracker as cfg
        with patch.dict(os.environ, env):
            return importlib.reload(cfg)

    def test_padrao_repete_o_ab_de_tres_bracos(self):
        cfg = self._recarregar()
        self.assertEqual(cfg.CONTROL_AB_REGIMES,
                         ("atual", "lento_ganho_alto", "lento_ganho_baixo"))

    def test_ab_da_janela_curta_como_o_tracker_py_configura(self):
        cfg = self._recarregar(QKD_AB_REGIMES="lento_ganho_baixo,lento_janela_curta",
                               QKD_AB_BLOCO_S="600")
        self.assertEqual(cfg.CONTROL_AB_REGIMES, ("lento_ganho_baixo", "lento_janela_curta"))
        self.assertEqual(cfg.CONTROL_AB_BLOCK_SECONDS, 600.0)

    def test_braco_com_nome_errado_nao_passa_da_importacao(self):
        with self.assertRaises(ValueError):
            self._recarregar(QKD_AB_REGIMES="lento_ganho_baixo,lento_janela_curtaa")

    def test_bloco_curto_demais_nao_passa_da_importacao(self):
        with self.assertRaises(ValueError):
            self._recarregar(QKD_AB_BLOCO_S="120")


class LacoComMountSimuladoTests(unittest.TestCase):
    """O laco de controle real, com o mount simulado e a escala da optica nova.

    Planta simplificada: deriva constante na elevacao, sem turbulencia. Verifica
    a integracao do braco novo, nao substitui o teste no mount.
    """

    def rodar(self, regime, deriva_arcsec_min=1.5, duracao_s=600.0):
        from modulos.controle import tracker_loop
        from modulos.controle.tracker_estado import TrackerState

        state = TrackerState(has_signal=True, dx_filt_px=0.0, dy_filt_px=0.0)
        clock = [1.0]
        position = np.zeros(2)
        velocity = np.zeros(2)
        deriva_px_s = A_22H58 @ np.array([0.0, deriva_arcsec_min / 3600.0 / 60.0])
        erros = []

        def move(axis, rate, unused):
            velocity[axis] = rate

        def sleep(dt):
            clock[0] += dt
            position[:] += (A_22H58 @ velocity + deriva_px_s) * dt
            with state.lock:
                state.measurement_seq += 1
                state.measurement_ts = clock[0]
                state.dx_filt_px, state.dy_filt_px = position
                state.stop = clock[0] > duracao_s
            if clock[0] > 200:
                erros.append(float(np.linalg.norm(position)))

        with patch.object(tracker_loop.time, "perf_counter", side_effect=lambda: clock[0]),                 patch.object(tracker_loop.time, "sleep", side_effect=sleep),                 patch.object(tracker_loop, "move_axis", side_effect=move),                 patch.object(tracker_loop, "stop_axes_safely"),                 patch.object(tracker_loop, "CONTROL_HZ", 10),                 patch.object(tracker_loop, "CONTROL_REGIME_PADRAO", regime),                 patch("builtins.print"):
            tracker_loop.executar_loop_controle(state, np.linalg.inv(A_22H58))
        escala = px_por_arcsec(A_22H58)
        return state, np.array(erros) / escala  # em arcsec

    def test_janela_curta_usa_a_porta_em_arcsec_e_segura_a_deriva(self):
        state, erro = self.rodar("lento_janela_curta")
        escala = px_por_arcsec(A_22H58)
        self.assertEqual(state.control_regime, "lento_janela_curta")
        self.assertAlmostEqual(state.hold_exit_radius_px, 1.0 * escala, places=6)
        self.assertAlmostEqual(state.hold_enter_radius_px, 0.42 * escala, places=6)
        self.assertGreater(state.correction_cycles, 3)
        # Deriva constante de 1,5"/min, o p95 da noite: a janela de 45 s atrasa
        # ~0,6" e a porta dispara em 1", entao o erro fica abaixo de ~2".
        self.assertLess(np.median(erro), 2.0)

    def test_com_a_mesma_deriva_o_regime_em_uso_fica_mais_longe(self):
        _, curta = self.rodar("lento_janela_curta")
        _, baixo = self.rodar("lento_ganho_baixo")
        self.assertLess(np.median(curta), np.median(baixo))


if __name__ == "__main__":
    unittest.main()
