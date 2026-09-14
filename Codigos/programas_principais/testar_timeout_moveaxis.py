"""O firmware para o MoveAxis sozinho, sem ninguem mandar zero?

O ``MoveAxis`` do ASCOM nao tem prazo pela especificacao: o eixo anda ate
alguem mandar zero. Se o firmware do mount tiver um timeout proprio, o risco de
um PC morto deixar o eixo derivando cai muito. Vale saber qual e o caso.

COMO FUNCIONA: comanda a velocidade MINIMA num eixo, nao manda mais nada, e
acompanha a posicao pelo encoder. Se ela parar de mudar antes do fim da janela,
o firmware tem timeout. Se continuar mudando ate o fim, nao tem.

SEGURANCA. Este programa move o mount e e feito para rodar REMOTO:

- usa a velocidade minima, 0,001042 deg/s, ou seja 3,75 arcsec/s;
- em 60 s de observacao isso da 0,0625 grau, menos de 4 arcmin;
- uma thread de guarda manda zero ao fim da janela FACA O QUE FIZER;
- o ``finally`` manda zero de novo;
- Ctrl+C tambem cai no ``finally``.

Mesmo um travamento completo deste programa deixaria o mount a 3,75 arcsec/s, e
o watchdog do tracker so consideraria problema a partir de 5 graus, o que
levaria 80 minutos. Ainda assim, confira a posicao no fim e rode
``parar_mount.py`` se algo parecer errado.

O que este teste NAO faz: simular o PC morrendo. Matar este processo nao
desconecta o driver do mount, porque quem segura a conexao e o servidor ASCOM
Remote, que continua no ar. Para o mount, o cliente nao sumiu.

Uso, a partir da pasta Codigos:

    python programas_principais/testar_timeout_moveaxis.py
    python programas_principais/testar_timeout_moveaxis.py --eixo 1 --janela 90
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import (
    VEL_MIN_LIMITE,
    mount_address,
    move_axis,
    read_altaz,
    stop_axes_safely,
)

ESCALA_PX_POR_GRAU = 9450.0


def guarda(prazo_s: float, eixo: int, parado: threading.Event) -> None:
    """Manda zero ao fim do prazo, aconteca o que acontecer no laco principal."""
    if parado.wait(prazo_s):
        return
    try:
        move_axis(eixo, 0.0, True)
        print("\n[guarda] velocidade zero enviada pelo prazo maximo.")
    except Exception as exc:
        print(f"\n[guarda] FALHOU ao zerar: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eixo", type=int, default=0, choices=(0, 1),
                        help="0 = azimute, 1 = altitude")
    parser.add_argument("--janela", type=float, default=60.0,
                        help="segundos de observacao (padrao 60)")
    args = parser.parse_args()

    janela = max(10.0, min(args.janela, 180.0))
    deslocamento_previsto = VEL_MIN_LIMITE * janela

    print(f"Mount em {mount_address()}")
    print(f"  eixo                : {args.eixo} ({'azimute' if args.eixo == 0 else 'altitude'})")
    print(f"  velocidade           : {VEL_MIN_LIMITE} deg/s ({VEL_MIN_LIMITE * 3600:.2f} arcsec/s)")
    print(f"  janela de observacao : {janela:.0f} s")
    print(f"  deslocamento previsto: {deslocamento_previsto:.4f} deg "
          f"({deslocamento_previsto * 3600:.1f} arcsec) se NAO houver timeout")
    print("\nESTE PROGRAMA MOVE O MOUNT. Uma thread de guarda manda zero ao fim.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado.")
        return 1

    az0, alt0 = read_altaz()
    print(f"\nposicao inicial: az {az0:.5f}  alt {alt0:.5f}")

    parado = threading.Event()
    vigia = threading.Thread(
        target=guarda, args=(janela + 5.0, args.eixo, parado), daemon=True
    )
    vigia.start()

    amostras = []
    try:
        move_axis(args.eixo, VEL_MIN_LIMITE, True)
        print("MoveAxis enviado. A partir daqui NADA mais e comandado.\n")
        print(f"{'t (s)':>7s} {'az':>11s} {'alt':>11s} {'andou (arcsec)':>16s}")
        t0 = time.perf_counter()
        while True:
            t = time.perf_counter() - t0
            if t > janela:
                break
            az, alt = read_altaz()
            andou = ((az - az0) if args.eixo == 0 else (alt - alt0)) * 3600.0
            amostras.append((t, andou))
            print(f"{t:7.1f} {az:11.5f} {alt:11.5f} {andou:16.1f}")
            time.sleep(3.0)
    finally:
        parado.set()
        move_axis(args.eixo, 0.0, True)
        stop_axes_safely(attempts=3, timeout=3.0)
        time.sleep(1.5)
        az1, alt1 = read_altaz()
        total = ((az1 - az0) if args.eixo == 0 else (alt1 - alt0)) * 3600.0
        print(f"\nposicao final: az {az1:.5f}  alt {alt1:.5f}")
        print(f"deslocamento total: {total:.1f} arcsec "
              f"({abs(total) / 3600 * ESCALA_PX_POR_GRAU:.0f} px)")

    if len(amostras) < 4:
        print("\nAmostras insuficientes para concluir.")
        return 1

    # Parou sozinho? Compara o quanto andou na ultima metade com o esperado.
    metade = amostras[len(amostras) // 2:]
    dt = metade[-1][0] - metade[0][0]
    d_andou = metade[-1][1] - metade[0][1]
    esperado = VEL_MIN_LIMITE * 3600.0 * dt
    print(f"\nna segunda metade da janela ({dt:.0f} s): andou {d_andou:.1f} arcsec, "
          f"esperado {esperado:.1f} arcsec sem timeout")
    if esperado > 0 and abs(d_andou) < 0.3 * esperado:
        print("\nO FIRMWARE PAROU SOZINHO. Ha timeout interno no MoveAxis,")
        print("e o risco de um PC morto deixar o eixo derivando e limitado.")
    else:
        print("\nO firmware NAO parou: andou o esperado ate o fim da janela.")
        print("O MoveAxis nao tem prazo neste mount, como a especificacao permite.")
        print("A protecao continua sendo a tarefa de parada no boot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
