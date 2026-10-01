"""Varre o apontamento em torno da posicao atual, para medir a tolerancia do enlace.

Existe por causa da noite de 2026-10-01: o tracker reduziu o erro de apontamento
(+0,59" sem correcao, 10 pares de blocos), e a potencia acoplada no CBPF nao
mudou. Para saber quanto erro custa quanta potencia, em vez de esperar a
atmosfera produzir deriva, o mount e deslocado de proposito e o power meter do
CBPF grava ao mesmo tempo. Os dois arquivos se cruzam pelo horario (t_unix).

Cada ponto fora do centro e cercado por duas medidas no centro:

    C, +10, C, -10, C, +20, C, -20, ... (arcsec, num eixo de cada vez)

Assim a transmissao da atmosfera, que varia +-15 % em ondas de uma hora, sai
dividindo cada ponto pela media dos dois centros vizinhos.

Segunda pergunta que a varredura responde: mover o mount da UFF desloca o feixe
no CBPF SEM mudar o angulo de chegada la. Se a mancha da camera do CBPF andar
junto com a varredura, ela mede a posicao do feixe, e nao o angulo.

Seguranca: deslocamentos de no maximo LIMITE_ARCSEC, confirmacao digitada,
recusa se outro programa estiver comandando o mount, e volta a posicao inicial
no fim, no Ctrl+C e em qualquer erro. Primeira execucao no mount em 2026-10-01,
de dia, versao curta (+-10 e 20" em az): cada passo em 1 a 3 s, chegada a
+-1" (o passo da leitura de posicao) e retorno com erro de 0".

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto e o tracker parado:

    python programas_principais/varrer_apontamento.py --plano
    python programas_principais/varrer_apontamento.py
    python programas_principais/varrer_apontamento.py --pontos 10,20,40 --espera 60 --eixos alt
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.configuracoes import saidas  # noqa: E402

# Maior deslocamento aceito. 300" = 0,083 grau: dez vezes a maior excursao de
# refracao medida (25"), e ainda pequeno demais para qualquer cabo esticar.
LIMITE_ARCSEC = 300.0
PONTOS_PADRAO = (10.0, 20.0, 40.0, 60.0)
ESPERA_PADRAO_S = 45.0
ASSENTAR_S = 3.0
VELOCIDADE_PADRAO = 0.003   # deg/s, ~11"/s: 60" em ~6 s
# A leitura de posicao do AM5 vem em passos de 1" (0,000278 grau); uma tolerancia
# menor que meio passo nunca seria atingida com certeza.
TOLERANCIA_GRAUS = 0.0003
LEITURA_S = 0.5


def plano(pontos, eixos) -> list[tuple[str, float]]:
    """Sequencia de (eixo, deslocamento em arcsec), cada ponto entre dois centros."""
    seq: list[tuple[str, float]] = []
    for eixo in eixos:
        seq.append((eixo, 0.0))
        for p in pontos:
            for sinal in (+1.0, -1.0):
                seq.append((eixo, sinal * p))
                seq.append((eixo, 0.0))
    return seq


def validar(pontos, eixos) -> None:
    if not pontos:
        raise ValueError("Nenhum ponto de varredura.")
    for p in pontos:
        if not 0 < p <= LIMITE_ARCSEC:
            raise ValueError(f"Ponto {p}\" fora de (0, {LIMITE_ARCSEC:g}\"].")
    for e in eixos:
        if e not in ("az", "alt"):
            raise ValueError(f"Eixo desconhecido: {e}")


def duracao_estimada_s(seq, espera_s, velocidade) -> float:
    total, anterior = 0.0, 0.0
    for _, p in seq:
        total += abs(p - anterior) / 3600.0 / velocidade + ASSENTAR_S + espera_s
        anterior = p
    return total


def executar(seq, espera_s, velocidade, pasta, *, mount=None, relogio=time.time, dormir=time.sleep):
    """Percorre o plano gravando a posicao; volta ao inicio aconteca o que for.

    `mount` reune as funcoes do mount (injetaveis para teste): read_altaz,
    mover_para(az, alt), parar().
    """
    az0, alt0 = mount["read_altaz"]()
    arquivo = (pasta / "varredura.csv").open("w", newline="", encoding="utf-8")
    w = csv.writer(arquivo)
    w.writerow(["t_unix", "data_hora", "passo", "eixo", "ponto_arcsec", "fase",
                "az_deg", "alt_deg", "desloc_az_arcsec", "desloc_alt_arcsec"])

    def gravar(i, eixo, ponto, fase):
        az, alt = mount["read_altaz"]()
        daz = ((az - az0 + 180.0) % 360.0 - 180.0) * 3600.0
        w.writerow([f"{relogio():.3f}", datetime.now().isoformat(timespec="milliseconds"),
                    i, eixo, f"{ponto:g}", fase, f"{az:.6f}", f"{alt:.6f}",
                    f"{daz:.1f}", f"{(alt - alt0) * 3600.0:.1f}"])
        arquivo.flush()

    try:
        for i, (eixo, ponto) in enumerate(seq):
            alvo_az = (az0 + (ponto / 3600.0 if eixo == "az" else 0.0)) % 360.0
            alvo_alt = alt0 + (ponto / 3600.0 if eixo == "alt" else 0.0)
            print(f"[{i + 1}/{len(seq)}] {eixo} {ponto:+g}\"")
            gravar(i, eixo, ponto, "mover")
            mount["mover_para"](alvo_az, alvo_alt)
            mount["parar"]()
            fim = relogio() + ASSENTAR_S
            while relogio() < fim:
                gravar(i, eixo, ponto, "assentar")
                dormir(LEITURA_S)
            fim = relogio() + espera_s
            while relogio() < fim:
                gravar(i, eixo, ponto, "medir")
                dormir(LEITURA_S)
    finally:
        mount["parar"]()
        print("Voltando a posicao inicial...")
        try:
            mount["mover_para"](az0, alt0)
        finally:
            mount["parar"]()
            gravar(-1, "-", 0.0, "retorno")
            arquivo.close()
    az, alt = mount["read_altaz"]()
    return ((az - az0 + 180.0) % 360.0 - 180.0) * 3600.0, (alt - alt0) * 3600.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pontos", default=",".join(f"{p:g}" for p in PONTOS_PADRAO),
                        help="deslocamentos em arcsec, separados por virgula (cada um nos dois sentidos)")
    parser.add_argument("--eixos", default="az,alt", help="az, alt ou az,alt")
    parser.add_argument("--espera", type=float, default=ESPERA_PADRAO_S,
                        help="segundos parado em cada ponto, gravando")
    parser.add_argument("--velocidade", type=float, default=VELOCIDADE_PADRAO, help="deg/s")
    parser.add_argument("--plano", action="store_true", help="so imprime o plano e sai, sem mount")
    args = parser.parse_args()

    pontos = [float(x.replace(",", ".")) for x in args.pontos.split(",") if x.strip()]
    eixos = [e.strip() for e in args.eixos.split(",") if e.strip()]
    validar(pontos, eixos)
    seq = plano(pontos, eixos)
    print("Plano (eixo, arcsec):", " ".join(f"{e}{p:+g}" for e, p in seq))
    print(f"{len(seq)} paradas de {args.espera:g} s; "
          f"~{duracao_estimada_s(seq, args.espera, args.velocidade) / 60:.0f} min no total")
    if args.plano:
        return 0

    from modulos.controle.mount_ascom import (  # noqa: PLC0415
        ensure_connected,
        ensure_not_tracking,
        ensure_unparked,
        read_altaz,
        stop_axes_safely,
    )
    from modulos.controle.mount_em_uso import motivo_de_uso  # noqa: PLC0415
    from modulos.controle.mount_pid import move_axes_pid_2d  # noqa: PLC0415

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de varrer.")
        return 2
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az0, alt0 = read_altaz()
    print(f"Posicao inicial: az {az0:.6f}  alt {alt0:.6f}")
    print(f"Maior deslocamento: {max(pontos):g}\" (limite {LIMITE_ARCSEC:g}\")")
    print("\nESTE PROGRAMA MOVE O MOUNT.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    pasta = saidas.VARREDURA_APONTAMENTO_DIR / f"varredura_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "metadados.json").write_text(json.dumps({
        "inicio_local": datetime.now().isoformat(timespec="seconds"),
        "inicio_t_unix": time.time(), "az0_deg": az0, "alt0_deg": alt0,
        "pontos_arcsec": pontos, "eixos": eixos, "espera_s": args.espera,
        "velocidade_deg_s": args.velocidade, "tolerancia_deg": TOLERANCIA_GRAUS,
        "plano": seq,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Gravando em {pasta}")

    mount = {
        "read_altaz": read_altaz,
        "mover_para": lambda az, alt: move_axes_pid_2d(
            True, 0.0, 0.0, max_velocity_deg_s=args.velocidade,
            absolute_target=(az, alt), tolerance_deg=TOLERANCIA_GRAUS),
        "parar": lambda: stop_axes_safely(attempts=3, timeout=3.0),
    }
    try:
        erro_az, erro_alt = executar(seq, args.espera, args.velocidade, pasta, mount=mount)
    except KeyboardInterrupt:
        print("\nInterrompido pelo operador; o mount voltou ao inicio.")
        return 1
    print(f"\nFim. Erro do retorno: az {erro_az:+.1f}\"  alt {erro_alt:+.1f}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
