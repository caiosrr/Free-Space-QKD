"""Identidade do mount NESTA maquina. Copie para serial_mount_local.py e edite.

Obrigatorio no PC compartilhado da UFF: sem isto a parada de emergencia pergunta
a identidade em todas as portas seriais, inclusive as dos lasers de outro grupo.

Os numeros saem de um comando que so lista os aparelhos, sem abrir porta
nenhuma (pasta Codigos):

    .venv\\Scripts\\python.exe -m serial.tools.list_ports -v

Na linha do mount aparece algo como "USB VID:PID=03C3:1234 SER=ABC123". Os dois
numeros de VID:PID sao hexadecimais: escreva com 0x na frente.

Com USB_VID e USB_PID definidos, PORTA_SERIAL e ignorada: a porta e achada pela
identidade, qualquer que seja o numero que o Windows der a ela.
"""

USB_VID = 0x0000   # fabricante, da linha do mount
USB_PID = 0x0000   # produto
USB_SERIE = None   # numero de serie, entre aspas, se a linha tiver SER=
