"""Inicia a calibracao continua com a camera ZWO ou IDS.

Sem argumentos, pergunta camera e perfil. Com argumentos, roda direto:

    python programas_principais/calibracao.py --camera ids --perfil robusto
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse
import os

from programas_principais._iniciador import aplicar_camera, perguntar_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["zwo", "ids"], default=None)
    parser.add_argument("--perfil", choices=["robusto", "rapido"], default=None)
    return parser.parse_args()


def perguntar_perfil() -> str:
    escolha = input("Perfil (1=robusto, 2=rapido) [1]: ").strip() or "1"
    if escolha not in {"1", "2"}:
        raise ValueError("Escolha 1 para robusto ou 2 para rapido.")
    return "robusto" if escolha == "1" else "rapido"


if __name__ == "__main__":
    args = parse_args()
    escolha = args.camera or perguntar_camera({"1": "zwo", "2": "ids"}, "1")
    camera = aplicar_camera(escolha)
    perfil = args.perfil or perguntar_perfil()
    os.environ["QKD_CALIBRATION_PROFILE"] = perfil

    print(f"Iniciando calibracao {perfil} com {camera}.")
    from modulos.calibracao.calibracao_continua_core import main

    main(perfil)
