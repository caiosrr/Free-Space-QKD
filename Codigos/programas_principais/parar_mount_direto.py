"""Para o mount falando LX200 direto na serial, sem o servidor ASCOM.

Por que existe: um MoveAxis em curso NAO para quando o servidor ASCOM cai --
medido neste mount em 2026-09-14, com o servidor fechado no meio de um pulso o
eixo andou 266 arcsec quando 246 eram previstos. Depois de um reinicio o eixo
pode seguir andando, e o parar_mount.py nao alcanca nada enquanto o servidor
ASCOM nao subir. Como ele e um programa de janela, numa conta com senha isso
significa esperar alguem fazer login.

Este programa fala com o AM5 pela porta serial, que nao precisa de desktop nem
de login. Registrado como tarefa de boot rodando como SYSTEM, ele para o eixo
com a maquina trancada.

Identificado em 2026-09-14: AM5N em COM5 a 9600 baud.

DUAS TRAVAS contra parar o que nao devia:

1. Se houver telemetria sendo escrita, ha uma sessao viva e o programa nao toca
   em nada: parar seria interromper um rastreio legitimo.
2. Se o servidor ASCOM estiver no ar, ele segura a porta serial com
   exclusividade e este programa simplesmente nao abre. Isso e proposital: ele
   so consegue agir justamente no cenario em que e necessario.

Uso, a partir da pasta Codigos:

    python programas_principais/parar_mount_direto.py
    python programas_principais/parar_mount_direto.py --aguardar 600

Precisa de pyserial:  python -m pip install pyserial
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

PORTA_PADRAO = "COM5"
BAUD_PADRAO = 9600

# Todos os comandos de parada do LX200. O driver do ASCOM pode estar usando
# movimento por eixo ou por direcao; mandar os cinco cobre as duas formas sem
# precisar adivinhar qual foi.
PARADAS = (b":Q#", b":Qe#", b":Qw#", b":Qn#", b":Qs#")
# Leituras de posicao em AltAz, para conferir se ainda esta andando.
POSICAO = (b":GZ#", b":GA#")

DIARIO = CODIGOS_DIR / "resultados" / "parar_mount.txt"
SESSOES = CODIGOS_DIR / "Link UFF" / "resultados" / "tracker" / "sessoes"
TRACKER_VIVO_S = 30.0


def anotar(texto: str) -> None:
    try:
        DIARIO.parent.mkdir(parents=True, exist_ok=True)
        with DIARIO.open("a", encoding="utf-8") as arquivo:
            momento = datetime.now().astimezone().isoformat(timespec="seconds")
            arquivo.write(f"{momento}  [direto] {texto}\n")
    except Exception:
        # Parar o mount importa mais do que anotar que parou.
        pass


def tracker_gravando() -> bool:
    """Ha uma sessao escrevendo telemetria neste instante?"""
    try:
        if not SESSOES.is_dir():
            return False
        arquivos = list(SESSOES.glob("*/telemetria.csv"))
        if not arquivos:
            return False
        recente = max(arquivos, key=lambda caminho: caminho.stat().st_mtime)
        return (time.time() - recente.stat().st_mtime) < TRACKER_VIVO_S
    except Exception:
        return False


def ler_posicao(porta_serial) -> str:
    """Azimute e altitude como o mount os devolve, sem interpretar."""
    leituras = []
    for comando in POSICAO:
        porta_serial.reset_input_buffer()
        porta_serial.write(comando)
        leituras.append(porta_serial.read(32).decode("ascii", "replace").strip())
    return " ".join(leituras)


def parar(porta: str, baud: int, timeout: float = 2.0) -> tuple[bool, str]:
    """Manda parar e confere pela posicao. Devolve (parado, descricao)."""
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        return False, "pyserial ausente: python -m pip install pyserial"
    try:
        with serial.Serial(porta, baud, timeout=timeout) as s:
            for comando in PARADAS:
                s.write(comando)
                time.sleep(0.05)
            # Confere movendo o relogio, nao a fe: duas leituras separadas. Se a
            # posicao mudar entre elas, algo ainda esta girando.
            antes = ler_posicao(s)
            time.sleep(1.5)
            depois = ler_posicao(s)
        if not antes or not depois:
            return False, f"{porta} respondeu vazio; parada NAO confirmada"
        if antes == depois:
            return True, f"parado e confirmado em {porta} (posicao {depois})"
        return False, f"AINDA EM MOVIMENTO em {porta}: {antes} -> {depois}"
    except Exception as exc:
        return False, f"{porta}: {type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--porta", default=PORTA_PADRAO)
    parser.add_argument("--baud", type=int, default=BAUD_PADRAO)
    parser.add_argument(
        "--aguardar", type=float, default=0.0, metavar="SEGUNDOS",
        help="insiste enquanto a porta estiver ocupada ou o mount nao responder",
    )
    parser.add_argument("--intervalo", type=float, default=10.0)
    args = parser.parse_args()

    if tracker_gravando():
        print("Ha uma sessao do tracker gravando agora; nada foi tocado.")
        anotar("abortado: tracker gravando")
        return 0

    print(f"Parando o mount por {args.porta} a {args.baud} baud ...")
    limite = time.monotonic() + max(0.0, args.aguardar)
    tentativas = 0
    while True:
        tentativas += 1
        ok, descricao = parar(args.porta, args.baud)
        print(f"  {descricao}")
        if ok:
            anotar(f"{descricao} (tentativa {tentativas})")
            return 0
        if time.monotonic() >= limite:
            break
        if tracker_gravando():
            print("Tracker comecou a gravar durante a espera; desistindo.")
            anotar("abortado na espera: tracker comecou a gravar")
            return 0
        time.sleep(max(1.0, args.intervalo))

    anotar(f"{descricao} (desistiu apos {tentativas} tentativas)")
    print()
    print("Se a porta estiver ocupada, o servidor ASCOM a esta segurando:")
    print("nesse caso use parar_mount.py, que fala pelo proprio ASCOM.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
