"""Poe o eixo de azimute a andar, para o teste da parada pela serial.

Existe uma pergunta que nem o reinicio nem o simulador respondem: a parada por
LX200 consegue deter um eixo que esta EM MOVIMENTO, com o servidor ASCOM fora do
ar? A sequencia do teste e comandar o movimento, fechar o servidor, e entao
rodar parar_mount_direto.py, que confirma pela leitura de posicao.

Este programa faz so a primeira parte: comanda o movimento e sai, deixando o
eixo andando de proposito. Ele imprime a posicao inicial, que e o numero
necessario para voltar depois.

SEGURANCA. A velocidade e a dos micropulsos, 0,001042 deg/s, ou 3,75 arcsec/s.
Mesmo meia hora a deriva sao menos de 2 graus, e o azimute gira livre. Se a
parada direta falhar, o plano B e reabrir o servidor ASCOM e rodar
parar_mount.py, que e a mesma parada que encerra qualquer sessao.

Uso, a partir da pasta Codigos:

    python programas_principais/testar_parada_direta.py

Pode apagar este arquivo depois que o resultado estiver anotado.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import (
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    mount_address,
    move_axis,
    read_altaz,
)
from modulos.controle.mount_em_uso import motivo_de_uso

VELOCIDADE_PADRAO = 0.001042  # deg/s, a mesma dos micropulsos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--velocidade", type=float, default=VELOCIDADE_PADRAO,
                        help="deg/s no azimute (padrao: a dos micropulsos)")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de testar.")
        return 2

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az, alt = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao inicial: az {az:.5f}  alt {alt:.5f}")
    print(f"  velocidade: {args.velocidade} deg/s = {args.velocidade * 3600:.2f} arcsec/s")
    print()
    print("ESTE PROGRAMA DEIXA O EIXO DE AZIMUTE ANDANDO.")
    print("Anote o az acima: e com ele que voce volta no fim.")
    if input("Digite SIM para comandar o movimento: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    move_axis(0, args.velocidade, True)
    print()
    print(f"EIXO EM MOVIMENTO a {args.velocidade * 3600:.2f} arcsec/s.")
    print()
    print("Agora, na ordem:")
    print("  1. feche o servidor ASCOM (Exit)")
    print("  2. espere uns 10 s")
    print("  3. rode:  python programas_principais/parar_mount_direto.py")
    print()
    print("Se ele disser 'parado e confirmado', a parada pela serial funciona")
    print("com o eixo em movimento. Se disser 'AINDA EM MOVIMENTO', reabra o")
    print("servidor ASCOM e rode parar_mount.py.")
    print()
    print(f"Para voltar depois:  python programas_principais/ir_para_posicao.py --az {az:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
