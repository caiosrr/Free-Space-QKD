"""Executa a calibracao foco multiplo usando a IDS U3-3680XCP-NIR."""

import os
import runpy
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

if __name__ == "__main__":
    target = (
        CODIGOS_DIR
        / "foco_multiplos"
        / "Calibracao_ang-pix_dual_v3_foco_temp.py"
    )
    runpy.run_path(str(target), run_name="__main__")
