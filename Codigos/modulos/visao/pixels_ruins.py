"""Mascara de pixels defeituosos do sensor, medida com o feixe bloqueado.

Objetivo: impedir que um pixel quente entre no centro de massa.
Entradas: frames escuros para construir; frame bruto para corrigir.
Saidas: mascara booleana do sensor inteiro; frame com os defeitos substituidos.
Unidades: contagens do sensor. Nao toca camera nem mount.

Por que isso importa aqui: no tracker a autoexposicao trabalha perto de 1150 us,
onde o pico do beacon fica em torno de 15 contagens sobre um fundo de 2. Nesse
regime um unico pixel quente de algumas dezenas de contagens nao e ruido, e sim
a fonte mais brilhante do recorte: ele domina o centroide e desloca a medida.
Na calibracao, com exposicao 15x maior, o mesmo pixel e irrelevante.

A mascara e sempre do SENSOR INTEIRO. Quem trabalha com recorte pede a fatia
correspondente com ``fatiar``, informando a origem da ROI.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


HOT_SIGMA_PADRAO = 5.0
COLD_SIGMA_PADRAO = 5.0
# Piso absoluto do limiar, em contagens. Sem ele o criterio em sigma colapsa: no
# escuro quase todo pixel le o mesmo valor inteiro, o desvio robusto cai para
# ~0,05 contagem e "5 sigma" vira 0,25 contagem. Na pratica isso marcou 0,58% do
# sensor, cerca de 12x acima do que um CMOS cientifico costuma ter de defeito.
# Um pixel so atrapalha se puder competir com o beacon, que fica em torno de 20
# contagens: abaixo de alguns contagens de excesso ele e irrelevante.
PISO_CONTAGENS_PADRAO = 3.0
# Acima disso a mascara provavelmente esta medindo sinal, nao defeito.
FRACAO_MAXIMA_PLAUSIVEL = 0.02


def construir_mascara(
    frames_escuros: list[np.ndarray] | np.ndarray,
    *,
    hot_sigma: float = HOT_SIGMA_PADRAO,
    cold_sigma: float = COLD_SIGMA_PADRAO,
    piso_contagens: float = PISO_CONTAGENS_PADRAO,
) -> tuple[np.ndarray, dict]:
    """Marca pixels persistentemente claros ou escuros num frame escuro medio.

    Recebe varios frames justamente para que um raio cosmico ou um pixel que
    piscou uma vez nao vire defeito permanente: a media dilui o evento isolado,
    enquanto um pixel realmente quente sobrevive.

    Devolve ``(mascara, estatisticas)``. A mascara e ``True`` onde ha defeito.
    """
    if isinstance(frames_escuros, np.ndarray) and frames_escuros.ndim == 2:
        frames_escuros = [frames_escuros]
    frames = [np.asarray(f, dtype=np.float64) for f in frames_escuros]
    if not frames:
        raise ValueError("Nenhum frame escuro para construir a mascara.")
    forma = frames[0].shape
    if len(forma) != 2 or any(f.shape != forma for f in frames):
        raise ValueError("Os frames escuros precisam ser 2D e do mesmo tamanho.")

    medio = np.mean(frames, axis=0)
    mediana = float(np.median(medio))
    # Desvio robusto: com muitos pixels quentes o desvio padrao comum inflaria
    # e a propria mascara deixaria de marca-los.
    mad = float(np.median(np.abs(medio - mediana)))
    desvio = max(1.4826 * mad, 1e-6)

    # O limiar e o MAIOR entre o criterio estatistico e o piso absoluto. Num
    # frame escuro quantizado o desvio robusto colapsa e o criterio em sigma
    # sozinho marcaria ruido de arredondamento como defeito.
    excesso_quente = max(hot_sigma * desvio, float(piso_contagens))
    excesso_frio = max(cold_sigma * desvio, float(piso_contagens))
    quentes = medio > mediana + excesso_quente
    frios = medio < mediana - excesso_frio
    mascara = quentes | frios

    fracao = float(mascara.mean())
    estatisticas = {
        "frames_usados": len(frames),
        "mediana": mediana,
        "desvio_robusto": desvio,
        "maximo": float(medio.max()),
        "pixels_quentes": int(quentes.sum()),
        "pixels_frios": int(frios.sum()),
        "total_defeituosos": int(mascara.sum()),
        "fracao_do_sensor": fracao,
        "hot_sigma": float(hot_sigma),
        "cold_sigma": float(cold_sigma),
        "piso_contagens": float(piso_contagens),
        "limiar_quente": float(mediana + excesso_quente),
        "limiar_frio": float(mediana - excesso_frio),
        # Quantos pixels sobreviveriam a cada limiar, para escolher com dado em
        # vez de aceitar o padrao no escuro.
        "perfil": {
            f"+{excesso:g}": int(np.count_nonzero(medio > mediana + excesso))
            for excesso in (1, 2, 3, 5, 10, 20, 50)
        },
        "plausivel": bool(fracao <= FRACAO_MAXIMA_PLAUSIVEL),
    }
    return mascara, estatisticas


def fatiar(mascara: np.ndarray, origem_xy: tuple[int, int], forma: tuple[int, int]) -> np.ndarray:
    """Recorta a mascara do sensor para uma ROI de origem ``(x, y)``."""
    altura, largura = int(forma[0]), int(forma[1])
    x0, y0 = int(origem_xy[0]), int(origem_xy[1])
    recorte = np.zeros((altura, largura), dtype=bool)
    sy0, sx0 = max(0, y0), max(0, x0)
    sy1 = min(mascara.shape[0], y0 + altura)
    sx1 = min(mascara.shape[1], x0 + largura)
    if sy1 > sy0 and sx1 > sx0:
        recorte[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = mascara[sy0:sy1, sx0:sx1]
    return recorte


def corrigir(frame: np.ndarray, mascara: np.ndarray) -> np.ndarray:
    """Substitui cada pixel defeituoso pela mediana da vizinhanca 3x3.

    Vetorizado com ``cv2.medianBlur`` porque isto roda em todo frame do
    tracker: um laco Python sobre os defeitos custaria mais que a deteccao.
    Com defeitos esparsos, incluir o proprio pixel ruim na janela 3x3 nao muda
    a mediana de nove valores.
    """
    frame = np.asarray(frame)
    if frame.shape != mascara.shape:
        raise ValueError(
            f"Frame {frame.shape} e mascara {mascara.shape} com formas diferentes; "
            "fatie a mascara para a ROI antes de corrigir."
        )
    if not mascara.any():
        return frame.astype(np.float32, copy=False)

    trabalho = np.ascontiguousarray(frame, dtype=np.float32)
    suavizado = cv2.medianBlur(trabalho, 3)
    return np.where(mascara, suavizado, trabalho)


def salvar(caminho: str | Path, mascara: np.ndarray) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    np.save(caminho, np.asarray(mascara, dtype=bool))
    return caminho


def carregar(caminho: str | Path) -> np.ndarray | None:
    """Le a mascara salva; devolve ``None`` quando ela nao existe."""
    caminho = Path(caminho)
    if not caminho.exists():
        return None
    mascara = np.load(caminho)
    if mascara.dtype != bool or mascara.ndim != 2:
        raise ValueError(f"Mascara invalida em {caminho}: esperava bool 2D.")
    return mascara
