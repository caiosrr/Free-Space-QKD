"""Configuracao unica da camera ASI."""

from modulos.configuracoes.ajustes_de_maquina import sobrepor

# Valores padrao. NAO EDITE AQUI para ajustar a camera de uma maquina: crie ao
# lado um camera_asi_local.py (copie camera_asi_local.exemplo.py). Ele fica fora
# do git, e o git pull nunca mais recusa.
GAIN = 100
EXPOSURE_US = 700.0

# Endereco da camera no ASCOM Remote Server / Alpaca.
ALPACA_ADDRESS = "127.0.0.1:11111"
DEVICE_NUMBER = 0

# Ajustes desta maquina, de camera_asi_local.py, se existir. Antes das contas e
# das validacoes abaixo, para elas valerem sobre o valor que sera usado.
AJUSTES_LOCAIS = sobrepor(globals(), "camera_asi_local", (
    "GAIN", "EXPOSURE_US", "ALPACA_ADDRESS", "DEVICE_NUMBER",
))

# A API ASCOM recebe a exposicao em segundos.
EXPOSURE_SECONDS = EXPOSURE_US * 1e-6

if GAIN < 0:
    raise ValueError("GAIN da ASI nao pode ser negativo.")
if EXPOSURE_US <= 0:
    raise ValueError("EXPOSURE_US da ASI deve ser maior que zero.")
