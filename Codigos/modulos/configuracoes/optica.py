"""Escala optica do enlace: converte pixels em metros no alvo.

Objetivo: dar significado fisico aos numeros do tracker. Um erro de "1,5 px"
nao diz nada sobre o enlace; "5 cm a 7 km" diz.
Unidades: TUDO em metros e radianos. Erro de unidade aqui corrompe em silencio
toda conversao, por isso ``avisos()`` procura explicitamente por mm trocado
por m.
Hardware: nenhum. E so geometria.

Estes valores NAO substituem os limiares em pixels do tracker: aqueles estao
amarrados a resolucao do mount e do detector, que e o que limita a correcao.
A escala aqui serve para relatar e para decidir tolerancia com base no enlace.
"""

from __future__ import annotations


# ===== AJUSTE PARA A SUA OPTICA =====

# Tamanho do pixel do sensor, em metros. IDS U3-3680XCP-NIR: 2,2 um.
PIXEL_PITCH_M = 2.2e-6

# Distancia focal EFETIVA de todo o trem optico, em metros. Se houver relay,
# reducao ou barlow depois do telescopio, este numero nao e o do catalogo:
# EFL = F_telescopio * (f2 / f1) para um relay de duas lentes.
FOCAL_LENGTH_M = 0.7004

# Distancia ate o alvo do enlace, em metros.
LINK_DISTANCE_M = 7000.0


# ===== DERIVADOS =====


def escala_rad_por_px() -> float:
    """Angulo subtendido por um pixel, em radianos."""
    return PIXEL_PITCH_M / FOCAL_LENGTH_M


def arcsec_por_px() -> float:
    return escala_rad_por_px() * 206264.806


def metros_por_px_no_alvo(distancia_m: float | None = None) -> float:
    """Deslocamento no plano do alvo correspondente a um pixel."""
    distancia = LINK_DISTANCE_M if distancia_m is None else float(distancia_m)
    return escala_rad_por_px() * distancia


def px_para_metros(erro_px: float, distancia_m: float | None = None) -> float:
    return float(erro_px) * metros_por_px_no_alvo(distancia_m)


def metros_para_px(erro_m: float, distancia_m: float | None = None) -> float:
    escala = metros_por_px_no_alvo(distancia_m)
    return float(erro_m) / escala if escala > 0 else float("nan")


def avisos() -> list[str]:
    """Problemas fisicamente implausiveis, sem levantar excecao.

    Devolve texto para o operador em vez de abortar: quem chama decide. O caso
    que isto existe para pegar e milimetro escrito onde se esperava metro, que
    nao quebra nada e simplesmente multiplica toda a conversao por mil.
    """
    problemas: list[str] = []

    if not 0.01 <= FOCAL_LENGTH_M <= 20.0:
        problemas.append(
            f"FOCAL_LENGTH_M={FOCAL_LENGTH_M} esta fora da faixa plausivel em METROS "
            "(0,01 a 20). Se o valor veio em milimetros, divida por 1000 "
            "(por exemplo 700,4 mm -> 0,7004 m)."
        )
    if not 1e-7 < PIXEL_PITCH_M < 1e-3:
        problemas.append(
            f"PIXEL_PITCH_M={PIXEL_PITCH_M} nao parece um tamanho de pixel em metros "
            "(esperado entre 1e-7 e 1e-3; 2,2 um = 2.2e-6)."
        )
    if LINK_DISTANCE_M <= 0:
        problemas.append(f"LINK_DISTANCE_M={LINK_DISTANCE_M} precisa ser positiva.")
    else:
        metros_px = metros_por_px_no_alvo()
        if not 1e-4 < metros_px < 10.0:
            problemas.append(
                f"A escala derivada da {metros_px:.6g} m/px a {LINK_DISTANCE_M:.0f} m, "
                "o que e implausivel. Confira PIXEL_PITCH_M, FOCAL_LENGTH_M e "
                "LINK_DISTANCE_M antes de confiar em qualquer numero em metros."
            )
    return problemas


def resumo() -> str:
    return (
        f"escala={arcsec_por_px():.3f} arcsec/px | "
        f"{metros_por_px_no_alvo() * 100:.2f} cm/px a {LINK_DISTANCE_M / 1000:.1f} km "
        f"(pixel={PIXEL_PITCH_M * 1e6:.2f} um, EFL={FOCAL_LENGTH_M:.4f} m)"
    )
