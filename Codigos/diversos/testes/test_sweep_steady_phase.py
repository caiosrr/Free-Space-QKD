"""Fase de velocidade constante da varredura e as duas reguas de escala.

Reproduz o defeito medido na sessao 2026-09-05: o mount leva cerca de 1 s para
vencer o atrito estatico, e nesse trecho a velocidade optica chega a variar 20x.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.calibracao import calibracao_continua_core as core


def varredura(
    *,
    duracao=12.0,
    fps=50.0,
    escala_px_deg=7100.0,
    taxa_deg_s=None,
    transiente_s=1.5,
    ruido_px=0.0,
    quantizacao_arcsec=1.0,
    semente=3,
):
    """Gera uma varredura sintetica com partida lenta e telemetria em degraus."""
    taxa = core.SWEEP_RATE_DEG_S if taxa_deg_s is None else taxa_deg_s
    rng = np.random.default_rng(semente)
    tempos = np.arange(0.0, duracao, 1.0 / fps)
    # Velocidade sobe de 0 ate a nominal ao longo do transiente e fica constante.
    fracao = np.clip(tempos / max(transiente_s, 1e-9), 0.0, 1.0)
    angulo = np.cumsum(taxa * fracao) / fps
    quantum = quantizacao_arcsec / 3600.0
    relatado = np.floor(angulo / quantum) * quantum if quantum > 0 else angulo
    amostras = []
    for t, ang_real, ang_rel in zip(tempos, angulo, relatado):
        amostras.append(
            core.SweepSample(
                run="fit_az_pos", axis=0, command_sign=1, elapsed_s=float(t),
                az_deg=float(ang_rel), alt_deg=0.0,
                delta_az_deg=float(ang_rel), delta_alt_deg=0.0,
                x_px=float(100.0 + escala_px_deg * ang_real + rng.normal(0, ruido_px)),
                y_px=float(200.0 + rng.normal(0, ruido_px)),
            )
        )
    return amostras


class SteadyPhaseTests(unittest.TestCase):
    def test_transiente_de_partida_e_descartado(self):
        amostras = varredura(transiente_s=1.5)
        inicio, fim, diag = core._janela_fase_estavel(amostras)
        self.assertGreaterEqual(inicio, 1.0)
        self.assertLess(inicio, 3.0)
        self.assertAlmostEqual(fim, amostras[-1].elapsed_s - core.SWEEP_DECEL_DISCARD_S, places=6)
        self.assertGreater(diag["steady_speed_px_s"], 0.0)
        self.assertIn("transient_seconds", diag)

    def test_velocidade_da_fase_estavel_bate_com_a_escala_real(self):
        amostras = varredura(escala_px_deg=7100.0)
        inicio, fim, _ = core._janela_fase_estavel(amostras)
        estaveis = [s for s in amostras if inicio <= s.elapsed_s <= fim]
        velocidade = core._velocidade_optica(estaveis)
        esperada = 7100.0 * core.SWEEP_RATE_DEG_S
        self.assertAlmostEqual(velocidade / esperada, 1.0, delta=0.05)

    def test_descartar_o_transiente_corrige_a_escala(self):
        """Sem descartar a partida, a escala medida sai baixa demais."""
        amostras = varredura(escala_px_deg=7100.0, duracao=6.0, transiente_s=1.5)
        inteira = core._duas_reguas(amostras, 0)["scale_from_time_px_deg"]
        inicio, fim, _ = core._janela_fase_estavel(amostras)
        estaveis = [s for s in amostras if inicio <= s.elapsed_s <= fim]
        so_estavel = core._duas_reguas(estaveis, 0)["scale_from_time_px_deg"]
        self.assertLess(inteira, 0.90 * 7100.0)
        self.assertAlmostEqual(so_estavel / 7100.0, 1.0, delta=0.05)

    def test_varredura_curta_demais_nao_tem_fase_estavel_utilizavel(self):
        amostras = varredura(duracao=2.0, transiente_s=1.5)
        inicio, fim, _ = core._janela_fase_estavel(amostras)
        self.assertLess(fim - inicio, core.SWEEP_MIN_STEADY_SECONDS)


class TwoRulersTests(unittest.TestCase):
    def test_as_duas_reguas_concordam_numa_varredura_longa(self):
        amostras = varredura(duracao=12.0, escala_px_deg=7100.0)
        inicio, fim, _ = core._janela_fase_estavel(amostras)
        regua = core._duas_reguas([s for s in amostras if inicio <= s.elapsed_s <= fim], 0)
        self.assertTrue(regua["comparable"])
        self.assertAlmostEqual(regua["encoder_over_time_ratio"], 1.0, delta=0.05)
        self.assertAlmostEqual(regua["scale_from_encoder_px_deg"] / 7100.0, 1.0, delta=0.06)

    def test_quantizacao_domina_uma_varredura_curta_e_some_numa_longa(self):
        """1 arcsec sobre 7 arcsec e erro grande; sobre 250 arcsec e desprezivel."""
        def erro_relativo(duracao):
            amostras = varredura(duracao=duracao, transiente_s=0.0, escala_px_deg=7100.0)
            regua = core._duas_reguas(amostras, 0)
            return abs(regua["scale_from_encoder_px_deg"] / 7100.0 - 1.0)

        curta = erro_relativo(2.0)
        longa = erro_relativo(30.0)
        self.assertGreater(curta, 3.0 * longa)
        self.assertLess(longa, 0.03)

    def test_poucas_amostras_nao_sao_comparaveis(self):
        self.assertFalse(core._duas_reguas(varredura(duracao=0.05), 0)["comparable"])


if __name__ == "__main__":
    unittest.main()
