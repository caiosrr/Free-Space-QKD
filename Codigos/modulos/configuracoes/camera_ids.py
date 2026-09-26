"""Configuracao unica da IDS usada por teste, centro de massa, calibracao e tracker."""

import os

from modulos.configuracoes import saidas
from modulos.configuracoes.ajustes_de_maquina import sobrepor


# ===== VALORES PADRAO =====
#
# NAO EDITE AQUI para ajustar a camera de uma maquina: crie ao lado um
# camera_ids_local.py (copie camera_ids_local.exemplo.py) e ponha nele so o
# que muda. Ele fica fora do git, e o git pull nunca mais recusa.

# A API da IDS usa microssegundos. 7276 us = 7.276 ms.
EXPOSURE_US = 6076.0

# Limite de aquisicao configurado na camera.
FRAME_RATE_FPS = 50.0


# Ganhos vistos no IDS peak Cockpit.
ANALOG_GAIN = 1.0
DIGITAL_GAIN = 1.0

# False usa as coordenadas nativas da IDS, sem girar a imagem.
# Se quiser restaurar o comportamento antigo, troque apenas para True.
ROTATE_IMAGE_180 = False


# ===== NORMALMENTE NAO E NECESSARIO ALTERAR =====

DEVICE_INDEX = 0

# Ajustes desta maquina, de camera_ids_local.py, se existir.
AJUSTES_LOCAIS = sobrepor(globals(), "camera_ids_local", (
    "EXPOSURE_US", "FRAME_RATE_FPS", "ANALOG_GAIN", "DIGITAL_GAIN",
    "ROTATE_IMAGE_180", "DEVICE_INDEX",
))
CAPTURE_TIMEOUT_MS = 5000
BUFFER_COUNT = 8
TEST_FRAMES = 50

# As pastas de saida sao as mesmas para todas as cameras; ver saidas.py.
RESULTS_DIR = saidas.RESULTS_DIR


def apply_environment() -> None:
    """Propaga esta configuracao para o backend compartilhado."""
    os.environ["QKD_CAMERA_BACKEND"] = "ids"
    os.environ["QKD_IDS_FPS"] = str(FRAME_RATE_FPS)
    os.environ["QKD_IDS_EXPOSURE_US"] = str(EXPOSURE_US)
    os.environ["QKD_IDS_ANALOG_GAIN"] = str(ANALOG_GAIN)
    os.environ["QKD_IDS_DIGITAL_GAIN"] = str(DIGITAL_GAIN)
    os.environ["QKD_ROTATE_IMAGE_180"] = "1" if ROTATE_IMAGE_180 else "0"
    os.environ["QKD_IDS_DEVICE"] = str(DEVICE_INDEX)
    os.environ["QKD_IDS_TIMEOUT_MS"] = str(CAPTURE_TIMEOUT_MS)
    os.environ["QKD_IDS_BUFFER_COUNT"] = str(BUFFER_COUNT)
    saidas.aplicar_saidas()
