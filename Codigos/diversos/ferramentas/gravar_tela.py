"""Grava uma tela inteira em MP4, para mostrar a bancada numa reuniao.

Existe porque o PC do laboratorio nao tinha gravador da tela inteira
(2026-10-01): a Ferramenta de Captura dele e antiga e a Game Bar so grava uma
janela. Este programa so le a tela; nao mexe em nada.

O video sai em tempo real: se a captura atrasar, o quadro anterior e repetido,
para 10 s gravados durarem 10 s. Minimize esta janela de console antes, senao
ela aparece no video.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/gravar_tela.py                  # tela principal, 30 s
    python diversos/ferramentas/gravar_tela.py --segundos 60
    python diversos/ferramentas/gravar_tela.py --monitor 1      # outra tela
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.dmd.tela import declarar_ciente_de_dpi, imprimir_monitores, listar_monitores  # noqa: E402

PASTA_PADRAO = Path.home() / "Videos" / "gravacoes_bancada"


def abrir_video(caminho: Path, fps: float, largura: int, altura: int) -> cv2.VideoWriter:
    """H.264 se o OpenCV tiver (abre em qualquer lugar); senao, MPEG-4 simples."""
    for codec in ("avc1", "mp4v"):
        video = cv2.VideoWriter(str(caminho), cv2.VideoWriter_fourcc(*codec), fps, (largura, altura))
        if video.isOpened():
            print(f"Codec {codec}.")
            return video
    raise RuntimeError("O OpenCV nao conseguiu abrir nenhum codec de MP4.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--monitor", type=int, default=None,
                        help="numero da tela, como em --listar-monitores (padrao: a principal)")
    parser.add_argument("--listar-monitores", action="store_true")
    parser.add_argument("--segundos", type=float, default=30.0)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--pasta", type=Path, default=PASTA_PADRAO)
    parser.add_argument("--espera", type=float, default=5.0,
                        help="segundos antes de comecar, para arrumar as janelas")
    args = parser.parse_args()

    declarar_ciente_de_dpi()
    monitores = listar_monitores()
    if args.listar_monitores:
        imprimir_monitores(monitores)
        return 0
    if args.monitor is None:
        m = next(mm for mm in monitores if mm["principal"])
    elif 1 <= args.monitor <= len(monitores):
        m = monitores[args.monitor - 1]
    else:
        imprimir_monitores(monitores)
        print(f"Nao existe a tela {args.monitor}.")
        return 1
    caixa = (m["x0"], m["y0"], m["x0"] + m["largura"], m["y0"] + m["altura"])

    args.pasta.mkdir(parents=True, exist_ok=True)
    caminho = args.pasta / f"tela_{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
    video = abrir_video(caminho, args.fps, m["largura"], m["altura"])

    print(f"Gravando {m['largura']} x {m['altura']} por {args.segundos:g} s em {caminho}")
    print(f"Comeca em {args.espera:g} s. Ctrl+C aqui no console para antes.")
    time.sleep(args.espera)
    print("GRAVANDO")
    inicio = time.monotonic()
    escritos = 0
    quadro = None
    try:
        while (agora := time.monotonic() - inicio) < args.segundos:
            quadro = cv2.cvtColor(np.asarray(ImageGrab.grab(bbox=caixa, all_screens=True)),
                                  cv2.COLOR_RGB2BGR)
            # Quantos quadros o video ja deveria ter neste instante.
            devidos = int((time.monotonic() - inicio) * args.fps) + 1
            while escritos < devidos:
                video.write(quadro)
                escritos += 1
    except KeyboardInterrupt:
        agora = time.monotonic() - inicio
        print("\nParado pelo operador.")
    finally:
        video.release()
    print(f"Pronto: {agora:.1f} s, {escritos} quadros, em {caminho}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
