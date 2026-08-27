"""Inicia a caracterizacao temporal do beacon com a camera IDS."""

import sys
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.visao.caracterizacao_beacon import main


if __name__ == "__main__":
    main()
