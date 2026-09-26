"""O observador sem correcao, rodando inteiro com camera e detector simulados.

Existe porque tres defeitos dele so apareceram na UFF, de madrugada, um de cada
vez: backend de camera errado, assinatura da ROI desatualizada, e o tamanho da
ROI lido como o canto dela (com a soma empilhada deixando de somar em silencio).
"""

import argparse
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from programas_principais import observar_sem_corrigir as obs  # noqa: E402
from modulos.controle import mount_em_uso, tracker_aquisicao, tracker_camera  # noqa: E402
from modulos.visao import detector_ilhas as foco  # noqa: E402

LARGURA, ALTURA = 252, 256        # a ROI que a IDS aplicou naquela noite


def quadro_com_ponto() -> np.ndarray:
    y, x = np.mgrid[0:ALTURA, 0:LARGURA]
    q = 8 + 150 * np.exp(-((x - 127) ** 2 + (y - 128) ** 2) / (2 * 6.0 ** 2))
    return q.astype(np.uint8)


class ObservadorTests(unittest.TestCase):
    def test_roda_grava_e_empilha_com_a_roi_de_verdade(self):
        quadro = quadro_com_ponto()
        rng = np.random.default_rng(0)
        with tempfile.TemporaryDirectory() as pasta, \
                patch.object(obs, "SAIDA_RAIZ", Path(pasta)), \
                patch.object(mount_em_uso, "motivo_de_uso", lambda: None), \
                patch.object(tracker_camera, "connect_camera", lambda: None), \
                patch.object(tracker_camera, "disconnect_camera", lambda: None), \
                patch.object(tracker_camera, "escolher_referencia_tracker",
                             lambda: SimpleNamespace(x_px=1223.7, y_px=891.5, focus_signature={})), \
                patch.object(tracker_camera, "set_camera_roi_validated",
                             lambda *a: (1096, 764, 127.7, 127.5)), \
                patch.object(tracker_camera, "current_roi_size", lambda padrao: (LARGURA, ALTURA)), \
                patch.object(tracker_camera, "capture_frame", lambda exp: quadro.astype(float)), \
                patch.object(tracker_camera, "latest_raw_frame", lambda: quadro), \
                patch.object(tracker_aquisicao, "medir_laser",
                             lambda f: (127.0 + rng.normal(0, 0.3), 128.0 + rng.normal(0, 0.3))), \
                patch.object(foco, "set_focus_mode", lambda modo: modo), \
                patch.object(foco, "reset_focus_lock", lambda: None):
            args = argparse.Namespace(camera="ids", horas=0.0008, intervalo_rajada=0.005,
                                      intervalo_imagem=0.01, exposicao_us=None, ganho=None)
            self.assertEqual(obs.main(args), 0)

            sessao = next(Path(pasta).iterdir())
            with (sessao / "quadros.csv").open(encoding="utf-8") as f:
                linhas = list(csv.DictReader(f))
            self.assertGreater(len(linhas), 10)
            self.assertAlmostEqual(float(linhas[-1]["x_px"]), 127.0, delta=2.0)
            # A soma empilhada tem o tamanho da ROI e de fato acumulou luz.
            soma = np.load(sessao / "empilhada_final.npy")
            self.assertEqual(soma.shape, (ALTURA, LARGURA))
            self.assertGreater(float(soma.max()), 0.0)


if __name__ == "__main__":
    unittest.main()
