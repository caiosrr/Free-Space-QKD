"""Inicia o tracker principal com a camera ASI ou IDS."""

import os
import sys
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))


def configurar_camera() -> str:
    escolha = input("Camera (1=ASI/ASCOM, 2=IDS) [1]: ").strip() or "1"
    if escolha == "2":
        from modulos.configuracoes import camera_ids

        camera_ids.apply_environment()
        return "IDS"
    if escolha != "1":
        raise ValueError("Escolha 1 para ASI/ASCOM ou 2 para IDS.")
    os.environ["QKD_CAMERA_BACKEND"] = "alpaca"
    return "ASI/ASCOM"


if __name__ == "__main__":
    camera = configurar_camera()
    print(f"Iniciando tracker com {camera}.")
    from modulos.controle.Tracker import main

    main()
