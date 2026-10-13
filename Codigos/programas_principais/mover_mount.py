"""Move o mount por um delta ou ate uma posicao absoluta, para trabalho de bancada.

Dois modos. Ate 2026-10-10 eram dois programas; o ``ir_para_posicao.py`` entrou
aqui como o modo ``--para``, sem mudar a rotina que ele usa.

``--delta AZ ALT``, em graus, e o modo rapido. Existe porque o modo ``--para``
e deliberadamente lento e cauteloso: ele usa a mesma rotina que o tracker usa
ao encerrar uma sessao, com 0,2 deg/s e tolerancia de 0,0005 grau. Isso e certo
no fim de uma sessao de 10 h e e um estorvo na bancada, onde se quer
reposicionar varias vezes seguidas. Tres diferencas, e todas sao ajustaveis:

    velocidade   0,5 deg/s contra 0,2, entao um grau leva 2 s em vez de 5
    tolerancia   0,005 grau contra 0,0005, e a chegada deixa de arrastar
    percurso     fatia sozinho quando o delta passa do limite de seguranca

O fatiamento resolve um problema real, descoberto em 2026-09-16: a rotina de
retorno recusa qualquer deslocamento maior que MAX_OFFSET + 0,5 grau, entao
depois de uma excursao de 7,3 graus nao havia como voltar num comando so, e a
recuperacao virava trabalho manual em etapas.

``--para AZ ALT``, em graus, leva a uma posicao absoluta com verificacao. O AM5
nao aceita ``SlewToAltAz`` (o driver reporta ``CanSlewAltAz = False``), entao
voltar a mao e trabalhoso. Usa a rotina de retorno do tracker e da calibracao:
pulsos com realimentacao pelo encoder, repetidos ate ficar dentro da
tolerancia, respeitando os limites de deslocamento configurados. Termina com
codigo 1 se nao chegou a tolerancia. Neste modo a confirmacao e sempre pedida:
``--sim``, ``--velocidade`` e ``--tolerancia`` sao recusados.

Os dois modos recusam mover enquanto ``motivo_de_uso()`` acusar outro programa
usando o mount. Sem argumentos, pergunta os deltas. Com ``--delta``, nao
pergunta nada alem da confirmacao, o que permite usar em sequencia.

``--delta ... --sim`` pula a confirmacao digitada. E uma excecao DELIBERADA a
regra do CLAUDE.md ("pede confirmacao digitada antes do primeiro movimento"),
decidida pelo Caio em 2026-10-10 para reposicionar varias vezes seguidas na
bancada. Nao remover sem perguntar. Sem ``--sim``, a confirmacao e pedida.

**Em hardware:** o ``--para`` rodou em 2026-10-13 na UFF, de volta a direcao do
CBPF depois de ~25" de afastamento, e parou a 1" do alvo nos dois eixos (lido
no app do mount). O ``--delta`` da forma fundida ainda nunca rodou.

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto:

    python programas_principais/mover_mount.py
    python programas_principais/mover_mount.py --delta -7.311 0
    python programas_principais/mover_mount.py --delta 0.5 -0.2 --sim
    python programas_principais/mover_mount.py --para 0.0203 0.0036
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
from modulos.controle.tracker_seguranca import retornar_posicao_inicial

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


def ir_para(alvo_az: float, alvo_alt: float) -> int:
    """Modo --para: posicao absoluta pela rotina de retorno do tracker."""
    ensure_connected()
    az_atual, alt_atual = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao atual: az {az_atual:.5f}  alt {alt_atual:.5f}")

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


def mover_delta(delta: list[float] | None, velocidade: float, tolerancia: float,
                sim: bool) -> int:
    """Modo --delta: deslocamento rapido, fatiado no limite de seguranca."""
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az_atual, alt_atual = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao atual: az {az_atual:+.5f}  alt {alt_atual:+.5f}")

    if delta is not None:
        delta_az, delta_alt = delta
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
    print(f"  velocidade   : {velocidade} deg/s   "
          f"tolerancia {tolerancia} graus")
    maior = max(abs(delta_az), abs(delta_alt))
    print(f"  tempo estimado: ~{maior / velocidade:.0f} s")
    if n > 1:
        print(f"  EM {n} ETAPAS, porque o limite de seguranca por comando e "
              f"{PASSO_MAXIMO_AZ:g} grau em azimute")

    print()
    print("ESTE PROGRAMA MOVE O MOUNT.")
    if not sim and input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    try:
        for i, (passo_az, passo_alt) in enumerate(zip(passos_az, passos_alt, strict=True), 1):
            if n > 1:
                print(f"\n  etapa {i}/{n}: az {passo_az:+.5f}  alt {passo_alt:+.5f}")
            move_axes_pid_2d(
                True, passo_az, passo_alt,
                max_velocity_deg_s=velocidade,
                tolerance_deg=tolerancia,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--delta", type=float, nargs=2, metavar=("AZ", "ALT"),
                      help="desloca por AZ e ALT, em graus")
    modo.add_argument("--para", type=float, nargs=2, metavar=("AZ", "ALT"),
                      help="leva a posicao absoluta AZ e ALT, em graus, pela rotina de retorno")
    parser.add_argument("--velocidade", type=float, default=None,
                        help=f"deg/s, so com --delta (padrao {VELOCIDADE_PADRAO})")
    parser.add_argument("--tolerancia", type=float, default=None,
                        help=f"graus, so com --delta (padrao {TOLERANCIA_PADRAO})")
    parser.add_argument("--sim", action="store_true",
                        help="nao pergunta confirmacao; so com --delta, para uso em sequencia")
    args = parser.parse_args()

    if args.para is not None and (
        args.sim or args.velocidade is not None or args.tolerancia is not None
    ):
        parser.error("--sim, --velocidade e --tolerancia valem so com --delta; "
                     "--para usa a rotina de retorno e sempre pede confirmacao")

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de mover.")
        return 2

    if args.para is not None:
        return ir_para(*args.para)
    return mover_delta(
        args.delta,
        VELOCIDADE_PADRAO if args.velocidade is None else args.velocidade,
        TOLERANCIA_PADRAO if args.tolerancia is None else args.tolerancia,
        args.sim,
    )


if __name__ == "__main__":
    raise SystemExit(main())
