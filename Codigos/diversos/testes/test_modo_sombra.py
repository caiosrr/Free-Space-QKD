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
