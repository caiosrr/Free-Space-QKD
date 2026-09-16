"""Mede a velocidade que o mount ENTREGA para cada velocidade comandada.

O controle fino usa 0,001042 deg/s, um valor herdado como empirico. Ele e
suspeito de nao ser arbitrario: 0,001042 deg/s sao 3,751 arcsec/s, ou 0,2494
vezes a taxa sideral de 15,041 arcsec/s. E o driver INDI do AM5 mostra que as
classes de taxa do firmware vao "de 0,25x a 1440x". A hipotese, entao, e que
0,001042 seja a classe mais lenta do firmware, e nao um limite descoberto por
tentativa.

Isso importa, e nas duas direcoes possiveis:

    Se o mount GRAMPEIA qualquer pedido menor em 0,25x sideral, entao comandar
    0,0005 deg/s moveria o dobro do previsto. Todo calculo de duracao de pulso
    abaixo dessa taxa estaria errado por um fator, sem ninguem perceber, porque
    o controle compensa na proxima medida.

    Se o mount OBEDECE a taxas menores, entao o passo minimo do pulso pode
    encolher. Hoje ele e 0,216 px, e foi dele que nasceu a zona morta diagonal
    remendada em 2026-09-15. Com metade da taxa, metade do passo, e o remendo
    passaria a ser quase desnecessario.

Como mede: para cada taxa, comanda o eixo de azimute e AMOSTRA a posicao durante
o movimento, ajustando uma reta por minimos quadrados. Isso da mais precisao que
medir so as pontas, com o encoder quantizado em 1 arcsec, e ainda revela se o
movimento e continuo ou aos saltos.

Cada taxa e medida nos dois sentidos, para o deslocamento liquido ficar perto de
zero e a medida nao depender de deriva ou de folga mecanica.

Usa o caminho de producao, o MoveAxis do ASCOM, porque e esse que o tracker usa.
Testar outra camada responderia outra pergunta.

Uso, a partir da pasta Codigos, com o servidor ASCOM ABERTO:

    python programas_principais/medir_velocidade_minima.py
    python programas_principais/medir_velocidade_minima.py --segundos 30

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

import numpy as np

from modulos.controle.mount_ascom import (
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    mount_address,
    move_axis,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_em_uso import motivo_de_uso

SIDERAL_ARCSEC_S = 15.041
TAXA_DO_CONTROLE = 0.001042  # deg/s, a que o tracker usa hoje

# Abaixo, na, e acima da taxa do controle. As duas primeiras sao as que
# respondem a pergunta; as ultimas servem de controle positivo, para garantir
# que o metodo de medida funciona onde o mount certamente obedece.
TAXAS_PADRAO = [0.00025, 0.0005, 0.00075, 0.001042, 0.0015, 0.003]

AMOSTRAGEM_HZ = 5.0
ACOMODACAO_S = 1.5


def medir_um_sentido(taxa_deg_s: float, sinal: int, segundos: float) -> dict:
    """Comanda o eixo de azimute e ajusta a taxa real pela reta das amostras."""
    tempos: list[float] = []
    posicoes: list[float] = []
    move_axis(0, sinal * taxa_deg_s, True)
    inicio = time.perf_counter()
    try:
        while True:
            agora = time.perf_counter() - inicio
            if agora >= segundos:
                break
            az, _ = read_altaz()
            tempos.append(agora)
            posicoes.append(az * 3600.0)
            time.sleep(1.0 / AMOSTRAGEM_HZ)
    finally:
        stop_axes_safely(attempts=3, timeout=3.0)
    time.sleep(ACOMODACAO_S)

    if len(tempos) < 5:
        return {"taxa_arcsec_s": None, "amostras": len(tempos), "residuo": None}
    t = np.asarray(tempos)
    p = np.asarray(posicoes)
    coef, residuos, *_ = np.linalg.lstsq(np.vstack([t, np.ones_like(t)]).T, p, rcond=None)
    ajustada = coef[0] * t + coef[1]
    # Residuo em arcsec: separa movimento continuo de movimento aos saltos.
    return {
        "taxa_arcsec_s": float(coef[0]) * sinal,
        "amostras": len(t),
        "residuo": float(np.sqrt(np.mean((p - ajustada) ** 2))),
        "percurso": float(p[-1] - p[0]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--segundos", type=float, default=20.0,
                        help="duracao de cada medida, em cada sentido")
    parser.add_argument("--taxas", type=float, nargs="+", default=TAXAS_PADRAO,
                        help="taxas a comandar, em deg/s")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de medir.")
        return 2

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az_inicial, alt_inicial = read_altaz()

    print(f"Mount em {mount_address()}")
    print(f"  posicao inicial: az {az_inicial:.5f}  alt {alt_inicial:.5f}")
    print(f"  {len(args.taxas)} taxas, {args.segundos:.0f} s em cada sentido")
    percurso = sum(t * args.segundos for t in args.taxas) * 3600.0
    print(f"  percurso total por sentido: ~{percurso:.0f} arcsec, e o liquido")
    print("  deve ficar perto de zero porque cada taxa vai e volta")
    print()
    print("ESTE PROGRAMA MOVE O EIXO DE AZIMUTE.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    print()
    print(f"{'comandada':>12s} {'entregue':>12s} {'razao':>8s} "
          f"{'x sideral':>10s} {'residuo':>9s} {'amostras':>9s}")
    print(f"{'(arcsec/s)':>12s} {'(arcsec/s)':>12s} {'':>8s} {'':>10s} {'(arcsec)':>9s}")
    linhas = []
    for taxa in sorted(args.taxas):
        medidas = []
        for sinal in (+1, -1):
            resultado = medir_um_sentido(taxa, sinal, args.segundos)
            if resultado["taxa_arcsec_s"] is not None:
                medidas.append(resultado)
        if not medidas:
            print(f"{taxa * 3600.0:12.3f}   sem amostras suficientes")
            continue
        entregue = float(np.mean([abs(m["taxa_arcsec_s"]) for m in medidas]))
        residuo = float(np.mean([m["residuo"] for m in medidas]))
        comandada = taxa * 3600.0
        linhas.append((comandada, entregue))
        print(f"{comandada:12.3f} {entregue:12.3f} {entregue / comandada:8.2f} "
              f"{entregue / SIDERAL_ARCSEC_S:10.3f} {residuo:9.2f} "
              f"{sum(m['amostras'] for m in medidas):9d}")

    az_final, _ = read_altaz()
    print()
    print(f"  azimute final {az_final:.5f}, liquido "
          f"{(az_final - az_inicial) * 3600.0:+.1f} arcsec")

    print()
    print("Como ler:")
    print("  razao ~1 em todas as taxas")
    print("    o mount obedece abaixo de 0,001042 deg/s, e o passo minimo do")
    print("    pulso pode encolher; a zona morta diagonal diminui junto")
    print("  razao >1 nas taxas baixas, com 'entregue' igual em todas elas")
    print("    o firmware grampeia no piso, e comandar menos nao adianta;")
    print(f"    confira se esse piso e 0,25x sideral, ou {0.25 * SIDERAL_ARCSEC_S:.3f} arcsec/s")
    print("  entregue zero nas taxas baixas")
    print("    abaixo do piso o mount simplesmente nao anda, e o valor atual")
    print("    ja e o minimo utilizavel")
    print()
    print("  residuo muito maior que 1 arcsec indica movimento aos saltos, e nao")
    print("  continuo: nesse caso a taxa media engana e o pulso curto nao entrega")
    print("  o que a conta preve.")
    print()
    print(f"  Para voltar: python programas_principais/ir_para_posicao.py --az {az_inicial:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
