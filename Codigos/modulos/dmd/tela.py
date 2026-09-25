"""O DMD como segundo monitor: achar a tela dele e abrir uma janela em tela cheia.

Compartilhado por todos os programas que desenham no DMD, para que as regras
que fazem um pixel da imagem ser exatamente um espelho fiquem num lugar so:

1. a tela do DMD precisa estar em 1920 x 1080, a resolucao do chip;
2. o processo se declara ciente de DPI, senao o Windows estica a janela por
   conta propria quando a escala da tela nao e 100%.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import cv2

# O DLP4710 tem 1920 x 1080 espelhos. O EDID da placa, porem, anuncia 1280 x 720
# como resolucao PREFERIDA (medido em 2026-09-24), e e essa que o Windows marca
# como "recomendada". Recebendo 720p, a placa amplia a imagem 1,5 vez e um pixel
# deixa de ser um espelho. 1920 x 1080 a 60 Hz esta na lista do EDID, mas precisa
# ser escolhida na mao.
RESOLUCAO_NATIVA = (1920, 1080)

JANELA_DMD = "DMD"

# Codigos das setas devolvidos por cv2.waitKeyEx no Windows.
SETA_ESQ, SETA_CIMA, SETA_DIR, SETA_BAIXO = 2424832, 2490368, 2555904, 2621440


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


def imprimir_monitores(monitores: list[dict]) -> None:
    for i, m in enumerate(monitores, 1):
        marca = "  (principal)" if m["principal"] else ""
        print(f"  monitor {i}: {m['largura']}x{m['altura']} "
              f"em x={m['x0']}, y={m['y0']}{marca}")


def escolher_monitor(numero: int, ignorar_resolucao: bool = False) -> dict | None:
    """O monitor do DMD, ou None com a explicacao impressa do que esta errado."""
    monitores = listar_monitores()
    if not 1 <= numero <= len(monitores):
        print(f"Monitor {numero} nao existe; ha {len(monitores)}.")
        return None
    m = monitores[numero - 1]
    if m["principal"]:
        print("Esse e o monitor PRINCIPAL. O DMD e o outro; confira com --listar-monitores.")
        return None
    if (m["largura"], m["altura"]) != RESOLUCAO_NATIVA and not ignorar_resolucao:
        print(f"A tela do DMD esta em {m['largura']}x{m['altura']}, e o chip tem "
              f"{RESOLUCAO_NATIVA[0]}x{RESOLUCAO_NATIVA[1]} espelhos.")
        print("Em Configuracoes, Sistema, Video, escolha a tela do DMD e ponha")
        print("1920 x 1080 NA MAO: a 'recomendada' desta placa e 1280 x 720.")
        return None
    return m


def abrir_janela_dmd(x0: int, y0: int) -> None:
    cv2.namedWindow(JANELA_DMD, cv2.WINDOW_NORMAL)
    # Mover ANTES de pedir tela cheia: a tela cheia vai para o monitor onde a
    # janela esta.
    cv2.moveWindow(JANELA_DMD, x0, y0)
    cv2.setWindowProperty(JANELA_DMD, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
