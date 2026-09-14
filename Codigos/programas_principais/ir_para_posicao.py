"""Leva o mount de volta a uma posicao absoluta, com verificacao.

Existe porque os testes de seguranca deslocam o mount de proposito e nao o
devolvem: ``testar_timeout_moveaxis.py`` e ``testar_homem_morto.py`` param o
eixo, mas param onde ele chegou. Voltar a mao e trabalhoso e o AM5 nao aceita
``SlewToAltAz`` (o driver reporta ``CanSlewAltAz = False``).

Usa a mesma rotina de retorno que o tracker e a calibracao usam ao encerrar:
pulsos com realimentacao pelo encoder, repetidos ate ficar dentro da
tolerancia, respeitando os limites de deslocamento configurados.

Sem argumentos de posicao, so informa onde o mount esta.

Uso, a partir da pasta Codigos:

    python programas_principais/ir_para_posicao.py
    python programas_principais/ir_para_posicao.py --az 0.0203 --alt 0.0036
    python programas_principais/ir_para_posicao.py --recuar-az 227
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import ensure_connected, mount_address, read_altaz
from modulos.controle.tracker_seguranca import retornar_posicao_inicial


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--az", type=float, default=None, help="azimute alvo, em graus")
    parser.add_argument("--alt", type=float, default=None, help="altitude alvo, em graus")
    parser.add_argument(
        "--recuar-az", type=float, default=None,
        help="desfaz um deslocamento em azimute, em ARCSEC (use o valor que o teste relatou)",
    )
    parser.add_argument(
        "--recuar-alt", type=float, default=None,
        help="desfaz um deslocamento em altitude, em ARCSEC",
    )
    args = parser.parse_args()

    ensure_connected()
    az_atual, alt_atual = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao atual: az {az_atual:.5f}  alt {alt_atual:.5f}")

    if args.recuar_az is not None or args.recuar_alt is not None:
        alvo_az = az_atual - (args.recuar_az or 0.0) / 3600.0
        alvo_alt = alt_atual - (args.recuar_alt or 0.0) / 3600.0
    elif args.az is not None or args.alt is not None:
        alvo_az = args.az if args.az is not None else az_atual
        alvo_alt = args.alt if args.alt is not None else alt_atual
    else:
        print("\nNenhum alvo informado; nada foi movido.")
        return 0

    print(f"  posicao alvo : az {alvo_az:.5f}  alt {alvo_alt:.5f}")
    d_az = (alvo_az - az_atual) * 3600.0
    d_alt = (alvo_alt - alt_atual) * 3600.0
    print(f"  deslocamento : az {d_az:+.1f}″  alt {d_alt:+.1f}″")
    print("\nESTE PROGRAMA MOVE O MOUNT.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado.")
        return 1

    resultado = retornar_posicao_inicial(alvo_az, alvo_alt)
    print()
    for chave in ("success", "attempts", "final_azimuth_deg", "final_altitude_deg", "error"):
        if chave in resultado:
            print(f"  {chave:20s} = {resultado[chave]}")
    if resultado.get("success"):
        print("\nPosicao restaurada dentro da tolerancia.")
        return 0
    print("\nNAO chegou a tolerancia. Rode de novo ou confira o mount.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
