"""Inicia o teste de aquisicao da camera IDS."""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.cameras.teste_ids import main


if __name__ == "__main__":
    raise SystemExit(main())
