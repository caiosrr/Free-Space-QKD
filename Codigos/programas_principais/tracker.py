"""Inicia o tracker principal com a camera ASI ou IDS.

    python programas_principais/tracker.py --camera ids --horas 0.5 --sem-autoteste
    python programas_principais/tracker.py --camera zwo --horas 0.5 --sem-autoteste

``asi`` e a ASI pelo ASCOM; ``zwo`` e a ASI pelo SDK nativo da ZWO.

Blocos alternados com e sem correcao, para medir o efeito do controle:

    python programas_principais/tracker.py --camera ids --horas 8 --blocos-minutos 15
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse
import os
import subprocess

from programas_principais._iniciador import aplicar_camera, perguntar_camera

VIGIA = CODIGOS_DIR / "programas_principais" / "vigia_mount.py"
VIGIA_LOG = CODIGOS_DIR / "resultados" / "vigia_mount.log"
# Folga entre a ultima linha de telemetria e o disparo. Os 25 s padrao do vigia
# podem cair em cima do retorno a posicao inicial, que roda DEPOIS de a
# telemetria parar: na sessao de 2026-09-15 o retorno levou 2 s, mas precisou de
# duas tentativas, e com mais tentativas os 25 s ficariam apertados.
VIGIA_DISPARO_S = 60.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids", "zwo"], default=None)
    parser.add_argument(
        "--horas",
        type=float,
        default=None,
        help="Duracao maxima da sessao. Sem isso, o programa pergunta.",
    )
    autoteste = parser.add_mutually_exclusive_group()
    autoteste.add_argument("--autoteste", dest="autoteste", action="store_true")
    autoteste.add_argument("--sem-autoteste", dest="autoteste", action="store_false")
    parser.set_defaults(autoteste=None)
    parser.add_argument(
        "--blocos-minutos", type=float, default=None,
        help="alterna blocos COM e SEM correcao dessa duracao; o primeiro corrige",
    )
    vigia = parser.add_mutually_exclusive_group()
    vigia.add_argument("--vigia", dest="vigia", action="store_true")
    vigia.add_argument("--sem-vigia", dest="vigia", action="store_false")
    parser.set_defaults(vigia=True)
    return parser.parse_args()


def iniciar_vigia() -> subprocess.Popen | None:
    """Sobe o vigia como processo FILHO, para nao depender de outra janela.

    O arranjo e proposital nos dois desfechos. Se o tracker terminar de forma
    ordenada, inclusive por Ctrl+C, o ``finally`` mata o vigia e ele nao chega a
    interferir no retorno a posicao inicial. Se o tracker for morto sem rodar o
    ``finally``, que e o caso que o vigia existe para cobrir, o filho sobrevive e
    para o mount sozinho.

    Roda em grupo de processos proprio para o Ctrl+C do console nao chegar nele
    direto: quem decide encerra-lo e o ``finally``, e nao a ordem em que o
    Windows entrega o sinal.
    """
    try:
        VIGIA_LOG.parent.mkdir(parents=True, exist_ok=True)
        saida = VIGIA_LOG.open("a", encoding="utf-8", buffering=1)
        return subprocess.Popen(
            [sys.executable, str(VIGIA), "--disparar-em", str(VIGIA_DISPARO_S)],
            cwd=str(CODIGOS_DIR), stdout=saida, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        # Sem vigia a sessao ainda roda, e as outras duas protecoes seguem de pe.
        print(f"AVISO: nao consegui iniciar o vigia ({type(exc).__name__}: {exc}).")
        return None


if __name__ == "__main__":
    args = parse_args()
    escolha = args.camera or perguntar_camera({"1": "asi", "2": "ids", "3": "zwo"}, "1")
    camera = aplicar_camera(escolha)
    print(f"Iniciando tracker com {camera}.")
    if args.blocos_minutos:
        # Lido pela configuracao na hora do import: precisa vir antes dele.
        os.environ["QKD_BLOCOS_CORRECAO"] = "1"
        os.environ["QKD_BLOCOS_CORRECAO_S"] = str(args.blocos_minutos * 60.0)
        print(f"Blocos de {args.blocos_minutos:g} min: com correcao, sem, com, ...")

    from modulos.controle.tracker_sessao import main

    vigia = iniciar_vigia() if args.vigia else None
    if vigia is not None:
        print(f"Vigia do mount ativo (pid {vigia.pid}), saida em {VIGIA_LOG}.")
    try:
        main(session_hours=args.horas, executar_autoteste=args.autoteste)
    finally:
        if vigia is not None and vigia.poll() is None:
            vigia.terminate()
            print("Vigia encerrado junto com a sessao.")
