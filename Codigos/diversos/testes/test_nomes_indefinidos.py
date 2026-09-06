"""Roda o Ruff junto da suite, para nome indefinido nao chegar a bancada.

O `pyproject.toml` ja seleciona F82 (nomes indefinidos) desde sempre, mas o
Ruff nao estava instalado no .venv, entao a regra existia so no papel. Em
2026-09-06 uma calibracao inteira de 290 s foi perdida por um
`pulse_diagnostics` que sobrou de um bloco removido: o codigo rodou ate o
relatorio final e so entao levantou NameError. O Ruff aponta isso em
milissegundos.

Como a suite e o unico passo que roda sempre antes de um push, a verificacao
mora aqui e nao num passo manual separado.
"""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parents[2]


def _ruff_disponivel() -> bool:
    if shutil.which("ruff"):
        return True
    resultado = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"],
        capture_output=True,
        cwd=CODIGOS_DIR,
    )
    return resultado.returncode == 0


class RuffTests(unittest.TestCase):
    @unittest.skipUnless(
        _ruff_disponivel(),
        "Ruff ausente: instale com 'python -m pip install -r requirements.txt' "
        "para ativar a checagem de nomes indefinidos.",
    )
    def test_sem_nomes_indefinidos_nem_erro_de_sintaxe(self):
        resultado = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "."],
            capture_output=True,
            text=True,
            cwd=CODIGOS_DIR,
        )
        self.assertEqual(
            resultado.returncode,
            0,
            "Ruff encontrou problemas:\n" + resultado.stdout + resultado.stderr,
        )


if __name__ == "__main__":
    unittest.main()
