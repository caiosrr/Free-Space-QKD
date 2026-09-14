"""O mount para quando o servidor ASCOM cai no meio de um MoveAxis?

Esta e a pergunta que mais importa para o risco de deriva. Em 2026-09-10 uma
sessao foi morta as 03:44 sem chance de encerrar, por reinicio do Windows ou
queda de energia. Se isso pegar um pulso em curso, o ``MoveAxis`` fica sem o
zero de encerramento e o eixo anda sozinho.

O teste anterior (``testar_timeout_moveaxis.py``) verifica se o firmware tem
prazo proprio, sem ninguem derrubar nada. Este vai alem: derruba de fato o
servidor ASCOM enquanto o eixo esta andando, que e o que acontece quando o PC
reinicia.

COMO FUNCIONA, com voce no comando de cada passo:

  1. o programa le a posicao e manda MoveAxis na velocidade minima
  2. VOCE fecha o servidor ASCOM (Exit)
  3. o programa cronometra a janela combinada
  4. VOCE reabre o servidor
  5. o programa le a posicao de novo e compara com o previsto

Se o mount andou o previsto para a janela inteira, ele continuou se movendo
sem o servidor: nao ha protecao nenhuma. Se andou so o do comeco, ele parou
quando a conexao caiu, e o risco praticamente desaparece.

SEGURANCA. Velocidade minima, 3,75 arcsec/s. Na janela padrao de 60 s isso da
menos de 4 arcmin. No pior caso imaginavel -- o mount nao para e voce demora
uma hora para reabrir o servidor -- seriam 3,75 graus, abaixo do limite de 5
graus do watchdog do tracker. Ao fim o programa sempre manda zero, e
``parar_mount.py`` continua disponivel.

Uso, a partir da pasta Codigos:

    python programas_principais/testar_homem_morto.py
    python programas_principais/testar_homem_morto.py --janela 90
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
    VEL_MIN_LIMITE,
    mount_address,
    move_axis,
    read_altaz,
    stop_axes_safely,
)


def posicao_com_paciencia(tentativas: int = 30, espera_s: float = 2.0):
    """Le a posicao insistindo, para o servidor ter tempo de voltar."""
    for tentativa in range(tentativas):
        try:
            return read_altaz()
        except Exception:
            if tentativa == 0:
                print("  aguardando o servidor responder", end="", flush=True)
            else:
                print(".", end="", flush=True)
            time.sleep(espera_s)
    raise RuntimeError("o servidor ASCOM nao voltou a responder")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eixo", type=int, default=0, choices=(0, 1))
    parser.add_argument("--janela", type=float, default=60.0,
                        help="segundos com o servidor fechado (padrao 60)")
    args = parser.parse_args()
    janela = max(20.0, min(args.janela, 180.0))
    previsto_arcsec = VEL_MIN_LIMITE * janela * 3600.0

    print(f"Mount em {mount_address()}")
    print(f"  eixo                 : {args.eixo} ({'azimute' if args.eixo == 0 else 'altitude'})")
    print(f"  velocidade           : {VEL_MIN_LIMITE * 3600:.2f} arcsec/s")
    print(f"  janela sem servidor  : {janela:.0f} s")
    print(f"  se NAO parar, andara : {previsto_arcsec:.0f} arcsec nessa janela")
    print("\nESTE PROGRAMA MOVE O MOUNT e pede que voce derrube o servidor ASCOM.")
    print("Tenha a janela do servidor a vista antes de comecar.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado.")
        return 1

    az0, alt0 = read_altaz()
    print(f"\nposicao inicial: az {az0:.5f}  alt {alt0:.5f}")

    parou_sozinho = None
    try:
        move_axis(args.eixo, VEL_MIN_LIMITE, True)
        t_comando = time.perf_counter()
        print("MoveAxis enviado, o eixo esta andando.\n")
        print(">>> FECHE O SERVIDOR ASCOM AGORA (Exit) e pressione ENTER <<<")
        input()
        t_fechou = time.perf_counter()
        andou_antes = (t_fechou - t_comando) * VEL_MIN_LIMITE * 3600.0
        print(f"servidor fechado {t_fechou - t_comando:.1f} s apos o comando "
              f"({andou_antes:.1f} arcsec ja percorridos)")

        print(f"\naguardando {janela:.0f} s com o servidor fora do ar...")
        time.sleep(janela)

        print("\n>>> REABRA O SERVIDOR ASCOM e pressione ENTER <<<")
        input()
        az1, alt1 = posicao_com_paciencia()
        print()
        total = ((az1 - az0) if args.eixo == 0 else (alt1 - alt0)) * 3600.0
        print(f"posicao apos a janela: az {az1:.5f}  alt {alt1:.5f}")
        print(f"\n  andou no total            : {abs(total):.1f} arcsec")
        print(f"  andaria antes de fechar    : {andou_antes:.1f} arcsec")
        print(f"  andaria se NAO tivesse parado: {andou_antes + previsto_arcsec:.1f} arcsec")
        # Margem generosa: o encoder e quantizado e o tempo de reabertura conta.
        parou_sozinho = abs(total) < andou_antes + 0.35 * previsto_arcsec
    finally:
        print("\nzerando os eixos...")
        try:
            move_axis(args.eixo, 0.0, True)
            stop_axes_safely(attempts=3, timeout=3.0)
            print("  eixos zerados.")
        except Exception as exc:
            print(f"  ATENCAO: nao consegui zerar ({exc}).")
            print("  Rode programas_principais/parar_mount.py assim que possivel.")

    if parou_sozinho is True:
        print("\nO MOUNT PAROU quando o servidor caiu.")
        print("O risco de deriva por morte subita do PC e muito menor do que")
        print("supunhamos, e a tarefa de parada no boot vira redundancia.")
    elif parou_sozinho is False:
        print("\nO MOUNT CONTINUOU ANDANDO sem o servidor.")
        print("Confirma o risco: um PC que reinicia no meio de um pulso deixa o")
        print("eixo derivando. A tarefa de parada no boot e necessaria.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
