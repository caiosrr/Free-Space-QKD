"""Inicia a calibracao continua com a camera ZWO ou IDS."""

import os
import sys
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))


def configurar() -> tuple[str, str]:
    camera = input("Camera (1=ZWO SDK, 2=IDS) [1]: ").strip() or "1"
    perfil = input("Perfil (1=robusto, 2=rapido) [1]: ").strip() or "1"
    if perfil not in {"1", "2"}:
        raise ValueError("Escolha 1 para robusto ou 2 para rapido.")
    nome_perfil = "robusto" if perfil == "1" else "rapido"

    if camera == "2":
        from modulos.configuracoes import camera_ids

        camera_ids.apply_environment()
        nome_camera = "IDS"
    elif camera == "1":
        from modulos.configuracoes.camera_asi import EXPOSURE_US, GAIN

        os.environ["QKD_CAMERA_BACKEND"] = "zwo_sdk"
        os.environ["QKD_ZWO_EXPOSURE_US"] = str(EXPOSURE_US)
        os.environ["QKD_ZWO_GAIN"] = str(GAIN)
        nome_camera = "ZWO SDK"
    else:
        raise ValueError("Escolha 1 para ZWO SDK ou 2 para IDS.")

    os.environ["QKD_CALIBRATION_PROFILE"] = nome_perfil
    return nome_camera, nome_perfil


if __name__ == "__main__":
    camera, perfil = configurar()
    print(f"Iniciando calibracao {perfil} com {camera}.")
    from modulos.calibracao.calibracao_continua_core import main

    main(perfil)
