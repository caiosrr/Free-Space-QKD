"""Holograma de fase no DMD: grade, OAM e turbulencia, para alinhar a bancada.

Bancada da USP, alinhamento de 2026-10-01 (pasta Codigos):

    .venv\\Scripts\\python.exe diversos\\ferramentas\\dmd_holograma.py --monitor 2 --centro 856 615 --raio 94 --periodo 7 --angulo 90

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
    h / j     vento mais lento / mais rapido (velocidade dividida / multiplicada por 1,5)
    i         tira / devolve a inclinacao media da tela (o que o tracker corrige)
    f         campo distante da previa liga / desliga (custa ~16 ms por quadro)
    4 6 8 2   mira: desvia a ordem +1 de 0,25 mrad em x / y do DMD;  5 zera
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
import time
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
    # Parametro de Fried NO DMD, em pixels. E um diametro: a abertura de
    # diametro r0 e a maior com fase ainda coerente (1 rad rms).
    r0: float = 40.0
    vento: bool = False
    vento_px_s: float = 30.0
    deslocamento: float = 0.0
    semente: int = 0
    sem_inclinacao: bool = False
    campo_distante: bool = True
    mira_x_mrad: float = 0.0
    mira_y_mrad: float = 0.0
    # Informativos, recalculados a cada quadro para a legenda.
    inclinacao_tela_mrad: tuple[float, float] = (0.0, 0.0)
    quadros_por_s: float = 0.0


# Gradiente de fase (rad/px) para angulo (mrad): theta = gradiente * lambda / (2 pi pitch).
# Confere com a portadora: 2 pi / 7 rad/px da 16,7 mrad, a separacao entre ordens.
MRAD_POR_RAD_PX = LAMBDA_M / (2.0 * np.pi * PITCH_M) * 1e3
PASSO_MIRA_MRAD = 0.25
INTERVALO_PREVIA_S = 0.2


def inclinacao(fase: np.ndarray, x: np.ndarray, y: np.ndarray,
               mascara: np.ndarray) -> tuple[float, float]:
    """Gradiente medio da fase na abertura (rad/px), por minimos quadrados.

    Pelas equacoes normais, um sistema 3 x 3 de somas, em vez de decompor a
    matriz de todos os pixels: com raio de 500 px, de 43 ms para poucos ms.
    """
    xm, ym, fm = x[mascara], y[mascara], fase[mascara]
    a = np.array([[xm @ xm, xm @ ym, xm.sum()],
                  [xm @ ym, ym @ ym, ym.sum()],
                  [xm.sum(), ym.sum(), float(xm.size)]])
    c = np.linalg.solve(a, np.array([xm @ fm, ym @ fm, fm.sum()]))
    return float(c[0]), float(c[1])


def fase_do_modo(estado: Estado, x: np.ndarray, y: np.ndarray,
                 x0: int, y0: int, tela: TelaKolmogorov) -> np.ndarray:
    """A fase desenhada dentro da abertura, conforme o modo.

    A tela de turbulencia foi gerada com r0 = 1 px, e aqui e reescalada: a
    fase de Kolmogorov cresce como r0^(-5/6), porque o espectro de potencia
    cresce como r0^(-5/3). Mudar a forca e so multiplicar, sem gerar outra.

    Por isso a inclinacao da tela tem direcao fixa e cresce junto: apertar z
    empurra a ordem +1 sempre para o mesmo lado. A inclinacao e a maior parte
    da fase de Kolmogorov numa abertura (87 %, Noll 1976), e e o que o tracker
    corrige; com sem_inclinacao ela sai, e sobra o que um tracker perfeito
    deixaria.
    """
    fase = np.zeros_like(x)
    if estado.modo in ("o", "c"):
        fase = fase + fase_oam(x, y, estado.ell)
    if estado.modo in ("t", "c"):
        h, w = x.shape
        turb = tela.janela(x0 + int(estado.deslocamento), y0, w, h) * estado.r0 ** (-5.0 / 6.0)
        gx, gy = inclinacao(turb, x, y, x * x + y * y <= estado.raio ** 2)
        estado.inclinacao_tela_mrad = (gx * MRAD_POR_RAD_PX, gy * MRAD_POR_RAD_PX)
        if estado.sem_inclinacao:
            turb = turb - gx * x - gy * y
        fase = fase + turb
    # Mira: uma inclinacao conhecida, para levar a +1 a um angulo escolhido.
    if estado.mira_x_mrad or estado.mira_y_mrad:
        fase = fase + (estado.mira_x_mrad * x + estado.mira_y_mrad * y) / MRAD_POR_RAD_PX
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
    mascara = x * x + y * y <= r * r

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
    if estado.mira_x_mrad or estado.mira_y_mrad:
        linhas.append(f"mira: ({estado.mira_x_mrad:+.2f}, {estado.mira_y_mrad:+.2f}) mrad em x, y do DMD")
    if estado.modo in ("o", "c"):
        linhas.append(f"OAM: ell = {estado.ell}")
    if estado.modo in ("t", "c"):
        ix, iy = estado.inclinacao_tela_mrad
        # tau0 = 0,314 r0 / v: o tempo que o vento leva para renovar a fase
        # num pedaco do tamanho de r0. Na atmosfera do enlace, poucos ms.
        tau0_ms = 0.314 * estado.r0 / estado.vento_px_s * 1e3
        linhas += [
            f"turbulencia: r0 = {estado.r0:.1f} px, D/r0 = {2 * estado.raio / estado.r0:.1f}",
            f"inclinacao da tela: ({ix:+.2f}, {iy:+.2f}) mrad"
            + ("  REMOVIDA" if estado.sem_inclinacao else ""),
            f"vento {estado.vento_px_s:.0f} px/s ({'ligado' if estado.vento else 'parado'}), "
            f"tau0 ~ {tau0_ms:.0f} ms; laco a {estado.quadros_por_s:.0f} quadros/s",
        ]
        if estado.periodo > estado.r0 / 3:
            linhas.append("ATENCAO: periodo grosso para esse r0; diminua com [")
    return linhas


def previa(estado: Estado, ligados: np.ndarray, fase: np.ndarray,
           mascara: np.ndarray) -> np.ndarray:
    if estado.campo_distante:
        distante = painel_campo_distante(ligados, estado)
    else:
        distante = np.full((LADO_PAINEL, LADO_PAINEL, 3), 40, np.uint8)
        cv2.putText(distante, "desligado (f)", (8, LADO_PAINEL // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    topo = np.hstack([painel_fase(fase, mascara), distante])
    texto = np.full((24 * 10, topo.shape[1], 3), 20, np.uint8)
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
        elif letra == "h":
            estado.vento_px_s = max(1.0, estado.vento_px_s / 1.5)
        elif letra == "j":
            estado.vento_px_s = min(5000.0, estado.vento_px_s * 1.5)
        elif letra == "f":
            estado.campo_distante = not estado.campo_distante
        elif letra == "i":
            estado.sem_inclinacao = not estado.sem_inclinacao
        elif letra in "4682":
            estado.mira_x_mrad += {"4": -PASSO_MIRA_MRAD, "6": PASSO_MIRA_MRAD}.get(letra, 0.0)
            estado.mira_y_mrad += {"8": -PASSO_MIRA_MRAD, "2": PASSO_MIRA_MRAD}.get(letra, 0.0)
        elif letra == "5":
            estado.mira_x_mrad = estado.mira_y_mrad = 0.0
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
    parser.add_argument("--raio", type=int, default=None, help="raio inicial da abertura, px")
    parser.add_argument("--periodo", type=int, default=None, help="periodo inicial da grade, px")
    parser.add_argument("--angulo", type=float, default=None, help="angulo inicial da grade, graus")
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
    if args.raio:
        estado.raio = args.raio
    if args.periodo:
        estado.periodo = args.periodo
    if args.angulo is not None:
        estado.angulo = args.angulo
    print("Gerando a tela de turbulencia (uns segundos)...")
    tela = TelaKolmogorov(TAMANHO_TELA, 1.0, semente=estado.semente)
    print("Pronto. Use a janela de previa.")

    abrir_janela_dmd(m["x0"], m["y0"])
    cv2.namedWindow(JANELA_PREVIA, cv2.WINDOW_AUTOSIZE)
    anterior = ultima_previa = time.monotonic()
    try:
        while True:
            quadro, ligados, fase, mascara = montar_holograma(estado, largura, altura, tela)
            cv2.imshow(JANELA_DMD, quadro)
            # Com vento, a previa (a parte cara e so para os olhos) e redesenhada
            # poucas vezes por segundo; o DMD recebe todos os quadros.
            if not estado.vento or time.monotonic() - ultima_previa >= INTERVALO_PREVIA_S:
                cv2.imshow(JANELA_PREVIA, previa(estado, ligados, fase, mascara))
                ultima_previa = time.monotonic()
            # Com vento, o laco roda o mais rapido que der; parado, espera tecla.
            tecla = cv2.waitKeyEx(1 if estado.vento else 0)
            agora = time.monotonic()
            dt, anterior = agora - anterior, agora
            if estado.vento and estado.modo in ("t", "c"):
                # A tela anda pelo relogio, nao por volta do laco: a velocidade
                # em px/s vale igual num PC lento e num rapido. O teto de 0,5 s
                # evita um salto depois de uma espera longa por tecla.
                estado.deslocamento += estado.vento_px_s * min(dt, 0.5)
                if dt > 0:
                    estado.quadros_por_s = 0.9 * estado.quadros_por_s + 0.1 / dt
            if tecla != -1 and chr(tecla & 0xFF).lower() == "n":
                estado.semente += 1
                tela = TelaKolmogorov(TAMANHO_TELA, 1.0, semente=estado.semente)
                continue
            if not aplicar_tecla(estado, tecla, largura, altura):
                break
    finally:
        # Sair deixa o DMD preto: nenhum espelho ligado mandando luz pela sala.
        cv2.imshow(JANELA_DMD, np.zeros((altura, largura), np.uint8))
        cv2.waitKey(1)
        # O alinhamento custa caro: sai impresso o comando que volta a ele.
        print("\nPara voltar a esta configuracao:")
        print("  .venv\\Scripts\\python.exe diversos\\ferramentas\\dmd_holograma.py "
              f"--monitor {args.monitor} --centro {estado.cx} {estado.cy} "
              f"--raio {estado.raio} --periodo {estado.periodo} --angulo {estado.angulo:g}")
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
