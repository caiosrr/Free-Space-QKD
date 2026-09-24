"""Primeiros passos com o DMD: ele e um segundo monitor, e controlar e desenhar.

A placa do DMD aparece para o Windows como uma tela a mais. Cada pixel da imagem
mostrada nela vira um micro-espelho:

    255 (branco)  espelho LIGADO, manda a luz para a saida util
    0   (preto)   espelho DESLIGADO, manda a luz para outro lado

Por isso este programa so desenha esses dois valores. Um cinza qualquer nao vira
"meio espelho": vira espelho piscando no tempo, e a camera pode ver o pisca.

Tres cuidados que fazem o pixel da imagem ser o espelho certo:

1. a imagem tem EXATAMENTE a resolucao nativa do DMD. Se for diferente, o
   OpenCV redimensiona e cria bordas cinzas;
2. a escala do Windows naquela tela esta em 100%;
3. o programa se declara "ciente de DPI", senao o Windows estica a janela por
   conta propria quando a escala nao e 100%. Isso e feito aqui no inicio.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/dmd_basico.py --listar-monitores
    python diversos/ferramentas/dmd_basico.py --monitor 2

Com a janela de PREVIA selecionada (a pequena, na tela principal):

    b  tudo branco          p  tudo preto          m  metade acesa
    r  retangulo            l  listras verticais
    + / -   aumenta ou diminui o lado do retangulo ou o periodo das listras
    setas   movem o retangulo, 1 pixel por toque (Shift nao muda nada)
    Esc     sai, deixando o DMD preto
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes

import cv2
import numpy as np

JANELA_DMD = "DMD"
JANELA_PREVIA = "previa do DMD (clique aqui para usar o teclado)"
LARGURA_PREVIA = 640

# Codigos das setas devolvidos por cv2.waitKeyEx no Windows.
SETA_ESQ, SETA_CIMA, SETA_DIR, SETA_BAIXO = 2424832, 2490368, 2555904, 2621440


# ----------------------------------------------------------------- monitores

def declarar_ciente_de_dpi() -> None:
    """Pede ao Windows coordenadas em pixels reais, sem esticar a janela."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def listar_monitores() -> list[dict]:
    """Posicao e tamanho de cada tela, na ordem em que o Windows as enumera."""
    monitores: list[dict] = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    def ao_encontrar(hmon, _hdc, _rect, _dado):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        monitores.append({
            "x0": r.left, "y0": r.top,
            "largura": r.right - r.left, "altura": r.bottom - r.top,
            "principal": bool(info.dwFlags & 1),
        })
        return True

    TIPO = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(wintypes.RECT), ctypes.c_double)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, TIPO(ao_encontrar), 0)
    return monitores


# ------------------------------------------------------------------- padroes
#
# Cada padrao e so uma matriz numpy de altura x largura, com 0 e 255. E aqui
# que voce inventa testes novos: escreva uma funcao que devolva uma matriz.

def tudo(largura: int, altura: int, valor: int) -> np.ndarray:
    return np.full((altura, largura), valor, dtype=np.uint8)


def metade(largura: int, altura: int) -> np.ndarray:
    """Metade ESQUERDA acesa: mostra se a imagem chega espelhada no chip."""
    img = tudo(largura, altura, 0)
    img[:, : largura // 2] = 255
    return img


def retangulo(largura: int, altura: int, cx: int, cy: int, lado: int) -> np.ndarray:
    """Um quadrado aceso de 'lado' pixels, centrado em (cx, cy)."""
    img = tudo(largura, altura, 0)
    x1, y1 = cx - lado // 2, cy - lado // 2
    img[max(0, y1): max(0, y1 + lado), max(0, x1): max(0, x1 + lado)] = 255
    return img


def listras(largura: int, altura: int, periodo: int) -> np.ndarray:
    """Listras verticais: 'periodo' pixels = uma faixa acesa + uma apagada.

    E uma grade de difracao que voce controla. O angulo entre as ordens segue
    sin(theta) = lambda / (periodo * pitch), entao dobrar o periodo junta as
    ordens pela metade. Medindo o espacamento, sai o pitch do DMD.
    """
    periodo = max(2, int(periodo))
    img = tudo(largura, altura, 0)
    acesas = (np.arange(largura) % periodo) < periodo // 2
    img[:, acesas] = 255
    return img


# ------------------------------------------------------------------ exibicao

def abrir_janela_dmd(x0: int, y0: int) -> None:
    cv2.namedWindow(JANELA_DMD, cv2.WINDOW_NORMAL)
    # Mover ANTES de pedir tela cheia: a tela cheia vai para o monitor onde a
    # janela esta.
    cv2.moveWindow(JANELA_DMD, x0, y0)
    cv2.setWindowProperty(JANELA_DMD, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)


def previa(img: np.ndarray, legenda: str) -> np.ndarray:
    """Copia pequena para a tela principal. INTER_NEAREST: sem suavizar."""
    escala = LARGURA_PREVIA / img.shape[1]
    pequena = cv2.resize(img, None, fx=escala, fy=escala,
                         interpolation=cv2.INTER_NEAREST)
    cor = cv2.cvtColor(pequena, cv2.COLOR_GRAY2BGR)
    cv2.putText(cor, legenda, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (0, 200, 255), 1, cv2.LINE_AA)
    return cor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listar-monitores", action="store_true")
    parser.add_argument("--monitor", type=int, default=None,
                        help="numero do monitor do DMD, como aparece na lista")
    args = parser.parse_args()

    declarar_ciente_de_dpi()
    monitores = listar_monitores()
    if args.listar_monitores or args.monitor is None:
        for i, m in enumerate(monitores, 1):
            marca = "  (principal)" if m["principal"] else ""
            print(f"  monitor {i}: {m['largura']}x{m['altura']} "
                  f"em x={m['x0']}, y={m['y0']}{marca}")
        if args.monitor is None:
            print("\nEscolha o do DMD com --monitor N.")
        return 0

    if not 1 <= args.monitor <= len(monitores):
        print(f"Monitor {args.monitor} nao existe; ha {len(monitores)}.")
        return 1
    m = monitores[args.monitor - 1]
    if m["principal"]:
        print("Esse e o monitor PRINCIPAL. O DMD e o outro; confira com --listar-monitores.")
        return 1

    largura, altura = m["largura"], m["altura"]
    print(f"DMD: {largura}x{altura} em x={m['x0']}. Use a janela de previa.")

    cx, cy = largura // 2, altura // 2
    lado, periodo = 40, 10
    modo = "p"

    abrir_janela_dmd(m["x0"], m["y0"])
    cv2.namedWindow(JANELA_PREVIA, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            if modo == "b":
                img, legenda = tudo(largura, altura, 255), "tudo branco"
            elif modo == "m":
                img, legenda = metade(largura, altura), "metade esquerda acesa"
            elif modo == "r":
                img = retangulo(largura, altura, cx, cy, lado)
                legenda = f"retangulo lado={lado} px em ({cx}, {cy})"
            elif modo == "l":
                img, legenda = listras(largura, altura, periodo), f"listras periodo={periodo} px"
            else:
                img, legenda = tudo(largura, altura, 0), "tudo preto"

            cv2.imshow(JANELA_DMD, img)
            cv2.imshow(JANELA_PREVIA, previa(img, legenda))

            tecla = cv2.waitKeyEx(50)
            if tecla == 27:
                break
            if tecla in (SETA_ESQ, SETA_DIR, SETA_CIMA, SETA_BAIXO):
                cx += {SETA_ESQ: -1, SETA_DIR: 1}.get(tecla, 0)
                cy += {SETA_CIMA: -1, SETA_BAIXO: 1}.get(tecla, 0)
                cx, cy = int(np.clip(cx, 0, largura - 1)), int(np.clip(cy, 0, altura - 1))
                continue
            if tecla == -1:
                continue
            letra = chr(tecla & 0xFF).lower()
            if letra in "bpmrl":
                modo = letra
            elif letra in "+=":
                lado, periodo = lado + 2, periodo + 2
            elif letra == "-":
                lado, periodo = max(2, lado - 2), max(2, periodo - 2)
    finally:
        # Sair deixa o DMD preto: nenhum espelho ligado mandando luz pela sala.
        cv2.imshow(JANELA_DMD, tudo(largura, altura, 0))
        cv2.waitKey(1)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
