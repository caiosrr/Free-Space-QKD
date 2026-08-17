"""Executa o centro de massa/foco multiplo usando a IDS U3-3680XCP-NIR."""

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

from foco_multiplos import Center_of_Mass_foco_temp as programa


programa.CAMERA_GAIN = camera_config.ANALOG_GAIN
programa.EXPOSURE_SECONDS = camera_config.EXPOSURE_US * 1e-6
programa.CAPTURE_COOLDOWN_SLEEP_S = 0.0


if __name__ == "__main__":
    programa.main()
