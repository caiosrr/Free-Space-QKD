"""Posicao e forma medidas na imagem media, nao na media dos CMs individuais."""
import cv2
import numpy as np


def measure_integrated_beacon(frames, centers, *, radius=48):
    """Integra em coordenadas fixas, com pesos iguais e sem realinhar frames.

    Centros individuais somente localizam uma abertura comum. O limiar baixo
    aplicado APOS a media preserva lobulos da luz; area nao vira deslocamento.
    """
    centers = np.asarray(centers, dtype=float)
    if not frames or centers.shape != (len(frames), 2) or not np.all(np.isfinite(centers)):
        raise ValueError('Frames/centros invalidos para imagem integrada.')
    shape = frames[0].shape
    if len(shape) != 2 or any(f.shape != shape for f in frames):
        raise ValueError('Dimensoes inconsistentes na imagem integrada.')
    x, y = np.median(centers, axis=0)
    h, w = shape
    x0, x1 = max(0, int(np.floor(x-radius))), min(w, int(np.ceil(x+radius+1)))
    y0, y1 = max(0, int(np.floor(y-radius))), min(h, int(np.ceil(y+radius+1)))
    if x1 <= x0 or y1 <= y0:
        raise ValueError('Abertura integrada fora da imagem.')
    mean = np.zeros((y1-y0, x1-x0), dtype=np.float64)
    for frame in frames:
        mean += frame[y0:y1, x0:x1]
    mean /= len(frames)  # Float evita overflow na soma de centenas de frames.
    yy, xx = np.indices(mean.shape, dtype=float)
    distance = np.hypot(xx+x0-x, yy+y0-y)
    annulus = (distance >= radius*.80) & (distance <= radius)
    aperture = distance < radius*.80
    if np.count_nonzero(annulus) < 20:
        raise RuntimeError('Fundo insuficiente ao redor da luz integrada.')
    background = float(np.median(mean[annulus]))
    noise = max(.05, float(1.4826*np.median(np.abs(mean[annulus]-background))))
    signal = cv2.GaussianBlur(mean-background, (3,3), .7)
    peak = float(np.max(signal[aperture]))
    if not np.isfinite(peak) or peak <= 3*noise:
        raise RuntimeError('Contraste insuficiente na imagem media.')
    threshold = max(3*noise, .02*peak)
    weights = np.where(aperture & (signal >= threshold), signal, 0.)
    total = float(weights.sum())
    if total <= 0:
        raise RuntimeError('Luz ausente na imagem media.')
    cx, cy = float((xx*weights).sum()/total), float((yy*weights).sum()/total)
    edge_fraction = float(weights[distance > radius*.70].sum()/total)
    if edge_fraction > .05:
        raise RuntimeError('Luz extensa/deslocada demais para a abertura integrada; possivel corte ou concorrente.')
    width = float(np.sqrt(((xx-cx)**2*weights).sum()/total))
    height = float(np.sqrt(((yy-cy)**2*weights).sum()/total))
    display = np.clip((mean-background)/peak*255, 0, 255).astype(np.uint8)
    return dict(center=[cx+x0, cy+y0], area_px=int(np.count_nonzero(weights)),
                rms_width_px=width, rms_height_px=height, background=background,
                background_noise=noise, peak_above_background=peak, threshold=threshold,
                edge_flux_fraction=edge_fraction, integrated_frames=len(frames),
                crop_origin=[x0,y0], preview=display)


def integrated_centroid(frames, centers, weights=None):
    # Assinatura compativel; similaridade seleciona frames mas nao pondera brilho.
    return tuple(measure_integrated_beacon(frames, centers)['center'])
