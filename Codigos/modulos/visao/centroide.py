"""Centro de massa de uma imagem media dentro de uma abertura circular.

Objetivo: uma unica definicao do estimador usado pela media temporal do tracker
e pela integracao curta da calibracao. Antes o mesmo algoritmo estava escrito
duas vezes, e ajustar um limiar exigia lembrar de ajustar o outro.
Entradas: imagem 2D ja combinada e a posicao esperada da luz, em pixels.
Saidas: ``(x, y)`` na imagem completa, ou ``None`` quando nao ha sinal util.
Unidades: pixels. Nao toca camera nem mount.

A abertura serve para que uma fachada distante ou outra ilha dentro da ROI nao
entre no somatorio; o pedestal e medido dentro dela e o limiar relativo e
aplicado DEPOIS da media, preservando lobulos fracos da propria luz.
"""

from __future__ import annotations

import numpy as np


def combinar_frames(
    frames: list[np.ndarray],
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """Media ponderada em ``float32``, sem realinhar frames.

    Somar em float evita estouro quando centenas de frames ``uint8`` entram na
    mesma janela.
    """
    if not frames:
        raise ValueError("Nenhum frame para combinar.")
    shape = frames[0].shape
    if len(shape) != 2 or any(frame.shape != shape for frame in frames):
        raise ValueError("Todos os frames combinados precisam ter o mesmo tamanho.")

    if weights is None:
        weights = np.ones(len(frames), dtype=float)
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (len(frames),) or not np.all(np.isfinite(weights)):
        raise ValueError("Pesos invalidos para a combinacao de frames.")
    weights = np.clip(weights, 0.05, 1.0)

    stacked = np.zeros(shape, dtype=np.float32)
    for frame, weight in zip(frames, weights):
        stacked += np.asarray(frame, dtype=np.float32) * float(weight)
    return stacked / float(np.sum(weights))


def centroide_em_abertura(
    image: np.ndarray,
    expected_x: float,
    expected_y: float,
    *,
    radius_px: float,
    threshold_percent: float,
) -> tuple[float, float] | None:
    """Centro de massa em torno de ``(expected_x, expected_y)``.

    Devolve ``None`` quando a janela fica vazia, o pico nao supera o pedestal
    ou o limiar relativo zera todos os pesos.
    """
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 2:
        raise ValueError("O centroide de abertura espera uma imagem 2D.")
    if not np.isfinite(expected_x) or not np.isfinite(expected_y):
        return None

    height, width = image.shape
    radius = float(radius_px)
    x0 = max(0, int(np.floor(expected_x - radius)))
    x1 = min(width, int(np.ceil(expected_x + radius + 1)))
    y0 = max(0, int(np.floor(expected_y - radius)))
    y1 = min(height, int(np.ceil(expected_y + radius + 1)))
    local = image[y0:y1, x0:x1].astype(np.float32, copy=True)
    if local.size == 0:
        return None

    yy, xx = np.indices(local.shape, dtype=np.float32)
    aperture = (
        (xx - (expected_x - x0)) ** 2 + (yy - (expected_y - y0)) ** 2
    ) <= radius**2
    pedestal = float(np.median(local[aperture])) if np.any(aperture) else 0.0
    weights = np.clip(local - pedestal, 0.0, None)
    weights[~aperture] = 0.0
    peak = float(weights.max())
    if peak <= 0.0:
        return None
    weights[weights < (peak * float(threshold_percent))] = 0.0
    total = float(weights.sum())
    if total <= 0.0:
        return None
    return (
        float(x0 + ((xx * weights).sum() / total)),
        float(y0 + ((yy * weights).sum() / total)),
    )
