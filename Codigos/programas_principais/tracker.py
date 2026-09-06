"""Inicia o tracker principal com a camera ASI ou IDS.

Sem argumentos, pergunta tudo (botao Play do VS Code). Com argumentos, roda sem
prompt, o que permite relancar uma sessao por script:

    python programas_principais/tracker.py --camera ids --horas 0.5 --sem-autoteste
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse

from programas_principais._iniciador import aplicar_camera, perguntar_camera


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids"], default=None)
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    escolha = args.camera or perguntar_camera({"1": "asi", "2": "ids"}, "1")
    camera = aplicar_camera(escolha)
    print(f"Iniciando tracker com {camera}.")

    from modulos.controle.tracker_sessao import main

    main(session_hours=args.horas, executar_autoteste=args.autoteste)
