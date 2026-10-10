"""O script de instalacao so registra programas que existem.

A tarefa de boot aponta para um arquivo. Se ele for renomeado ou fundido em
outro, a tarefa falha em silencio a cada boot, e o mount fica sem a parada pela
serial. Foi o que a fusao de 2026-10-10 (parar_mount_direto.py dentro do
parar_mount.py) teria causado sem este teste.
"""

import re
import unittest
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parents[2]
SCRIPT = CODIGOS_DIR / "diversos" / "instalacao" / "registrar_tarefas_seguranca.ps1"


class ScriptDeInstalacaoTests(unittest.TestCase):
    def test_todo_programa_citado_existe(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        citados = set(re.findall(r"programas_principais[\\/]\w+\.py", texto))
        self.assertTrue(citados, "nenhum programa citado: o padrao deixou de casar")
        for caminho in sorted(citados):
            with self.subTest(caminho=caminho):
                self.assertTrue((CODIGOS_DIR / caminho.replace("\\", "/")).is_file(), caminho)

    def test_a_tarefa_de_boot_usa_a_serial(self):
        texto = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('-Argument "programas_principais\\parar_mount.py --via serial --aguardar 600"', texto)


if __name__ == "__main__":
    unittest.main()
