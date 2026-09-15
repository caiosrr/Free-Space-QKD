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
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import mount_address, stop_axes_safely

DIARIO = CODIGOS_DIR / "resultados" / "parar_mount.txt"


def anotar(texto: str) -> None:
    """Deixa rastro em disco, porque como tarefa agendada ninguem le a tela.

    Sem isto nao havia como saber se a protecao de boot chegou a rodar: a
    unica evidencia era o LastTaskResult do agendador, que diz que o processo
    saiu com zero, nao o que ele encontrou no mount.
    """
    try:
        DIARIO.parent.mkdir(parents=True, exist_ok=True)
        with DIARIO.open("a", encoding="utf-8") as arquivo:
            momento = datetime.now().astimezone().isoformat(timespec="seconds")
            arquivo.write(f"{momento}  {texto}\n")
    except Exception:
        # Parar o mount importa mais do que registrar que parou.
        pass


def main() -> int:
    print(f"Parando o mount em {mount_address()} ...")
    try:
        ok = stop_axes_safely(attempts=3, timeout=3.0)
    except Exception as exc:
        # Mount desligado ou driver fora do ar tambem e um desfecho seguro:
        # sem servidor ASCOM nao ha quem mantenha um MoveAxis em curso.
        print(f"Nao foi possivel falar com o mount: {exc}")
        print("Se o driver estiver fora do ar, nao ha comando de movimento ativo.")
        anotar(f"sem contato com o mount: {type(exc).__name__}: {exc}")
        return 1
    if ok:
        print("Ambos os eixos confirmados em velocidade zero.")
        anotar("eixos confirmados em velocidade zero")
        return 0
    print("ALERTA: nao consegui confirmar a parada. Verifique o mount FISICAMENTE.")
    anotar("ALERTA: nao confirmou a parada dos eixos")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
