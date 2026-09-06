"""Endereco unico do ASCOM Remote / Alpaca.

Camera ASI e mount conversam com o mesmo servidor. Manter o endereco aqui
evita que uma mudanca de host/porta seja aplicada so na camera, deixando o
mount apontando em silencio para o endereco antigo.
"""

ALPACA_ADDRESS = "127.0.0.1:11111"
CAMERA_DEVICE_NUMBER = 0
TELESCOPE_DEVICE_NUMBER = 0
