"""Inicia a medicao/alinhamento por ilhas com a camera ASI ou IDS."""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse

from programas_principais._iniciador import aplicar_camera, perguntar_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids"], default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    camera = aplicar_camera(args.camera or perguntar_camera({"1": "asi", "2": "ids"}, "1"))
    print(f"Iniciando centro de massa com {camera}.")
    from modulos.visao.detector_ilhas import main

    main()
