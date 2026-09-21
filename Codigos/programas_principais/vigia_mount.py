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

COMO ELE PARA: pela escada de ``parada_emergencia``, nao por uma tentativa so.
Ate 2026-09-21 ele chamava apenas o Alpaca, e isso deixava um furo grande: se o
SERVIDOR ASCOM tivesse caido junto com o tracker, o vigia nao alcancava o mount
por nada, embora a porta serial estivesse livre justamente por isso. A escada
tenta o Alpaca, depois a serial, e se preciso encerra o servidor para soltar a
porta e repete a serial.

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

from modulos.controle.mount_ascom import mount_address
from modulos.controle.parada_emergencia import escalar_parada

SESSOES = CODIGOS_DIR / "Link UFF" / "resultados" / "tracker" / "sessoes"
# Mesmo diario das paradas de emergencia: um disparo deste vigia e do mesmo
# tipo de evento, e concentrar os dois num arquivo so evita ter de lembrar de
# dois lugares quando algo der errado de madrugada.
DIARIO = CODIGOS_DIR / "resultados" / "parar_mount.txt"


def anotar(texto: str) -> None:
    """Deixa rastro em disco.

    O vigia so imprimia na tela, e uma janela fechada ou rolada levava junto a
    unica evidencia de que ele agiu.
    """
    try:
        DIARIO.parent.mkdir(parents=True, exist_ok=True)
        with DIARIO.open("a", encoding="utf-8") as arquivo:
            momento = datetime.now().astimezone().isoformat(timespec="seconds")
            arquivo.write(f"{momento}  [vigia] {texto}\n")
    except Exception:
        # Parar o mount importa mais do que anotar que parou.
        pass


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
    parser.add_argument("--porta-serial", default=None,
                        help="porta do degrau serial; sem isto ele descobre")
    parser.add_argument("--sem-encerrar-servidor", dest="encerrar",
                        action="store_false",
                        help="nao derruba o servidor ASCOM no ultimo degrau")
    parser.set_defaults(encerrar=True)
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
                    anotar("armado: tracker gravando")
                armado = True
                estado = f"gravando (telemetria de {idade:.0f} s atras)"
            elif armado and idade > args.disparar_em:
                print(f"\n[{agora()}] TRACKER PAROU de gravar ha {idade:.0f} s.")
                print(f"[{agora()}] subindo a escada de parada:")
                ok, degraus = escalar_parada(
                    porta_serial=args.porta_serial,
                    permitir_encerrar_servidor=args.encerrar,
                )
                print(
                    f"[{agora()}] "
                    + ("MOUNT PARADO." if ok else "FALHA: verifique o mount.")
                )
                anotar(
                    f"DISPAROU apos {idade:.0f} s sem telemetria: "
                    + ("parado" if ok else "FALHA ao parar, verifique o mount")
                )
                # Cada degrau no diario: de manha, saber ONDE a escada
                # resolveu diz qual protecao esta carregando o peso.
                for degrau in degraus:
                    anotar(f"  {degrau}")
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
