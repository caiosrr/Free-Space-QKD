"""Porta serial do mount, quando a parada de emergencia fala LX200 direto.

Sem porta configurada, a parada descobre sozinha: pergunta a identidade (:GVP#)
em cada porta serial do PC. Isso e pratico num PC so nosso, e perigoso num
compartilhado. Desde 2026-09-29 a bancada da UFF divide o PC com um laser de
405 nm de outro grupo, e controladores de laser costumam falar por serial. No
boot, antes de o programa do laser abrir a porta dele, a descoberta mandaria
bytes ao controlador do laser.

Com PORTA_SERIAL definida, NENHUMA outra porta e tocada. Defina por maquina, em
serial_mount_local.py ao lado deste arquivo (fora do git), por exemplo:

    PORTA_SERIAL = "COM4"
"""

from modulos.configuracoes.ajustes_de_maquina import sobrepor

PORTA_SERIAL: str | None = None

AJUSTES_LOCAIS = sobrepor(globals(), "serial_mount_local", ("PORTA_SERIAL",))
