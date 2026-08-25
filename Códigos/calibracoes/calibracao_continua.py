"""Executavel unico da calibracao continua para ZWO SDK ou IDS peak."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parents[1]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", choices=("zwo", "ids"), default="zwo")
    parser.add_argument("--perfil", choices=("rapido", "robusto"), default="robusto")
    parser.add_argument("--sdk-path", help="caminho completo de ASICamera2.dll")
    parser.add_argument("--rotacionar-180", action="store_true", help="mantem a convencao antiga da imagem")
    return parser.parse_args()


def _configure(args) -> None:
    if args.camera == "ids":
        link_dir = CODIGOS_DIR / "Link UFF"
        if str(link_dir) not in sys.path:
            sys.path.insert(0, str(link_dir))
        import config_camera_ids

        config_camera_ids.apply_environment()
    else:
        from config_camera_asi import EXPOSURE_US, GAIN

        os.environ["QKD_CAMERA_BACKEND"] = "zwo_sdk"
        os.environ.setdefault("QKD_ZWO_EXPOSURE_US", str(EXPOSURE_US))
        os.environ.setdefault("QKD_ZWO_GAIN", str(GAIN))
    if args.sdk_path:
        os.environ["QKD_ZWO_SDK_PATH"] = args.sdk_path
    if args.rotacionar_180:
        os.environ["QKD_ROTATE_IMAGE_180"] = "1"
    os.environ["QKD_CALIBRATION_PROFILE"] = args.perfil


def main() -> None:
    args = _arguments()
    _configure(args)
    from calibracoes.calibracao_continua_core import main as run

    run(args.perfil)


if __name__ == "__main__":
    main()
