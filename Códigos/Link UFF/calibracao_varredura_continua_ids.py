"""Executa a calibracao IDS por varredura continua no Link UFF."""

import sys
from pathlib import Path

LINK_UFF_DIR = Path(__file__).resolve().parent
CODIGOS_DIR = LINK_UFF_DIR.parent
if str(LINK_UFF_DIR) not in sys.path:
    sys.path.insert(0, str(LINK_UFF_DIR))
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import config_camera_ids as camera_config

camera_config.apply_environment()

from foco_multiplos.calibracao_varredura_continua import main


if __name__ == "__main__":
    main()
