"""Move o mount por um delta em azimute e altitude, para trabalho de bancada.

Existe porque o ``ir_para_posicao.py`` e deliberadamente lento e cauteloso: ele
usa a mesma rotina que o tracker usa ao encerrar uma sessao, com 0,2 deg/s e
tolerancia de 0,0005 grau. Isso e certo no fim de uma sessao de 10 h e e um
estorvo na bancada, onde se quer reposicionar varias vezes seguidas.

Tres diferencas, e todas sao ajustaveis:

    velocidade   0,5 deg/s contra 0,2, entao um grau leva 2 s em vez de 5
    tolerancia   0,005 grau contra 0,0005, e a chegada deixa de arrastar
    percurso     fatia sozinho quando o delta passa do limite de seguranca

O fatiamento resolve um problema real, descoberto em 2026-09-16: a rotina de
retorno recusa qualquer deslocamento maior que MAX_OFFSET + 0,5 grau, entao
depois de uma excursao de 7,3 graus nao havia como voltar num comando so, e a
recuperacao virava trabalho manual em etapas.

Sem argumentos, pergunta os deltas. Com argumentos, nao pergunta nada alem da
confirmacao, o que permite usar em sequencia.

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto:

    python programas_principais/mover_mount.py
    python programas_principais/mover_mount.py --az -7.311
    python programas_principais/mover_mount.py --az 0.5 --alt -0.2 --sim
    python programas_principais/mover_mount.py --para-az 0 --para-alt 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.configuracoes.tracker import MAX_OFFSET_ALT_DEG, MAX_OFFSET_AZ_DEG
from modulos.controle.mount_ascom import (
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    mount_address,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_em_uso import motivo_de_uso
from modulos.controle.mount_pid import move_axes_pid_2d

VELOCIDADE_PADRAO = 0.5      # deg/s
TOLERANCIA_PADRAO = 0.005    # graus

# Maior pedaco que a rotina de PID aceita de uma vez. O limite de seguranca e
# MAX_OFFSET + 0,5; fico abaixo dele para o fatiamento nao encostar na borda.
PASSO_MAXIMO_AZ = MAX_OFFSET_AZ_DEG - 0.5
PASSO_MAXIMO_ALT = MAX_OFFSET_ALT_DEG - 0.5


def fatiar(delta: float, maximo: float) -> list[float]:
    """Divide um deslocamento em pedacos que caibam no limite, todos iguais."""
    if abs(delta) <= maximo:
        return [delta] if delta else []
    pedacos = int(abs(delta) // maximo) + 1
    return [delta / pedacos] * pedacos


def perguntar(rotulo: str) -> float:
    while True:
        texto = input(f"  delta em {rotulo} (graus, Enter para 0): ").strip()
        if not texto:
            return 0.0
        try:
            return float(texto.replace(",", "."))
        except ValueError:
            print("    numero invalido.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--az", type=float, default=None, help="delta em azimute, graus")
    parser.add_argument("--alt", type=float, default=None, help="delta em altitude, graus")
    parser.add_argument("--para-az", type=float, default=None,
                        help="azimute ALVO em vez de delta")
    parser.add_argument("--para-alt", type=float, default=None,
                        help="altitude ALVO em vez de delta")
    parser.add_argument("--velocidade", type=float, default=VELOCIDADE_PADRAO,
                        help=f"deg/s (padrao {VELOCIDADE_PADRAO})")
    parser.add_argument("--tolerancia", type=float, default=TOLERANCIA_PADRAO,
                        help=f"graus (padrao {TOLERANCIA_PADRAO})")
    parser.add_argument("--sim", action="store_true",
                        help="nao pergunta confirmacao; para uso em sequencia")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de mover.")
        return 2

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az_atual, alt_atual = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao atual: az {az_atual:+.5f}  alt {alt_atual:+.5f}")

    if args.para_az is not None or args.para_alt is not None:
        delta_az = (args.para_az - az_atual) if args.para_az is not None else 0.0
        delta_alt = (args.para_alt - alt_atual) if args.para_alt is not None else 0.0
    elif args.az is not None or args.alt is not None:
        delta_az = args.az or 0.0
        delta_alt = args.alt or 0.0
    else:
        print()
        delta_az = perguntar("AZIMUTE")
        delta_alt = perguntar("ALTITUDE")

    if not delta_az and not delta_alt:
        print("\nDelta zero; nada a fazer.")
        return 0

    passos_az = fatiar(delta_az, PASSO_MAXIMO_AZ)
    passos_alt = fatiar(delta_alt, PASSO_MAXIMO_ALT)
    n = max(len(passos_az), len(passos_alt), 1)
    passos_az += [0.0] * (n - len(passos_az))
    passos_alt += [0.0] * (n - len(passos_alt))

    print()
    print(f"  deslocamento : az {delta_az:+.5f}  alt {delta_alt:+.5f} graus")
    print(f"  destino      : az {az_atual + delta_az:+.5f}  "
          f"alt {alt_atual + delta_alt:+.5f}")
    print(f"  velocidade   : {args.velocidade} deg/s   "
          f"tolerancia {args.tolerancia} graus")
    maior = max(abs(delta_az), abs(delta_alt))
    print(f"  tempo estimado: ~{maior / args.velocidade:.0f} s")
    if n > 1:
        print(f"  EM {n} ETAPAS, porque o limite de seguranca por comando e "
              f"{PASSO_MAXIMO_AZ:g} grau em azimute")

    print()
    print("ESTE PROGRAMA MOVE O MOUNT.")
    if not args.sim and input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    try:
        for i, (passo_az, passo_alt) in enumerate(zip(passos_az, passos_alt, strict=True), 1):
            if n > 1:
                print(f"\n  etapa {i}/{n}: az {passo_az:+.5f}  alt {passo_alt:+.5f}")
            move_axes_pid_2d(
                True, passo_az, passo_alt,
                max_velocity_deg_s=args.velocidade,
                tolerance_deg=args.tolerancia,
            )
    except KeyboardInterrupt:
        print("\n  interrompido; parando os eixos.")
        stop_axes_safely(attempts=3, timeout=3.0)
        return 1
    finally:
        stop_axes_safely(attempts=3, timeout=3.0)

    az_final, alt_final = read_altaz()
    print()
    print(f"  posicao final: az {az_final:+.5f}  alt {alt_final:+.5f}")
    erro_az = (az_final - az_atual) - delta_az
    erro_alt = (alt_final - alt_atual) - delta_alt
    print(f"  erro do movimento: az {erro_az * 3600:+.1f}  "
          f"alt {erro_alt * 3600:+.1f} arcsec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
