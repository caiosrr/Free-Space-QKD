"""Executa o tracker continuo usando a IDS U3-3680XCP-NIR."""

import runpy
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


if __name__ == "__main__":
    runpy.run_path(str(CODIGOS_DIR / "controle" / "Tracker.py"), run_name="__main__")
