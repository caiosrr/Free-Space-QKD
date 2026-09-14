"""Vigia independente: para o mount se o tracker morrer.

O ``MoveAxis`` do ASCOM nao tem prazo -- a propria especificacao diz que o eixo
"continua indefinidamente". Medido neste mount em 2026-09-14: com o servidor
ASCOM FECHADO no meio de um pulso, o eixo andou 266 arcsec quando 246 eram
previstos. Nem o firmware nem a perda da conexao param nada.

Entao, se o tracker morrer no meio de um pulso, o eixo deriva a 3,75 arcsec/s
(velocidade dos micropulsos) ou ate 360 graus/h (velocidade maxima que o
controlador pode comandar).

Este programa roda em paralelo ao tracker, num processo separado, e faz uma
coisa so: se o tracker estava gravando e parou de gravar, manda zero nos eixos.

O QUE ELE COBRE: o tracker morrer sozinho com o Windows de pe. E o caso mais
provavel, e reduz a janela de reacao de ~90 s (o tempo de um boot) para poucos
segundos.

O QUE ELE NAO COBRE: o PC inteiro morrer, porque ele morre junto. Para isso
existe a tarefa de boot com ``parar_mount.py``. E se o PC nao voltar, nada em
software resolve -- so um nobreak.

SO AGE DEPOIS DE VER O TRACKER VIVO. Sem isso ele pararia o mount no meio de
uma calibracao ou de um movimento manual, que e pior que o problema.

Uso, a partir da pasta Codigos, numa janela separada:

    python programas_principais/vigia_mount.py
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

from modulos.controle.mount_ascom import mount_address, stop_axes_safely

SESSOES = CODIGOS_DIR / "Link UFF" / "resultados" / "tracker" / "sessoes"


def idade_da_telemetria() -> float | None:
    """Segundos desde a ultima escrita na telemetria mais recente."""
    if not SESSOES.is_dir():
        return None
    arquivos = list(SESSOES.glob("*/telemetria.csv"))
    if not arquivos:
        return None
    recente = max(arquivos, key=lambda p: p.stat().st_mtime)
    return time.time() - recente.stat().st_mtime


def agora() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--armar-em", type=float, default=15.0,
        help="considera o tracker vivo se a telemetria for mais nova que isto (s)",
    )
    parser.add_argument(
        "--disparar-em", type=float, default=25.0,
        help="para o mount se a telemetria ficar parada por mais que isto (s)",
    )
    parser.add_argument("--intervalo", type=float, default=2.0)
    args = parser.parse_args()
    if args.disparar_em <= args.armar_em:
        print("ERRO: --disparar-em precisa ser maior que --armar-em.")
        return 2

    print(f"Vigia do mount em {mount_address()}")
    print(f"  arma quando a telemetria estiver mais nova que {args.armar_em:.0f} s")
    print(f"  dispara se ela ficar parada por mais de {args.disparar_em:.0f} s")
    print("  Ctrl+C encerra o vigia sem tocar no mount.\n")

    armado = False
    ultimo_aviso = 0.0
    try:
        while True:
            idade = idade_da_telemetria()
            if idade is None:
                estado = "sem sessoes"
            elif idade <= args.armar_em:
                if not armado:
                    print(f"[{agora()}] ARMADO: tracker gravando.")
                armado = True
                estado = f"gravando (telemetria de {idade:.0f} s atras)"
            elif armado and idade > args.disparar_em:
                print(f"\n[{agora()}] TRACKER PAROU de gravar ha {idade:.0f} s.")
                print("[{}] parando os eixos...".format(agora()))
                ok = stop_axes_safely(attempts=3, timeout=3.0)
                print(f"[{agora()}] {'eixos zerados.' if ok else 'FALHA ao zerar, verifique o mount.'}")
                # Desarma para nao ficar repetindo; rearma sozinho se o
                # tracker voltar, o que cobre o operador reiniciando a sessao.
                armado = False
                estado = "disparado, aguardando nova sessao"
            else:
                estado = f"telemetria de {idade:.0f} s atras"

            if time.time() - ultimo_aviso >= 60.0:
                print(f"[{agora()}] {'armado' if armado else 'em espera'}: {estado}")
                ultimo_aviso = time.time()
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print(f"\n[{agora()}] vigia encerrado. O mount NAO foi tocado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
