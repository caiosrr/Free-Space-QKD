"""Varredura de apontamento: plano, limites e retorno ao inicio com o mount simulado."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from programas_principais import varrer_apontamento as va  # noqa: E402


class MountFalso:
    def __init__(self, az=359.5, alt=-0.45, falhar_no=None):
        self.az, self.alt = az, alt
        self.movimentos = []
        self.falhar_no = falhar_no

    def read_altaz(self):
        return self.az, self.alt

    def mover_para(self, az, alt):
        self.movimentos.append((az, alt))
        if self.falhar_no is not None and len(self.movimentos) == self.falhar_no:
            raise KeyboardInterrupt
        self.az, self.alt = az, alt

    def funcoes(self):
        return {"read_altaz": self.read_altaz, "mover_para": self.mover_para, "parar": lambda: None}


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def agora(self):
        return self.t

    def dormir(self, dt):
        self.t += dt


class PlanoTests(unittest.TestCase):
    def test_cada_ponto_fica_entre_dois_centros(self):
        seq = va.plano([10, 20], ["az"])
        self.assertEqual(seq, [("az", 0), ("az", 10), ("az", 0), ("az", -10), ("az", 0),
                               ("az", 20), ("az", 0), ("az", -20), ("az", 0)])

    def test_limites(self):
        with self.assertRaises(ValueError):
            va.validar([10, 400], ["az"])
        with self.assertRaises(ValueError):
            va.validar([10], ["ra"])
        with self.assertRaises(ValueError):
            va.validar([], ["az"])


class ExecucaoTests(unittest.TestCase):
    def rodar(self, mount):
        r = Relogio()
        with tempfile.TemporaryDirectory() as tmp, patch("builtins.print"):
            pasta = Path(tmp)
            try:
                va.executar(va.plano([10], ["alt"]), 2.0, 0.003, pasta,
                            mount=mount.funcoes(), relogio=r.agora, dormir=r.dormir)
            except KeyboardInterrupt:
                pass
            with (pasta / "varredura.csv").open(encoding="utf-8") as f:
                linhas = list(csv.DictReader(f))
        return linhas

    def test_percorre_e_volta_ao_inicio(self):
        m = MountFalso()
        linhas = self.rodar(m)
        self.assertEqual(m.movimentos[-1], (359.5, -0.45))
        self.assertEqual((m.az, m.alt), (359.5, -0.45))
        medidas = {(l["ponto_arcsec"]) for l in linhas if l["fase"] == "medir"}
        self.assertEqual(medidas, {"0", "10", "-10"})
        no_ponto = [l for l in linhas if l["fase"] == "medir" and l["ponto_arcsec"] == "10"]
        self.assertAlmostEqual(float(no_ponto[0]["desloc_alt_arcsec"]), 10.0, places=1)
        self.assertEqual(linhas[-1]["fase"], "retorno")

    def test_ctrl_c_no_meio_ainda_volta_ao_inicio(self):
        m = MountFalso(falhar_no=3)
        self.rodar(m)
        self.assertEqual(m.movimentos[-1], (359.5, -0.45))


class TravaTests(unittest.TestCase):
    def test_varredura_gravando_trava_o_mount(self):
        from modulos.controle import mount_em_uso
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "varredura_x"
            run.mkdir()
            (run / "varredura.csv").write_text("t\n", encoding="utf-8")
            with patch.object(mount_em_uso, "VARREDURA_RUNS", Path(tmp)), \
                    patch.object(mount_em_uso, "TRACKER_SESSOES", Path(tmp) / "nada"), \
                    patch.object(mount_em_uso, "CALIBRACAO_RUNS", Path(tmp) / "nada"):
                self.assertIn("varredura", mount_em_uso.motivo_de_uso())


if __name__ == "__main__":
    unittest.main()
