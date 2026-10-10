"""A parada acha o mount pela identidade USB, e o aviso do Telegram sai sozinho."""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modulos.configuracoes import serial_mount  # noqa: E402
from modulos.controle import parada_emergencia as pe  # noqa: E402
from programas_principais import publicar_estado_pc as pub  # noqa: E402


def aparelho(porta, vid, pid, serie=None):
    return SimpleNamespace(device=porta, vid=vid, pid=pid, serial_number=serie)


MOUNT = aparelho("COM7", 0x03C3, 0x1234, "AM5X")
LASER = aparelho("COM4", 0x1234, 0x0001, "OBIS1")


class IdentidadeTests(unittest.TestCase):
    def test_acha_o_mount_qualquer_que_seja_o_numero(self):
        porta, _ = pe.porta_pela_identidade(0x03C3, 0x1234, "AM5X", [LASER, MOUNT])
        self.assertEqual(porta, "COM7")

    def test_sem_o_mount_nao_devolve_porta(self):
        porta, descricao = pe.porta_pela_identidade(0x03C3, 0x1234, None, [LASER])
        self.assertIsNone(porta)
        self.assertIn("nenhuma porta tocada", descricao)

    def test_dois_iguais_sem_serie_e_ambiguo(self):
        outro = aparelho("COM9", 0x03C3, 0x1234, "OUTRO")
        porta, descricao = pe.porta_pela_identidade(0x03C3, 0x1234, None, [MOUNT, outro])
        self.assertIsNone(porta)
        self.assertIn("USB_SERIE", descricao)
        porta, _ = pe.porta_pela_identidade(0x03C3, 0x1234, "OUTRO", [MOUNT, outro])
        self.assertEqual(porta, "COM9")

    def test_identidade_vence_a_porta_fixa(self):
        with mock.patch.multiple(serial_mount, USB_VID=0x03C3, USB_PID=0x1234,
                                 USB_SERIE=None, PORTA_SERIAL="COM4"), \
             mock.patch("serial.tools.list_ports.comports", return_value=[LASER, MOUNT]):
            porta, _ = pe.porta_do_mount()
        self.assertEqual(porta, "COM7")

    def test_mount_ausente_nao_abre_porta_nenhuma(self):
        with mock.patch.multiple(serial_mount, USB_VID=0x03C3, USB_PID=0x1234,
                                 USB_SERIE=None, PORTA_SERIAL="COM4"), \
             mock.patch("serial.tools.list_ports.comports", return_value=[LASER]), \
             mock.patch("serial.Serial", side_effect=AssertionError("abriu uma porta")):
            ok, descricao = pe.parar_pela_serial()
        self.assertFalse(ok)
        self.assertIn("nao esta ligado", descricao)


class AcompanharTests(unittest.TestCase):
    def rodar(self, rodadas, relogio=None):
        pasta = Path(tempfile.mkdtemp())
        args = argparse.Namespace(saida=pasta, intervalo=60.0, telegram_token="t",
                                  telegram_chat="c", incluir_ociosidade=False)
        avisos = []
        with mock.patch.object(pub, "publicar", side_effect=rodadas) as publicar, \
             mock.patch.object(pub, "avisar_telegram", side_effect=lambda *a: avisos.append(a[2])), \
             mock.patch.object(pub.time, "sleep"), \
             mock.patch.object(pub.time, "monotonic", side_effect=relogio or iter(range(0, 10**6, 60))):
            codigo = pub.acompanhar(args)
        return codigo, publicar.call_count, avisos, pasta

    def test_sai_depois_de_avisar_o_fim(self):
        codigo, n, _, pasta = self.rodar([("parado", "parado"), ("gravando", "gravando"),
                                          ("gravando", "gravando"), ("parado", "parado")])
        self.assertEqual((codigo, n), (0, 4))
        self.assertFalse((pasta / ".acompanhante_vivo").exists())

    def test_aviso_do_fim_que_falhou_e_repetido(self):
        codigo, n, _, _ = self.rodar([("gravando", "gravando"), ("parado", "gravando"),
                                      ("parado", "parado")])
        self.assertEqual((codigo, n), (0, 3))

    def test_desiste_se_a_sessao_nao_comeca(self):
        rodadas = [("parado", "parado")] * 30
        codigo, n, avisos, _ = self.rodar(rodadas, relogio=iter(range(0, 10**6, 300)))
        self.assertEqual(codigo, 1)
        self.assertLess(n, 30)
        self.assertIn("NAO comecou", avisos[-1])

    def test_segundo_acompanhante_desiste(self):
        pasta = Path(tempfile.mkdtemp())
        (pasta / ".acompanhante_vivo").write_text("123", encoding="utf-8")
        args = argparse.Namespace(saida=pasta, intervalo=60.0, telegram_token="t",
                                  telegram_chat="c", incluir_ociosidade=False)
        with mock.patch.object(pub, "publicar") as publicar:
            self.assertEqual(pub.acompanhar(args), 0)
        publicar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
