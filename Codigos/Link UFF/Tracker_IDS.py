"""Inicia o tracker usando a camera IDS U3-3680XCP-NIR.

Este arquivo faz somente duas coisas:
1. aplica exposicao, ganho e caminhos exclusivos do Link UFF;
2. chama o tracker comum em ``controle/Tracker.py``.
"""

import sys
from pathlib import Path

LINK_UFF_DIR = Path(__file__).resolve().parent
CODIGOS_DIR = LINK_UFF_DIR.parent
if str(LINK_UFF_DIR) not in sys.path:
    sys.path.insert(0, str(LINK_UFF_DIR))
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import config_camera_ids as camera_config

# Seleciona a IDS e os resultados do Link UFF antes de importar o tracker.
camera_config.apply_environment()

from controle.Tracker import main


if __name__ == "__main__":
    main()
