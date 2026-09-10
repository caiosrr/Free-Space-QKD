"""Para os dois eixos do mount. Nao usa camera e nao depende do tracker.

Existe para um cenario especifico: o tracker foi morto sem chance de encerrar --
desligamento do Windows, queda de energia, logoff -- no meio de um pulso. O
ASCOM MoveAxis nao tem prazo: uma vez comandado, o eixo anda ATE alguem mandar
zero. Se o `MoveAxis(0)` nunca sair, o mount segue andando sozinho.

Quanto isso custa, com os limites deste projeto:

    pulso fino (0,001042 deg/s):   3,75 deg/h    ->  45 deg em 12 h
    velocidade maxima (0,1 deg/s):  360 deg/h    -> tudo, em qualquer prazo

O watchdog de MAX_OFFSET_AZ_DEG/ALT existe, mas roda DENTRO do tracker: morre
junto com ele e nao protege exatamente do caso que mais assusta.

Uso:

    python programas_principais/parar_mount.py

Feito para ser registrado como tarefa do Windows na INICIALIZACAO: assim,
qualquer reinicio inesperado para o mount em segundos, sem operador presente.
Tambem serve para parar o mount a distancia quando algo trava.

Sai com codigo 0 se confirmou a parada dos dois eixos, 1 caso contrario, para
que a tarefa agendada registre a falha.
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import mount_address, stop_axes_safely


def main() -> int:
    print(f"Parando o mount em {mount_address()} ...")
    try:
        ok = stop_axes_safely(attempts=3, timeout=3.0)
    except Exception as exc:
        # Mount desligado ou driver fora do ar tambem e um desfecho seguro:
        # sem servidor ASCOM nao ha quem mantenha um MoveAxis em curso.
        print(f"Nao foi possivel falar com o mount: {exc}")
        print("Se o driver estiver fora do ar, nao ha comando de movimento ativo.")
        return 1
    if ok:
        print("Ambos os eixos confirmados em velocidade zero.")
        return 0
    print("ALERTA: nao consegui confirmar a parada. Verifique o mount FISICAMENTE.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
