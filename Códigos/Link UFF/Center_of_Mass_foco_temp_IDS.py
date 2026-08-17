"""Executa o centro de massa/foco multiplo usando a IDS U3-3680XCP-NIR."""

import os
import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

os.environ["QKD_CAMERA_BACKEND"] = "ids"
os.environ.setdefault("QKD_IDS_FPS", "20")
os.environ.setdefault("QKD_IDS_EXPOSURE_US", "7276")
os.environ.setdefault("QKD_IDS_ANALOG_GAIN", "1")
os.environ.setdefault("QKD_IDS_DIGITAL_GAIN", "1")

from foco_multiplos import Center_of_Mass_foco_temp as programa


programa.CAMERA_GAIN = 1
programa.EXPOSURE_SECONDS = 7.276e-3
programa.CAPTURE_COOLDOWN_SLEEP_S = 0.0


if __name__ == "__main__":
    programa.main()
