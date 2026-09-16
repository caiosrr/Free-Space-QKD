"""Desconectar pela API do ASCOM para o mount? Mede, com o eixo em movimento.

Ja testamos duas formas de perder a conexao e nenhuma parou o eixo: fechar a
janela do servidor ASCOM (266 arcsec quando 246 eram previstos) e arrancar o
cabo USB (7,3 graus, e seguiu ate mandarmos parar).

Falta uma terceira, que e um caminho de codigo DIFERENTE: mandar
``Connected = False`` pela propria API. Alguns drivers param o movimento de
proposito ao desconectar, porque sabem que ninguem mais vai mandar o zero.
Fechar a janela do servidor nao exercita esse caminho: o processo morre, e
depende de o driver ter um encerramento ordenado.

Como mede: poe o azimute a andar, desconecta pela API, espera, reconecta e le a
posicao. A diferenca entre o percurso previsto e o medido responde.

    parou na desconexao   ->  percurso ~= taxa x tempo ATE a desconexao
    ignorou a desconexao  ->  percurso ~= taxa x tempo TOTAL

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto:

    python programas_principais/testar_desconexao_ascom.py
    python programas_principais/testar_desconexao_ascom.py --espera 20

Pode apagar este arquivo depois que o resultado estiver no roteiro.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import (
    call,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    mount_address,
    move_axis,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_em_uso import motivo_de_uso

VELOCIDADE_PADRAO = 0.05   # deg/s, 180 arcsec/s


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--velocidade", type=float, default=VELOCIDADE_PADRAO)
    parser.add_argument("--antes", type=float, default=5.0,
                        help="segundos de movimento antes de desconectar")
    parser.add_argument("--espera", type=float, default=15.0,
                        help="segundos desconectado")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de testar.")
        return 2

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az0, alt0 = read_altaz()
    total = args.antes + args.espera
    print(f"Mount em {mount_address()}")
    print(f"  azimute inicial: {az0:.5f} deg")
    print(f"  {args.antes:.0f} s movendo, desconecta, {args.espera:.0f} s, reconecta")
    print(f"  percurso se IGNORAR a desconexao: {args.velocidade*total*3600:.0f} arcsec")
    print(f"  percurso se PARAR na desconexao:  {args.velocidade*args.antes*3600:.0f} arcsec")
    print()
    print("ESTE PROGRAMA MOVE O EIXO DE AZIMUTE.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    desconectou = False
    try:
        move_axis(0, args.velocidade, True)
        print(f"\n  eixo em movimento a {args.velocidade*3600:.0f} arcsec/s")
        time.sleep(args.antes)
        _, _ = read_altaz()

        call("PUT", "connected", data={"Connected": False}, timeout=5.0)
        desconectou = True
        print(f"  Connected = False enviado; esperando {args.espera:.0f} s")
        time.sleep(args.espera)

        call("PUT", "connected", data={"Connected": True}, timeout=5.0)
        desconectou = False
        time.sleep(1.0)
        az1, _ = read_altaz()
    finally:
        if desconectou:
            # Nao deixar o mount desconectado com um comando possivelmente ativo.
            try:
                call("PUT", "connected", data={"Connected": True}, timeout=5.0)
            except Exception as exc:
                print(f"  FALHA ao reconectar: {type(exc).__name__}: {exc}")
        stop_axes_safely(attempts=3, timeout=3.0)

    time.sleep(1.5)
    az2, _ = read_altaz()
    # azimute e modular: o percurso curto nunca deve cruzar 360, mas se cruzar,
    # a diferenca crua daria 360 graus de erro.
    delta = (az2 - az0 + 180.0) % 360.0 - 180.0
    percorrido = abs(delta) * 3600.0
    esperado_ignorando = args.velocidade * total * 3600.0
    esperado_parando = args.velocidade * args.antes * 3600.0

    print()
    print(f"  azimute final: {az2:.5f} deg")
    print(f"  percorrido: {percorrido:.0f} arcsec")
    print(f"    se tivesse ignorado a desconexao: {esperado_ignorando:.0f}")
    print(f"    se tivesse parado na desconexao:  {esperado_parando:.0f}")
    print()
    meio = (esperado_ignorando + esperado_parando) / 2
    if percorrido < meio:
        print("  O DRIVER PAROU O EIXO AO DESCONECTAR.")
        print("  Isso seria uma protecao real: basta o programa desconectar ao morrer.")
    else:
        print("  O driver NAO parou: desconectar pela API nao interrompe o movimento.")
        print("  Terceira forma de perder a conexao testada, terceiro resultado igual.")
    print()
    print(f"  Para voltar: python programas_principais/mover_mount.py --para-az {az0:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
