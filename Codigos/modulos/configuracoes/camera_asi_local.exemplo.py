"""Ajustes da ASI NESTA maquina. Copie para camera_asi_local.py e edite.

camera_asi_local.py fica fora do git: mude a vontade, o git pull nao reclama.
Na bancada da USP, com o DMD, a exposicao tem de cobrir ciclos inteiros dele
(16667 us), senao aparecem faixas pretas andando pela imagem.
"""

EXPOSURE_US = 16667.0
GAIN = 150
