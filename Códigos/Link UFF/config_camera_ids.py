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

# Cada etapa do experimento grava e le somente dentro de Link UFF/resultados.
RESULTS_DIR = Path(__file__).resolve().parent / "resultados"
ACQUISITION_OUTPUT_DIR = RESULTS_DIR / "aquisicao"
CENTER_OF_MASS_OUTPUT_DIR = RESULTS_DIR / "centro_de_massa"
CALIBRATION_OUTPUT_DIR = RESULTS_DIR / "calibracao"
CALIBRATION_METADATA_DIR = CALIBRATION_OUTPUT_DIR / "metadados"
MATRICES_OUTPUT_DIR = RESULTS_DIR / "matrizes"
TRACKER_OUTPUT_DIR = RESULTS_DIR / "tracker"

# Alias mantido para compatibilidade com scripts locais que importavam OUTPUT_DIR.
OUTPUT_DIR = RESULTS_DIR


def apply_environment() -> None:
    """Propaga esta configuracao para o backend compartilhado."""
    os.environ["QKD_CAMERA_BACKEND"] = "ids"
    os.environ["QKD_IDS_FPS"] = str(FRAME_RATE_FPS)
    os.environ["QKD_IDS_EXPOSURE_US"] = str(EXPOSURE_US)
    os.environ["QKD_IDS_ANALOG_GAIN"] = str(ANALOG_GAIN)
    os.environ["QKD_IDS_DIGITAL_GAIN"] = str(DIGITAL_GAIN)
    os.environ["QKD_IDS_DEVICE"] = str(DEVICE_INDEX)
    os.environ["QKD_IDS_TIMEOUT_MS"] = str(CAPTURE_TIMEOUT_MS)
    os.environ["QKD_CAMERA_OUTPUT_DIR"] = str(RESULTS_DIR)
    os.environ["QKD_CENTER_OF_MASS_OUTPUT_DIR"] = str(CENTER_OF_MASS_OUTPUT_DIR)
    os.environ["QKD_CALIBRATION_OUTPUT_DIR"] = str(CALIBRATION_OUTPUT_DIR)
    os.environ["QKD_CALIBRATION_METADATA_DIR"] = str(CALIBRATION_METADATA_DIR)
    os.environ["QKD_CALIBRATION_MATRIX_DIR"] = str(MATRICES_OUTPUT_DIR)
    os.environ["QKD_TRACKER_OUTPUT_DIR"] = str(TRACKER_OUTPUT_DIR)
