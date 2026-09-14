"""Figuras da comparacao entre os regimes de controle do A/B.

Duas figuras, cada uma com um trabalho:

  06_ab_metricas   os tres bracos lado a lado em quatro metricas. Painel por
                   metrica, cada um com o SEU eixo: as unidades sao diferentes
                   (px, px, contagem/h, %) e forcar uma escala comum mentiria.
  07_ab_turbulencia o mecanismo. Erro por bloco contra a turbulencia daquele
                   bloco, um ponto por bloco. E aqui que se ve POR QUE o regime
                   lento ganha: a vantagem cresce com a turbulencia.

Uso, a partir da raiz do repositorio:

    python Codigos/diversos/ferramentas/gerar_graficos_ab_controle.py \
        --sessao "Codigos/Link UFF/resultados/tracker/sessoes/tracker_..." \
        --saida "Arquivos/Tracker AB .../Graficos"
"""

from __future__ import annotations

import argparse
import statistics as st
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SLIDE_DARK = "#193638"
TINTA = "#F1F5F9"
TINTA_2 = "#94A3B8"

# Paleta categorica em ordem FIXA, validada para visao normal (dE >= 15) e para
# deuteranopia e protanopia (dE >= 8) contra o fundo SLIDE_DARK.
REGIMES = [
    ("atual", "regime anterior", "#64748B"),
    ("lento_ganho_alto", "janela longa, ganho alto", "#EA8C00"),
    ("lento_ganho_baixo", "janela longa, ganho baixo", "#2563EB"),
]
TRANSICAO_S = 120.0


def estilo() -> None:
    plt.rcParams.update({
        "figure.facecolor": SLIDE_DARK,
        "axes.facecolor": "white",
        "font.size": 13,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
        "axes.edgecolor": "#CBD5E1",
        "grid.color": "#E2E8F0",
        "grid.linewidth": 0.8,
    })


def blocos(df: pd.DataFrame):
    """Trechos contiguos de mesmo regime, com a transicao inicial descartada."""
    regime = df["regime_controle"].to_numpy()
    tempo = df["tempo_decorrido_s"].to_numpy()
    cortes = np.flatnonzero(regime[1:] != regime[:-1]) + 1
    for ini, fim in zip(np.r_[0, cortes], np.r_[cortes, len(df)], strict=False):
        pedaco = df.iloc[ini:fim]
        util = pedaco[pedaco["tempo_decorrido_s"] >= tempo[ini] + TRANSICAO_S]
        if len(util) > 200:
            yield regime[ini], pedaco, util


def medir(sessao: Path) -> dict:
    df = pd.read_csv(sessao / "telemetria.csv", low_memory=False)
    dados = {chave: {"erro": [], "p90": [], "dc": [], "corr": [], "morto": [],
                     "turb": []} for chave, _, _ in REGIMES}
    for nome, pedaco, util in blocos(df):
        if nome not in dados:
            continue
        valido = util[util["sinal_encontrado"] == 1]
        erro = valido["distancia_px"].dropna()
        if erro.empty:
            continue
        d = dados[nome]
        d["erro"].append(float(erro.median()))
        d["p90"].append(float(erro.quantile(0.90)))
        d["dc"].append(float(np.hypot(valido["erro_x_filtrado_px"].mean(),
                                      valido["erro_y_filtrado_px"].mean())))
        horas = (pedaco["tempo_decorrido_s"].iloc[-1] - pedaco["tempo_decorrido_s"].iloc[0]) / 3600
        ciclos = pedaco["ciclos_correcao"].iloc[-1] - pedaco["ciclos_correcao"].iloc[0]
        d["corr"].append(float(ciclos / horas) if horas > 0 else np.nan)
        d["morto"].append(100.0 * pedaco["fase_correcao"].isin(["pulso", "acomodacao"]).mean())
        d["turb"].append(float(pedaco["desvio_padrao_2d_px"].median()))
    return dados


def figura_metricas(dados: dict, saida: Path) -> Path:
    metricas = [
        ("erro", "Erro radial mediano", "px", True),
        ("p90", "Erro radial P90", "px", True),
        ("dc", "Desvio sistemático", "px", True),
        ("corr", "Correções por hora", "", True),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 8.6))
    for ax, (chave, titulo, unidade, menor_melhor) in zip(
        axes.ravel(), metricas, strict=False
    ):
        valores = [st.mean(dados[n][chave]) for n, _, _ in REGIMES]
        cores = [c for _, _, c in REGIMES]
        y = np.arange(len(REGIMES))[::-1]
        ax.barh(y, valores, height=0.62, color=cores, zorder=3)
        for yi, v in zip(y, valores, strict=False):
            ax.text(v + max(valores) * 0.035, yi, f"{v:.2f}".replace(".", ","),
                    va="center", fontsize=13, color="#334155", fontweight="bold")
        ax.set_yticks(y, [rotulo for _, rotulo, _ in REGIMES], fontsize=11)
        ax.set_xlim(0, max(valores) * 1.35)
        rotulo = f"{titulo}  ({unidade})" if unidade else titulo
        ax.set_title(rotulo, loc="left", color="#0F172A")
        ax.grid(True, axis="x", zorder=0)
        ax.set_axisbelow(True)
        for lado in ("top", "right", "left"):
            ax.spines[lado].set_visible(False)
        if menor_melhor:
            ax.set_xlabel("menor é melhor", fontsize=10, color="#64748B")
    fig.suptitle("Três regimes de controle, mesma noite, blocos alternados de 10 min",
                 x=0.012, y=0.982, ha="left", fontsize=20, fontweight="bold", color=TINTA)
    fig.text(0.012, 0.918,
             "sessão de 3 h 21 min · 7 blocos por regime · 2 min de transição "
             "descartados em cada bloco",
             ha="left", fontsize=13, color=TINTA_2)
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.905], h_pad=3.2, w_pad=3.0)
    caminho = saida / "06_ab_metricas.png"
    fig.savefig(caminho, dpi=200, facecolor=SLIDE_DARK)
    plt.close(fig)
    return caminho


def figura_turbulencia(dados: dict, saida: Path) -> Path:
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    for nome, rotulo, cor in REGIMES:
        d = dados[nome]
        ax.scatter(d["turb"], d["erro"], s=150, color=cor, edgecolor="white",
                   linewidth=1.6, label=rotulo, zorder=3)
    ax.set_xlabel("Turbulência no bloco  (desvio padrão 2D do centroide, px)")
    ax.set_ylabel("Erro radial mediano do bloco  (px)")
    ax.grid(True, zorder=0)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.legend(frameon=False, loc="upper left", fontsize=12)
    fig.suptitle("A vantagem do regime lento cresce com a turbulência",
                 x=0.012, y=0.975, ha="left", fontsize=20, fontweight="bold", color=TINTA)
    fig.text(0.012, 0.928,
             "cada ponto é um bloco de 10 min · a janela de 120 s promedia "
             "turbulência, então rende mais quando há mais o que promediar",
             ha="left", fontsize=12.5, color=TINTA_2)
    fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.90])
    caminho = saida / "07_ab_turbulencia.png"
    fig.savefig(caminho, dpi=200, facecolor=SLIDE_DARK)
    plt.close(fig)
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessao", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    estilo()
    saida = args.saida.resolve()
    saida.mkdir(parents=True, exist_ok=True)
    dados = medir(args.sessao.resolve())
    for nome, _, _ in REGIMES:
        print(f"  {nome:20s} {len(dados[nome]['erro'])} blocos")
    for caminho in (figura_metricas(dados, saida), figura_turbulencia(dados, saida)):
        print(f"  - {caminho}")


if __name__ == "__main__":
    main()
