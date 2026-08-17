"""Configuracao unica da IDS usada por teste, centro de massa, calibracao e tracker."""

import os
from pathlib import Path


# ===== AJUSTE ESTES VALORES PARA A IMAGEM DO EXPERIMENTO =====

# A API da IDS usa microssegundos. 7276 us = 7.276 ms.
EXPOSURE_US = 7276.0

# Limite de aquisicao configurado na camera.
FRAME_RATE_FPS = 20.0

# Ganhos vistos no IDS peak Cockpit.
ANALOG_GAIN = 1.0
DIGITAL_GAIN = 1.0


# ===== NORMALMENTE NAO E NECESSARIO ALTERAR =====

DEVICE_INDEX = 0
CAPTURE_TIMEOUT_MS = 5000
TEST_FRAMES = 50
OUTPUT_DIR = Path(__file__).resolve().parent / "resultados"


def apply_environment() -> None:
    """Propaga esta configuracao para o backend compartilhado."""
    os.environ["QKD_CAMERA_BACKEND"] = "ids"
    os.environ["QKD_IDS_FPS"] = str(FRAME_RATE_FPS)
    os.environ["QKD_IDS_EXPOSURE_US"] = str(EXPOSURE_US)
    os.environ["QKD_IDS_ANALOG_GAIN"] = str(ANALOG_GAIN)
    os.environ["QKD_IDS_DIGITAL_GAIN"] = str(DIGITAL_GAIN)
    os.environ["QKD_IDS_DEVICE"] = str(DEVICE_INDEX)
    os.environ["QKD_IDS_TIMEOUT_MS"] = str(CAPTURE_TIMEOUT_MS)
    os.environ["QKD_CAMERA_OUTPUT_DIR"] = str(OUTPUT_DIR)
