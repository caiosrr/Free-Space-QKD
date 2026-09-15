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

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import mount_address, stop_axes_safely
from modulos.controle.mount_em_uso import motivo_de_uso

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


def tentar_parar() -> tuple[bool, str]:
    """Uma tentativa. Devolve (parou, descricao do que aconteceu)."""
    try:
        if stop_axes_safely(attempts=3, timeout=3.0):
            return True, "eixos confirmados em velocidade zero"
        return False, "ALERTA: nao confirmou a parada dos eixos"
    except Exception as exc:
        return False, f"sem contato com o mount: {type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--aguardar", type=float, default=0.0, metavar="SEGUNDOS",
        help=(
            "insiste ate conseguir falar com o mount. No boot o servidor ASCOM"
            " normalmente ainda nao subiu, e uma tentativa unica falha sempre."
        ),
    )
    parser.add_argument("--intervalo", type=float, default=10.0)
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}); nada foi tocado.")
        anotar(f"abortado: {uso}")
        return 0

    print(f"Parando o mount em {mount_address()} ...")
    limite = time.monotonic() + max(0.0, args.aguardar)
    tentativas = 0
    while True:
        tentativas += 1
        parou, descricao = tentar_parar()
        if parou:
            print("Ambos os eixos confirmados em velocidade zero.")
            anotar(f"{descricao} (tentativa {tentativas})")
            return 0
        if time.monotonic() >= limite:
            break
        uso = motivo_de_uso()
        if uso:
            print(f"Mount passou a ser usado durante a espera ({uso}); desistindo.")
            anotar(f"abortado na espera: {uso}")
            return 0
        # Enquanto o servidor ASCOM nao responde, o eixo pode estar andando: um
        # MoveAxis em curso NAO para quando o servidor cai, medido neste mount
        # em 2026-09-14. Insistir e a unica forma de alcanca-lo.
        time.sleep(max(1.0, args.intervalo))

    print(descricao)
    if "sem contato" in descricao:
        print("Se o driver estiver fora do ar, o eixo pode seguir em movimento:")
        print("abra o servidor ASCOM e rode este programa de novo.")
    else:
        print("Verifique o mount FISICAMENTE.")
    anotar(f"{descricao} (desistiu apos {tentativas} tentativas)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
