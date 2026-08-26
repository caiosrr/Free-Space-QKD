"""Atalho clicavel para executar a calibracao continua com a camera IDS.

Abra este arquivo no VS Code e use o botao Play. A implementacao da calibracao
continua em ``calibracoes/calibracao_continua_core.py``; este arquivo apenas
aplica a configuracao IDS do Link UFF e escolhe o perfil.
"""

import os
import sys
from pathlib import Path


# Troque para "rapido" somente quando quiser uma verificacao preliminar curta.
PERFIL = "robusto"

LINK_UFF_DIR = Path(__file__).resolve().parent
CODIGOS_DIR = LINK_UFF_DIR.parent
if str(LINK_UFF_DIR) not in sys.path:
    sys.path.insert(0, str(LINK_UFF_DIR))
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import config_camera_ids


config_camera_ids.apply_environment()
os.environ["QKD_CALIBRATION_PROFILE"] = PERFIL

from calibracoes.calibracao_continua_core import main


if __name__ == "__main__":
    main(PERFIL)
