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

from modulos.controle.mount_em_uso import motivo_de_uso
from modulos.controle.parada_emergencia import parar_pela_serial

DIARIO = CODIGOS_DIR / "resultados" / "parar_mount.txt"


def anotar(texto: str) -> None:
    try:
        DIARIO.parent.mkdir(parents=True, exist_ok=True)
        with DIARIO.open("a", encoding="utf-8") as arquivo:
            momento = datetime.now().astimezone().isoformat(timespec="seconds")
            arquivo.write(f"{momento}  [direto] {texto}\n")
    except Exception:
        # Parar o mount importa mais do que anotar que parou.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--porta", default=None,
                        help="sem isto, descobre qual porta responde")
    parser.add_argument("--baud", type=int, default=BAUD_PADRAO)
    parser.add_argument(
        "--aguardar", type=float, default=0.0, metavar="SEGUNDOS",
        help="insiste enquanto a porta estiver ocupada ou o mount nao responder",
    )
    parser.add_argument("--intervalo", type=float, default=10.0)
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}); nada foi tocado.")
        anotar(f"abortado: {uso}")
        return 0

    alvo = args.porta or "a porta que responder"
    print(f"Parando o mount por {alvo} a {args.baud} baud ...")
    limite = time.monotonic() + max(0.0, args.aguardar)
    tentativas = 0
    while True:
        tentativas += 1
        ok, descricao = parar_pela_serial(args.porta, args.baud)
        print(f"  {descricao}")
        if ok:
            anotar(f"{descricao} (tentativa {tentativas})")
            return 0
        if time.monotonic() >= limite:
            break
        uso = motivo_de_uso()
        if uso:
            print(f"Mount passou a ser usado durante a espera ({uso}); desistindo.")
            anotar(f"abortado na espera: {uso}")
            return 0
        time.sleep(max(1.0, args.intervalo))

    anotar(f"{descricao} (desistiu apos {tentativas} tentativas)")
    print()
    print("Se a porta estiver ocupada, o servidor ASCOM a esta segurando:")
    print("nesse caso use parar_mount.py, que fala pelo proprio ASCOM.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
