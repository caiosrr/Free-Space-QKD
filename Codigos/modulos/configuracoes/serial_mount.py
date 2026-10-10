"""Como a parada de emergencia acha a porta serial do mount, quando fala LX200 direto.

Tres formas, da mais segura para a menos:

1. Pela IDENTIDADE USB (USB_VID, USB_PID e, se der, USB_SERIE). O Windows
   informa de cada aparelho USB o fabricante, o produto e o numero de serie
   sem que nenhuma porta seja aberta. A parada so abre a porta do aparelho que
   bate com os tres; se nenhum bater, nao toca em nada. Obrigatorio num PC
   compartilhado.
2. Pelo NUMERO da porta (PORTA_SERIAL, por exemplo "COM4"). Nenhuma outra porta
   e tocada, mas o numero pode mudar: o mount em outra entrada USB, ou um
   aparelho de outro grupo ligado antes, e a COM4 passa a ser outra coisa, que
   receberia os comandos de parada.
3. Sem nada configurado, a parada DESCOBRE: pergunta a identidade (:GVP#) em
   cada porta serial. Pratico num PC so nosso, e perigoso num compartilhado.

Desde 2026-09-29 a bancada da UFF divide o PC com os lasers OBIS de outro grupo,
cujo programa usa portas seriais USB. Defina por maquina, em
serial_mount_local.py ao lado deste arquivo (fora do git). Os numeros saem de

    python -m serial.tools.list_ports -v

que so lista os aparelhos, sem abrir nenhuma porta. Ver
serial_mount_local.exemplo.py.
"""

from modulos.configuracoes.ajustes_de_maquina import sobrepor

USB_VID: int | None = None
USB_PID: int | None = None
USB_SERIE: str | None = None
PORTA_SERIAL: str | None = None

AJUSTES_LOCAIS = sobrepor(globals(), "serial_mount_local",
                          ("USB_VID", "USB_PID", "USB_SERIE", "PORTA_SERIAL"))
