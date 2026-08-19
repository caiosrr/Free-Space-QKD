"""Configuracao unica da camera ASI usada pelos programas normais."""

# Ajuste somente estes valores para centro de massa, calibracao e tracker.
GAIN = 100
EXPOSURE_US = 700.0

# Endereco da camera no ASCOM Remote Server / Alpaca.
ALPACA_ADDRESS = "127.0.0.1:11111"
DEVICE_NUMBER = 0

# A API ASCOM recebe a exposicao em segundos.
EXPOSURE_SECONDS = EXPOSURE_US * 1e-6

if GAIN < 0:
    raise ValueError("GAIN da ASI nao pode ser negativo.")
if EXPOSURE_US <= 0:
    raise ValueError("EXPOSURE_US da ASI deve ser maior que zero.")
