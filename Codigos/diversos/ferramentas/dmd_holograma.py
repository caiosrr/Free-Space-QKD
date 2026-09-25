"""Holograma de fase no DMD: grade, OAM e turbulencia, para alinhar a bancada.

O holograma e desenhado so dentro de uma ABERTURA circular, que voce centra no
feixe do laser. Fora dela o DMD fica preto. Na tela principal aparecem duas
previas: a FASE desenhada, e o CAMPO DISTANTE simulado, que e o que a lente (ou
a propria propagacao ate longe) faz com o holograma. Nele se veem as ordens: a
0 no centro, a +1 marcada com um circulo, a -1 no lado oposto.

A fisica esta em modulos/dmd/holograma.py, e o estudo em
Anotacoes/07_holograma_no_dmd.md.

Uso, a partir da pasta Codigos, com a tela do DMD em 1920 x 1080:

    python diversos/ferramentas/dmd_holograma.py --listar-monitores
    python diversos/ferramentas/dmd_holograma.py --monitor 2

Teclas, com a janela de PREVIA selecionada:

    g  grade pura              o  grade com OAM (forquilha)
    t  grade com turbulencia   c  OAM mais turbulencia
    p  tudo preto              b  tudo branco
    w a s d   movem a abertura 25 px;  setas, 1 px
    - / =     raio da abertura menor / maior
    [ / ]     periodo da grade menor / maior (ordens mais / menos afastadas)
    , / .     gira a grade 15 graus (gira a direcao das ordens)
    k / l     OAM: ell menos / mais um
    z / x     turbulencia mais FORTE / mais fraca (r0 dividido / multiplicado por 1,5)
    n         nova tela de turbulencia (outra realizacao aleatoria)
    v         vento liga / desliga: a tela anda, como a atmosfera
    Esc       sai, deixando o DMD preto

ALINHAMENTO, em resumo:
    1. modo g, abertura sobre o feixe. No cartao aparecem a ordem 0, forte, e
       as +1 e -1 dos lados. Gire a grade (, .) ate elas ficarem na horizontal.
    2. o espelho da segunda mesa pega SO a +1 e a devolve ao telescopio.
    3. modo o: se o ponto na camera vira ROSQUINHA, voce esta na +1 ou na -1.
       A ordem 0 nao muda com o OAM. E o teste que confirma o alinhamento.
    4. modo t ou c, e a turbulencia entra.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

CODIGOS_DIR = Path(__file__).resolve().parents[2]
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.dmd.holograma import (  # noqa: E402
    TelaKolmogorov,
    coordenadas,
    fase_oam,
    holograma_lee,
    portadora,
)
from modulos.dmd.tela import (  # noqa: E402
    JANELA_DMD,
    SETA_BAIXO,
    SETA_CIMA,
    SETA_DIR,
    SETA_ESQ,
    abrir_janela_dmd,
    declarar_ciente_de_dpi,
    escolher_monitor,
    imprimir_monitores,
    listar_monitores,
)

JANELA_PREVIA = "holograma (clique aqui para usar o teclado)"
LADO_PAINEL = 360
PITCH_M = 5.4e-6
LAMBDA_M = 633e-9
TAMANHO_TELA = 2048          # periodo da tela de turbulencia, em pixels do DMD


@dataclass
class Estado:
    modo: str = "g"
    cx: int = 960
    cy: int = 540
    raio: int = 150          # o feixe de 1 mm tem ~185 px de diametro
    periodo: int = 8
    angulo: float = 0.0
    ell: int = 1
    r0: float = 40.0         # raio de Fried NO DMD, em pixels
    vento: bool = False
    deslocamento: int = 0
    semente: int = 0


def fase_do_modo(estado: Estado, x: np.ndarray, y: np.ndarray,
                 x0: int, y0: int, tela: TelaKolmogorov) -> np.ndarray:
    """A fase desenhada dentro da abertura, conforme o modo.

    A tela de turbulencia foi gerada com r0 = 1 px, e aqui e reescalada: a
    fase de Kolmogorov cresce como r0^(-5/6), porque o espectro de potencia
    cresce como r0^(-5/3). Mudar a forca e so multiplicar, sem gerar outra.
    """
    fase = np.zeros_like(x)
    if estado.modo in ("o", "c"):
        fase = fase + fase_oam(x, y, estado.ell)
    if estado.modo in ("t", "c"):
        h, w = x.shape
        base = tela.janela(x0 + estado.deslocamento, y0, w, h)
        fase = fase + base * estado.r0 ** (-5.0 / 6.0)
    return fase


def montar_holograma(estado: Estado, largura: int, altura: int,
                     tela: TelaKolmogorov) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Quadro inteiro do DMD, mais o recorte da abertura para as previas.

    Devolve (quadro 0/255, recorte binario, fase do recorte, mascara).
    """
    quadro = np.zeros((altura, largura), dtype=np.uint8)
    if estado.modo == "b":
        quadro[:] = 255
    if estado.modo in ("p", "b"):
        vazio = np.zeros((1, 1))
        return quadro, vazio, vazio, vazio.astype(bool)

    r = estado.raio
    x0, x1 = max(0, estado.cx - r), min(largura, estado.cx + r + 1)
    y0, y1 = max(0, estado.cy - r), min(altura, estado.cy + r + 1)
    x, y = coordenadas(x1 - x0, y1 - y0, estado.cx - x0, estado.cy - y0)
    mascara = np.hypot(x, y) <= r

    fase = fase_do_modo(estado, x, y, x0, y0, tela)
    ligados = holograma_lee(fase, portadora(x, y, estado.periodo, estado.angulo)) & mascara
    quadro[y0:y1, x0:x1] = np.where(ligados, 255, 0).astype(np.uint8)
    return quadro, ligados, fase, mascara


def painel_fase(fase: np.ndarray, mascara: np.ndarray) -> np.ndarray:
    """Fase dobrada em [0, 2 pi) como cor; fora da abertura, cinza."""
    if fase.size <= 1:
        return np.full((LADO_PAINEL, LADO_PAINEL, 3), 40, np.uint8)
    h = (np.mod(fase, 2 * np.pi) / (2 * np.pi) * 179).astype(np.uint8)
    hsv = np.dstack([h, np.full_like(h, 255), np.full_like(h, 255)])
    cor = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    cor[~mascara] = 40
    return cv2.resize(cor, (LADO_PAINEL, LADO_PAINEL), interpolation=cv2.INTER_NEAREST)


def painel_campo_distante(ligados: np.ndarray, estado: Estado) -> np.ndarray:
    """|FFT|^2 do recorte, em escala log: e o que a lente faz com o holograma.

    Pixels ligados refletem para a saida util com amplitude 1, os desligados
    com 0: o campo nessa direcao e o proprio padrao binario. A ordem +1 fica a
    n/periodo pixels do centro, na direcao da grade, e e marcada com um circulo.
    """
    if ligados.size <= 1:
        return np.full((LADO_PAINEL, LADO_PAINEL, 3), 40, np.uint8)
    n = 512
    campo = np.zeros((n, n))
    h, w = ligados.shape
    campo[:min(h, n), :min(w, n)] = ligados[:n, :n]
    potencia = np.fft.fftshift(np.abs(np.fft.fft2(campo)) ** 2)
    img = np.log10(potencia + 1.0)
    img = (255 * img / max(img.max(), 1e-9)).astype(np.uint8)
    cor = cv2.cvtColor(cv2.resize(img, (LADO_PAINEL, LADO_PAINEL),
                                  interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
    a = np.deg2rad(estado.angulo)
    d = LADO_PAINEL / estado.periodo
    c = LADO_PAINEL // 2
    mais1 = (int(round(c + d * np.cos(a))), int(round(c + d * np.sin(a))))
    cv2.circle(cor, mais1, 9, (0, 200, 255), 1, cv2.LINE_AA)
    return cor


def legenda(estado: Estado) -> list[str]:
    nomes = {"g": "grade pura", "o": "OAM", "t": "turbulencia", "c": "OAM + turbulencia",
             "p": "tudo preto", "b": "tudo branco"}
    ang_ordens = LAMBDA_M / (estado.periodo * PITCH_M)
    linhas = [
        f"modo: {nomes[estado.modo]}",
        f"abertura: centro ({estado.cx}, {estado.cy}), raio {estado.raio} px",
        f"grade: periodo {estado.periodo} px, angulo {estado.angulo:.0f} graus",
        f"ordens separadas por {ang_ordens * 1e3:.1f} mrad = {ang_ordens * 300:.1f} cm a 3 m",
    ]
    if estado.modo in ("o", "c"):
        linhas.append(f"OAM: ell = {estado.ell}")
    if estado.modo in ("t", "c"):
        linhas.append(f"turbulencia: r0 = {estado.r0:.1f} px, "
                      f"feixe/r0 ~ {185 / estado.r0:.1f}"
                      + ("  (vento)" if estado.vento else ""))
        if estado.periodo > estado.r0 / 3:
            linhas.append("ATENCAO: periodo grosso para esse r0; diminua com [")
    return linhas


def previa(estado: Estado, ligados: np.ndarray, fase: np.ndarray,
           mascara: np.ndarray) -> np.ndarray:
    topo = np.hstack([painel_fase(fase, mascara), painel_campo_distante(ligados, estado)])
    texto = np.full((24 * 7, topo.shape[1], 3), 20, np.uint8)
    for i, linha in enumerate(legenda(estado)):
        cor = (0, 120, 255) if linha.startswith("ATENCAO") else (230, 230, 230)
        cv2.putText(texto, linha, (8, 20 + 24 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    cor, 1, cv2.LINE_AA)
    cv2.putText(topo, "fase", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(topo, "campo distante (log)", (LADO_PAINEL + 8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([topo, texto])


def aplicar_tecla(estado: Estado, tecla: int, largura: int, altura: int) -> bool:
    """Atualiza o estado. Devolve False para sair."""
    if tecla == 27:
        return False
    if tecla in (SETA_ESQ, SETA_DIR, SETA_CIMA, SETA_BAIXO):
        estado.cx += {SETA_ESQ: -1, SETA_DIR: 1}.get(tecla, 0)
        estado.cy += {SETA_CIMA: -1, SETA_BAIXO: 1}.get(tecla, 0)
    elif tecla != -1:
        letra = chr(tecla & 0xFF).lower()
        if letra in "gotcpb":
            estado.modo = letra
        elif letra in "wasd":
            estado.cx += {"a": -25, "d": 25}.get(letra, 0)
            estado.cy += {"w": -25, "s": 25}.get(letra, 0)
        elif letra == "-":
            estado.raio = max(10, int(round(estado.raio / 1.25)))
        elif letra == "=":
            estado.raio = min(altura // 2, int(round(estado.raio * 1.25)))
        elif letra == "[":
            estado.periodo = max(2, estado.periodo - 1)
        elif letra == "]":
            estado.periodo += 1
        elif letra == ",":
            estado.angulo = (estado.angulo - 15.0) % 360.0
        elif letra == ".":
            estado.angulo = (estado.angulo + 15.0) % 360.0
        elif letra == "k":
            estado.ell -= 1
        elif letra == "l":
            estado.ell += 1
        elif letra == "z":
            estado.r0 = max(2.0, estado.r0 / 1.5)
        elif letra == "x":
            estado.r0 = min(2000.0, estado.r0 * 1.5)
        elif letra == "v":
            estado.vento = not estado.vento
    estado.cx = int(np.clip(estado.cx, 0, largura - 1))
    estado.cy = int(np.clip(estado.cy, 0, altura - 1))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listar-monitores", action="store_true")
    parser.add_argument("--monitor", type=int, default=None)
    parser.add_argument("--ignorar-resolucao", action="store_true")
    parser.add_argument("--centro", type=int, nargs=2, default=None, metavar=("X", "Y"),
                        help="centro inicial da abertura, em pixels do DMD")
    args = parser.parse_args()

    declarar_ciente_de_dpi()
    if args.listar_monitores or args.monitor is None:
        imprimir_monitores(listar_monitores())
        if args.monitor is None:
            print()
            print("Escolha o do DMD com --monitor N.")
        return 0
    m = escolher_monitor(args.monitor, args.ignorar_resolucao)
    if m is None:
        return 1
    largura, altura = m["largura"], m["altura"]

    estado = Estado(cx=largura // 2, cy=altura // 2)
    if args.centro:
        estado.cx, estado.cy = args.centro
    print("Gerando a tela de turbulencia (uns segundos)...")
    tela = TelaKolmogorov(TAMANHO_TELA, 1.0, semente=estado.semente)
    print("Pronto. Use a janela de previa.")

    abrir_janela_dmd(m["x0"], m["y0"])
    cv2.namedWindow(JANELA_PREVIA, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            quadro, ligados, fase, mascara = montar_holograma(estado, largura, altura, tela)
            cv2.imshow(JANELA_DMD, quadro)
            cv2.imshow(JANELA_PREVIA, previa(estado, ligados, fase, mascara))
            tecla = cv2.waitKeyEx(30 if estado.vento else 0)
            if tecla != -1 and chr(tecla & 0xFF).lower() == "n":
                estado.semente += 1
                tela = TelaKolmogorov(TAMANHO_TELA, 1.0, semente=estado.semente)
                continue
            if not aplicar_tecla(estado, tecla, largura, altura):
                break
            if estado.vento and estado.modo in ("t", "c"):
                estado.deslocamento += 2
    finally:
        # Sair deixa o DMD preto: nenhum espelho ligado mandando luz pela sala.
        cv2.imshow(JANELA_DMD, np.zeros((altura, largura), np.uint8))
        cv2.waitKey(1)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
