"""A sessao sem correcao (tracker.py --sem-correcao) nunca move o mount.

Roda a sessao inteira do tracker, com o laco de controle e o watchdog de
verdade, contra um mount simulado no nivel do ASCOM. A camera e a aquisicao sao
trocadas por uma fonte de medidas com erro grande, para o controle ter motivo
de corrigir; no meio da sessao o mount simulado "deriva" alem do limite, para o
watchdog pedir parada e o fim da sessao ter motivo de voltar a posicao inicial.

A mesma sessao com correcao serve de controle do proprio teste: ela PRECISA
chamar o laco de movimento e o retorno. Se nao chamasse, a ausencia de chamadas
na sessao sem correcao nao provaria nada.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import modulos.controle.mount_ascom as mount_ascom  # noqa: E402
import modulos.controle.tracker_autoteste as tracker_autoteste  # noqa: E402
import modulos.controle.tracker_loop as tracker_loop  # noqa: E402
import modulos.controle.tracker_seguranca as tracker_seguranca  # noqa: E402
import modulos.controle.tracker_sessao as tracker_sessao  # noqa: E402
import modulos.controle.tracker_telemetria as tracker_telemetria  # noqa: E402
from modulos.configuracoes.tracker import MAX_OFFSET_AZ_DEG  # noqa: E402

DURACAO_S = 2.5
DERIVA_EM_S = 1.5


class MountSimulado:
    """Responde como o servidor ASCOM e anota tudo o que recebe."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.puts = []
        self.lock = threading.Lock()

    def call(self, method, command, timeout=5.0, **extra):
        if method == "PUT":
            with self.lock:
                self.puts.append((command, dict(extra.get("data") or {})))
            return None
        if command == "azimuth":
            derivou = time.monotonic() - self.t0 > DERIVA_EM_S
            return 100.0 + (MAX_OFFSET_AZ_DEG + 1.0 if derivou else 0.0)
        if command == "altitude":
            return 1.0
        if command in {"atpark", "tracking"}:
            return False
        return True

    def comandos_de_movimento(self):
        """Todo PUT que nao seja parada, conexao ou desligar o rastreio."""
        inofensivos = []
        for comando, dados in self.puts:
            if comando == "moveaxis" and float(dados.get("Rate", 1.0)) == 0.0:
                continue
            if comando == "connected" or (comando == "tracking" and not dados.get("Tracking")):
                continue
            inofensivos.append((comando, dados))
        return inofensivos


def aquisicao_falsa(state, logger, display, **_):
    """Medidas com 40 px de erro a 50 Hz, ate o watchdog ou o tempo acabar."""
    fim = time.monotonic() + DURACAO_S
    seq = 0
    while time.monotonic() < fim:
        with state.lock:
            if state.stop:
                break
            seq += 1
            state.has_signal = True
            state.measurement_seq = seq
            state.measurement_ts = time.perf_counter()
            state.dx_filt_px = 40.0
            state.dy_filt_px = -25.0
        time.sleep(0.02)
    return SimpleNamespace(motivo="fim_do_teste", ultimo_frame=None)


class SessaoSemCorrecaoTests(unittest.TestCase):
    def _rodar_sessao(self, sem_correcao, executar_autoteste=None):
        mount = MountSimulado()
        espioes = {
            "move_axis do laco": mock.Mock(),
            "move_axes_pid_2d": mock.Mock(),
            "retorno": mock.Mock(return_value={"success": False}),
            "autoteste": mock.Mock(),
            "recuperacao do autoteste": mock.Mock(),
        }
        saida = Path(tempfile.mkdtemp())
        optica = mock.Mock()
        optica.escala_rad_por_px_medida.return_value = None
        optica.avisos.return_value = []
        alvo = SimpleNamespace(focus_signature={"primary": None}, x_px=50.0, y_px=50.0)
        trocas = [
            mock.patch.object(mount_ascom, "call", side_effect=mount.call),
            mock.patch.object(tracker_loop, "SESSION_WITHOUT_CORRECTION", sem_correcao),
            mock.patch.object(tracker_sessao, "SESSION_WITHOUT_CORRECTION", sem_correcao),
            mock.patch.object(tracker_telemetria, "SESSION_WITHOUT_CORRECTION", sem_correcao),
            mock.patch.object(tracker_loop, "move_axis", espioes["move_axis do laco"]),
            mock.patch.object(tracker_seguranca, "move_axes_pid_2d", espioes["move_axes_pid_2d"]),
            mock.patch.object(tracker_autoteste, "move_axes_pid_2d", espioes["move_axes_pid_2d"]),
            mock.patch.object(tracker_sessao, "retornar_posicao_inicial", espioes["retorno"]),
            mock.patch.object(tracker_sessao, "aplicar_deslocamento_autoteste", espioes["autoteste"]),
            mock.patch.object(tracker_sessao, "monitorar_recuperacao",
                              espioes["recuperacao do autoteste"]),
            mock.patch.object(tracker_sessao, "executar_aquisicao", aquisicao_falsa),
            mock.patch.object(tracker_sessao, "TRACKER_OUTPUT_DIR", saida),
            mock.patch.object(tracker_sessao, "carregar_matriz_calibracao",
                              return_value=(np.array([[0.001, 0.0], [0.0, 0.001]]), "teste")),
            mock.patch.object(tracker_sessao, "optica", optica),
            mock.patch.object(tracker_sessao, "escolher_referencia_tracker", return_value=alvo),
            mock.patch.object(tracker_sessao, "set_camera_roi_validated",
                              return_value=(0, 0, 32.0, 32.0)),
            mock.patch.object(tracker_sessao, "current_roi_size", return_value=(64, 64)),
            mock.patch.object(tracker_sessao, "TrackerDisplay"),
            mock.patch.object(tracker_sessao, "connect_camera"),
            mock.patch.object(tracker_sessao, "disconnect_camera"),
            mock.patch.object(tracker_sessao, "reset_camera_roi"),
            mock.patch.object(tracker_sessao.diagnostico, "mascara_gravada", return_value=None),
            mock.patch.object(tracker_sessao.desligamento_windows, "registrar", return_value=False),
            mock.patch("builtins.print"),
        ]
        estados = []
        original = tracker_sessao.TrackerState

        def guardar_estado(*a, **k):
            estados.append(original(*a, **k))
            return estados[-1]

        trocas.append(mock.patch.object(tracker_sessao, "TrackerState", side_effect=guardar_estado))
        for troca in trocas:
            troca.start()
        try:
            tracker_sessao.main(session_hours=1.0, executar_autoteste=executar_autoteste)
        finally:
            for troca in reversed(trocas):
                troca.stop()
        resumo = json.loads(next(saida.glob("sessoes/*/resumo.json")).read_text(encoding="utf-8"))
        return mount, espioes, resumo, estados[0].snapshot()

    def test_a_sessao_sem_correcao_nao_chama_nenhum_movimento(self):
        mount, espioes, resumo, estado = self._rodar_sessao(sem_correcao=True)
        for nome, espiao in espioes.items():
            self.assertEqual(espiao.call_count, 0, f"{nome} foi chamado: {espiao.call_args_list}")
        self.assertEqual(mount.comandos_de_movimento(), [])
        self.assertTrue(resumo["sem_correcao"])
        self.assertFalse(estado["correction_enabled"])
        # O watchdog seguiu de pe: pediu parada pela deriva simulada.
        self.assertTrue(str(estado["safety_stop_reason"]).startswith("limite_absoluto_az"))

    def test_o_mesmo_cenario_com_correcao_move_o_mount(self):
        # Controle do teste: sem isto, nenhuma chamada acima nao provaria nada.
        mount, espioes, resumo, estado = self._rodar_sessao(sem_correcao=False,
                                                            executar_autoteste=False)
        self.assertGreater(espioes["move_axis do laco"].call_count, 0)
        self.assertEqual(espioes["retorno"].call_count, 1)
        self.assertFalse(resumo["sem_correcao"])
        self.assertTrue(estado["correction_enabled"])

    def test_autoteste_e_recusado(self):
        with self.assertRaises(ValueError), \
                mock.patch.object(tracker_sessao, "SESSION_WITHOUT_CORRECTION", True):
            tracker_sessao.main(session_hours=1.0, executar_autoteste=True)


class OpcoesDoTrackerTests(unittest.TestCase):
    def _parse(self, *argv):
        from programas_principais import tracker

        with mock.patch.object(sys, "argv", ["tracker.py", *argv]), mock.patch("sys.stderr"):
            return tracker.parse_args()

    def test_combinacoes_que_moveriam_o_mount_sao_recusadas(self):
        for extra in (["--autoteste"], ["--blocos-minutos", "15"], ["--ab-janela-curta", "10"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self._parse("--sem-correcao", *extra)

    def test_sem_correcao_aceita_o_resto(self):
        args = self._parse("--sem-correcao", "--sem-autoteste", "--horas", "10",
                           "--exposicao-us", "9000", "--ganho", "8")
        self.assertTrue(args.sem_correcao)
        self.assertEqual((args.exposicao_us, args.ganho), (9000.0, 8.0))


if __name__ == "__main__":
    unittest.main()
