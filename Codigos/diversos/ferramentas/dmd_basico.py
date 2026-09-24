"""Primeiros passos com o DMD: ele e um segundo monitor, e controlar e desenhar.

O DMD do laboratorio e um DLP4710 (1920 x 1080, pixel de 5,4 um, espelhos de
+-17 graus), numa placa DLPDLCR4710EVM-G2 convertida conforme Cox e Drozdov,
Applied Optics. Detalhes, alimentacao e a ORDEM DE DESLIGAR no roteiro, secao
"DMD do laboratorio da USP".

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
    + / -   no retangulo, multiplica ou divide o lado por 1,5
            nas listras, soma ou tira 2 pixels do periodo
    setas   movem o retangulo 1 pixel por toque, para ajuste fino
    w a s d movem o retangulo 50 pixels por toque, para achar o feixe
            (o feixe de um HeNe tem ~1 mm, ou ~185 espelhos de 5,4 um)
    i       inverte a imagem: preto vira branco e vice-versa
    Esc     sai, deixando o DMD preto

Por que inverter: os espelhos refletem igualmente bem nos dois estados, e
"ligado" e "desligado" sao so nomes herdados do projetor. Se a direcao dos
espelhos DESLIGADOS for a mais conveniente na bancada, desenha-se o ponto em
preto sobre fundo branco. Confira antes que o reflexo usado some com a tela
toda branca: se ficar aceso nos dois, e o reflexo fixo da janela de vidro.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes

import cv2
import numpy as np

# O DLP4710 tem 1920 x 1080 espelhos. O EDID da placa, porem, anuncia 1280 x 720
# como resolucao PREFERIDA (medido em 2026-09-24), e e essa que o Windows marca
# como "recomendada". Recebendo 720p, a placa amplia a imagem 1,5 vez e um pixel
# deixa de ser um espelho. 1920 x 1080 a 60 Hz esta na lista do EDID, mas precisa
# ser escolhida na mao.
RESOLUCAO_NATIVA = (1920, 1080)

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
    parser.add_argument("--ignorar-resolucao", action="store_true",
                        help="roda mesmo fora de 1920x1080 (os pixels deixam de ser espelhos)")
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
    if (largura, altura) != RESOLUCAO_NATIVA and not args.ignorar_resolucao:
        print(f"A tela do DMD esta em {largura}x{altura}, e o chip tem "
              f"{RESOLUCAO_NATIVA[0]}x{RESOLUCAO_NATIVA[1]} espelhos.")
        print("Em Configuracoes, Sistema, Video, escolha a tela do DMD e ponha")
        print("1920 x 1080 NA MAO: a 'recomendada' desta placa e 1280 x 720.")
        return 1
    print(f"DMD: {largura}x{altura} em x={m['x0']}. Use a janela de previa.")

    cx, cy = largura // 2, altura // 2
    # O quadrado nasce maior que o feixe, para ser facil de achar com o laser.
    lado, periodo = 200, 10
    passo_rapido = 50
    modo = "p"
    invertido = False

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

            if invertido:
                img = 255 - img
                legenda += " (INVERTIDO)"

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
            elif letra == "i":
                invertido = not invertido
            elif letra in "wasd":
                cx += {"a": -passo_rapido, "d": passo_rapido}.get(letra, 0)
                cy += {"w": -passo_rapido, "s": passo_rapido}.get(letra, 0)
                cx, cy = int(np.clip(cx, 0, largura - 1)), int(np.clip(cy, 0, altura - 1))
            elif letra in "+=":
                if modo == "l":
                    periodo += 2
                else:
                    lado = min(int(round(lado * 1.5)), altura)
            elif letra == "-":
                if modo == "l":
                    periodo = max(2, periodo - 2)
                else:
                    lado = max(2, int(round(lado / 1.5)))
    finally:
        # Sair deixa o DMD preto, sem inverter: nenhum espelho ligado mandando
        # luz pela sala, qualquer que seja o modo em uso.
        cv2.imshow(JANELA_DMD, tudo(largura, altura, 0))
        cv2.waitKey(1)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
