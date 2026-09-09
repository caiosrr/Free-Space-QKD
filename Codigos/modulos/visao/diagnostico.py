"""Diagnostico de camera e sinal antes de uma sessao, sem mover o mount.

Objetivo: responder em um minuto o que hoje exige uma sessao inteira: quanto
sinal existe, quanta escala do sensor esta em uso, qual a escala fisica e se o
centroide fecha.
Hardware: apenas a camera. NAO conecta nem comanda o mount.
Saidas: relatorio no terminal e, opcionalmente, uma mascara de pixels ruins.

Tambem mede o ``duty cycle`` de fotons, que e a fracao do periodo de aquisicao
em que o sensor esta de fato integrando. Uma exposicao muito curta dentro de um
periodo longo joga fotons fora sem ganhar taxa nenhuma.
"""

from __future__ import annotations

import time

import numpy as np

from modulos.configuracoes import optica
from modulos.visao import detector_ilhas as foco
from modulos.visao import pixels_ruins


def _estatisticas_frame(frame: np.ndarray) -> dict:
    valores = np.asarray(frame, dtype=np.float64)
    fundo = float(np.median(valores))
    pico = float(valores.max())
    # Piso de meia contagem: num frame quantizado e quase uniforme o MAD vai a
    # zero e o SNR estoura para valores absurdos (ja apareceu como 7,6e10).
    ruido = max(1.4826 * float(np.median(np.abs(valores - fundo))), 0.5)
    return {
        "fundo": fundo,
        "pico": pico,
        "amplitude": pico - fundo,
        "ruido_robusto": ruido,
        "snr": (pico - fundo) / ruido,
        "saturados": int(np.count_nonzero(valores >= 255)),
        "fracao_escala_usada": pico / 255.0,
    }


def medir(exposure_seconds: float, quantidade: int = 20) -> dict:
    """Captura alguns frames e resume sinal, taxa e estabilidade do centroide."""
    centros = []
    estatisticas = []
    sigmas = []
    inicio = time.perf_counter()
    for _ in range(max(3, int(quantidade))):
        frame = foco.capture_frame(exposure_seconds, min_raw_signal=0.0)
        bruto = foco.LAST_RAW_FRAME
        estatisticas.append(_estatisticas_frame(bruto if bruto is not None else frame))
        centro = foco.centro_massa(frame)
        selecionado = (foco.get_focus_debug().get("selected") or {})
        if centro is not None:
            centros.append((float(centro[0]), float(centro[1])))
            sigmas.append(float(selecionado.get("sigma_centroide_px", 0.0)))
    duracao = time.perf_counter() - inicio
    n = len(estatisticas)

    resultado = {
        "exposicao_us": exposure_seconds * 1e6,
        "frames": n,
        "taxa_hz": n / max(duracao, 1e-9),
        "periodo_us": (duracao / max(n, 1)) * 1e6,
        "detectados": len(centros),
    }
    resultado["duty_cycle"] = resultado["exposicao_us"] / max(resultado["periodo_us"], 1e-9)
    for chave in ("fundo", "pico", "amplitude", "ruido_robusto", "snr", "fracao_escala_usada"):
        resultado[chave] = float(np.median([e[chave] for e in estatisticas]))
    resultado["saturados"] = int(np.max([e["saturados"] for e in estatisticas]))
    if centros:
        pontos = np.asarray(centros, dtype=float)
        resultado["centro"] = pontos.mean(axis=0).tolist()
        resultado["dispersao_px"] = float(
            np.median(np.hypot(*(pontos - np.median(pontos, axis=0)).T))
        )
        resultado["sigma_centroide_px"] = float(np.median(sigmas)) if sigmas else float("nan")
    return resultado


def imprimir(resultado: dict) -> None:
    print(
        f"  exposicao={resultado['exposicao_us']:7.0f} us | "
        f"taxa={resultado['taxa_hz']:5.1f} Hz | "
        f"periodo={resultado['periodo_us']:7.0f} us | "
        f"duty={resultado['duty_cycle']:5.1%}"
    )
    print(
        f"     fundo={resultado['fundo']:6.1f} | pico={resultado['pico']:6.1f} | "
        f"amplitude={resultado['amplitude']:6.1f} | SNR={resultado['snr']:6.1f} | "
        f"escala usada={resultado['fracao_escala_usada']:5.1%} | "
        f"saturados={resultado['saturados']}"
    )
    if "centro" in resultado:
        print(
            f"     centro=({resultado['centro'][0]:.2f}, {resultado['centro'][1]:.2f}) | "
            f"dispersao={resultado['dispersao_px']:.3f} px | "
            f"sigma por frame={resultado['sigma_centroide_px']:.3f} px | "
            f"detectado em {resultado['detectados']}/{resultado['frames']} frames"
        )
    else:
        print("     ATENCAO: centroide nao encontrado em nenhum frame.")


def calibrar_pixels_ruins(exposure_seconds: float, quantidade: int = 20) -> dict:
    """Constroi a mascara a partir de frames escuros e a grava para as sessoes.

    NAO pergunta nada e nao segura a camera alem do necessario: enquanto este
    processo mantem a IDS aberta, nenhum outro programa consegue abri-la, e e
    justamente por outro programa que o operador aponta o telescopio. Quem
    chama confirma ANTES de conectar.
    """
    print("\n=== MASCARA DE PIXELS RUINS ===")
    foco.definir_mascara_pixels_ruins(None)
    print(f"Capturando {quantidade} frames escuros...")
    escuros = []
    for _ in range(quantidade):
        foco.capture_frame(exposure_seconds, min_raw_signal=0.0)
        if foco.LAST_RAW_FRAME is not None:
            escuros.append(np.array(foco.LAST_RAW_FRAME, copy=True))

    # LAST_RAW_FRAME ja passou pela rotacao de exibicao, mas a correcao e
    # aplicada no frame CRU do sensor, antes dela. A mascara precisa viver no
    # mesmo espaco em que sera usada: desfazemos a rotacao aqui. Como rot180 e
    # sua propria inversa, aplicar de novo devolve as coordenadas do sensor.
    if foco.ROTATE_IMAGE_180:
        escuros = [np.rot90(quadro, 2) for quadro in escuros]

    mascara, estatisticas = pixels_ruins.construir_mascara(escuros)
    caminho = pixels_ruins.salvar(_caminho_mascara(), mascara)

    print(f"  frames usados      : {estatisticas['frames_usados']}")
    print(f"  mediana / desvio   : {estatisticas['mediana']:.2f} / {estatisticas['desvio_robusto']:.2f}")
    print(f"  maximo no escuro   : {estatisticas['maximo']:.1f}")
    print(f"  quentes / frios    : {estatisticas['pixels_quentes']} / {estatisticas['pixels_frios']}")
    print(
        f"  total defeituosos  : {estatisticas['total_defeituosos']} "
        f"({estatisticas['fracao_do_sensor']:.4%} do sensor)"
    )
    if not estatisticas["plausivel"]:
        print(
            "  ATENCAO: fracao alta demais para defeitos de sensor. Provavelmente "
            "havia luz durante a captura; refaca no escuro."
        )
    print(
        f"  limiar quente      : {estatisticas['limiar_quente']:.2f} contagens "
        f"(piso absoluto {estatisticas['piso_contagens']:.1f})"
    )
    perfil = " | ".join(f"{k}: {v}" for k, v in estatisticas["perfil"].items())
    print(f"  pixels por excesso : {perfil}")
    print(f"  mascara salva em   : {caminho}")
    return estatisticas


def _caminho_mascara():
    from pathlib import Path
    import os

    base = os.environ.get("QKD_CENTER_OF_MASS_OUTPUT_DIR") or os.environ.get(
        "QKD_CAMERA_OUTPUT_DIR"
    )
    raiz = Path(base) if base else foco.ROOT_DIR / "resultados"
    return raiz / "mascara_pixels_ruins.npy"


def mascara_gravada():
    """Le a mascara do disco sem aplica-la. ``None`` quando nao existe.

    Quem usa ROI precisa aplicar depois, informando a origem do recorte.
    """
    return pixels_ruins.carregar(_caminho_mascara())


def carregar_mascara_se_existir(origem_xy=(0, 0)) -> bool:
    """Ativa a mascara gravada, quando houver. Devolve se aplicou."""
    mascara = pixels_ruins.carregar(_caminho_mascara())
    if mascara is None:
        return False
    foco.definir_mascara_pixels_ruins(mascara, origem_xy)
    return True
