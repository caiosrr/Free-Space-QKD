"""Testa se o PulseGuide move o mount, e quanto. SUPERVISIONADO.

Por que interessa: o ``MoveAxis`` do ASCOM nao tem prazo. Uma vez comandado, o
eixo anda ate alguem mandar zero, entao um PC que morre no meio de um pulso
deixa o mount andando sozinho. O ``PulseGuide`` e o oposto: voce diz "mova por
N milissegundos" e o FIRMWARE conta o tempo. Se o PC morrer, o mount para.

Isso o tornaria a primitiva certa para os micropulsos do tracker, e resolveria
de uma vez o risco de deriva por morte subita.

A duvida que este programa responde: ele funciona neste mount? O operador
testou no comeco do projeto e nao conseguiu mover nada. A hipotese e que
faltava o RASTREIO ligado -- o PulseGuide do ASCOM e definido como correcao ao
rastreio, e varios mounts o ignoram sem ele. Aqui isso e testado nas duas
condicoes.

MOVE O MOUNT. Rode com o telescopio livre e a mao no botao. Os deslocamentos
sao minusculos (menos de 2 arcsec por pulso), mas sao movimento real.

Uso:

    python programas_principais/testar_pulseguide.py
    python programas_principais/testar_pulseguide.py --com-rastreio
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import call, mount_address, read_altaz

# ASCOM GuideDirections: 0=Norte 1=Sul 2=Leste 3=Oeste
DIRECOES = {0: "norte", 1: "sul", 2: "leste", 3: "oeste"}
DURACOES_MS = (200, 500, 1000)
REPETICOES = 3


def ler(comando, **extra):
    try:
        return call("GET", comando, timeout=5.0, **extra)
    except Exception as exc:
        return f"<erro: {type(exc).__name__}>"


def esperar_fim_do_pulso(limite_s: float) -> float:
    """Espera IsPulseGuiding voltar a False. Devolve quanto esperou."""
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < limite_s:
        try:
            if not call("GET", "ispulseguiding", timeout=3.0):
                break
        except Exception:
            break
        time.sleep(0.05)
    return time.perf_counter() - inicio


def um_pulso(direcao: int, duracao_ms: int) -> dict:
    """Um PulseGuide, medindo o deslocamento pelo encoder."""
    az0, alt0 = read_altaz()
    t0 = time.perf_counter()
    call("PUT", "pulseguide", data={"Direction": direcao, "Duration": duracao_ms})
    esperou = esperar_fim_do_pulso(duracao_ms / 1000.0 + 3.0)
    # O encoder do AM5 e quantizado; uma folga deixa o valor assentar.
    time.sleep(0.6)
    az1, alt1 = read_altaz()
    return {
        "d_az_arcsec": (az1 - az0) * 3600.0,
        "d_alt_arcsec": (alt1 - alt0) * 3600.0,
        "espera_s": esperou,
        "tempo_total_s": time.perf_counter() - t0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--com-rastreio", action="store_true",
        help="liga o rastreio sideral antes de testar e desliga no fim",
    )
    args = parser.parse_args()

    print(f"Mount em {mount_address()}")
    taxa_ra = ler("guideraterightascension")
    taxa_dec = ler("guideratedeclination")
    print(f"  CanPulseGuide      : {ler('canpulseguide')}")
    print(f"  taxa de guiagem RA : {taxa_ra} deg/s")
    print(f"  taxa de guiagem Dec: {taxa_dec} deg/s")
    if isinstance(taxa_ra, float) and taxa_ra > 0:
        print(f"  -> 1 px (0,381″) levaria {0.381 / (taxa_ra * 3600) * 1000:.0f} ms")
    rastreio_antes = ler("tracking")
    print(f"  rastreando         : {rastreio_antes}")

    print("\nESTE PROGRAMA MOVE O MOUNT.")
    print("Deslocamento maximo esperado: ~2 arcsec por pulso, 27 pulsos no total.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado.")
        return 1

    if args.com_rastreio:
        print("\nLigando o rastreio sideral...")
        call("PUT", "tracking", data={"Tracking": True})
        time.sleep(2.0)
        print(f"  rastreando agora: {ler('tracking')}")

    try:
        print(f"\n{'direcao':>8s} {'ms':>6s} {'d_az':>9s} {'d_alt':>9s} {'espera':>8s}")
        resultados = {}
        for direcao in (0, 1, 2, 3):
            for duracao in DURACOES_MS:
                medidas = []
                for _ in range(REPETICOES):
                    r = um_pulso(direcao, duracao)
                    medidas.append(r)
                    print(
                        f"{DIRECOES[direcao]:>8s} {duracao:6d} "
                        f"{r['d_az_arcsec']:+9.2f} {r['d_alt_arcsec']:+9.2f} "
                        f"{r['espera_s']:8.2f}"
                    )
                    time.sleep(0.4)
                resultados[(direcao, duracao)] = medidas

        print("\n=== RESUMO ===")
        houve_movimento = False
        for (direcao, duracao), medidas in resultados.items():
            az = statistics.median(m["d_az_arcsec"] for m in medidas)
            alt = statistics.median(m["d_alt_arcsec"] for m in medidas)
            desloc = max(abs(az), abs(alt))
            if desloc > 0.3:
                houve_movimento = True
            print(
                f"  {DIRECOES[direcao]:>6s} {duracao:5d} ms -> "
                f"az {az:+6.2f}″  alt {alt:+6.2f}″"
            )

        print()
        if houve_movimento:
            print("O PulseGuide MOVE este mount.")
            print("Proximo passo: medir se a duracao e proporcional ao deslocamento,")
            print("e se o firmware para sozinho quando o cliente cai.")
        else:
            print("O PulseGuide NAO moveu o mount de forma mensuravel.")
            if not args.com_rastreio:
                print("Repita com --com-rastreio: o ASCOM define PulseGuide como")
                print("correcao ao rastreio, e varios mounts o ignoram sem ele.")
            else:
                print("Nem com rastreio ligado. O caminho do PulseGuide morre aqui,")
                print("e a protecao continua sendo a tarefa de parada no boot.")
    finally:
        if args.com_rastreio:
            print("\nDesligando o rastreio...")
            try:
                call("PUT", "tracking", data={"Tracking": False})
                print(f"  rastreando: {ler('tracking')}")
            except Exception as exc:
                print(f"  ATENCAO: nao consegui desligar o rastreio ({exc}).")
                print("  DESLIGUE MANUALMENTE antes de rodar o tracker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
