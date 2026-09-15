"""O erro de apontamento visto de duas formas, na mesma sessao.

A figura do amanhecer mostra que o erro fica praticamente constante nas 10 h.
Essa e a conclusao certa, mas ela esconde a distribuicao: uma mediana parada
pode conviver com uma cauda que engorda. Aqui a mediana vem acompanhada da
faixa entre p10 e p90, e as tres fases da noite sao comparadas por ECDF.

A ECDF, e nao um histograma, porque a pergunta e "que fracao do tempo o erro
ficou abaixo de X". Num histograma isso exige somar barras de cabeca; na ECDF
e uma leitura direta no eixo, e as tres curvas podem ser comparadas sem que o
numero de caixas mude a resposta.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/gerar_grafico_erro.py         --sessao "Link UFF/resultados/tracker/sessoes/tracker_2026-09-15_00-09-46"         --saida "Arquivos/Tracker amanhecer"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

NAVY = "#12263A"
ORANGE = "#EA8C00"
SLIDE_DARK = "#193638"

CREPUSCULO_H = 5.6
NASCER_H = 6.05

# Trio validado nas seis checagens: todos os pares separam >= 15 em visao
# normal e >= 13 sob deuteranopia e protanopia.
BLOCOS = (
    ("noite", 0.0, CREPUSCULO_H, "#2563EB"),
    ("crepúsculo", CREPUSCULO_H, 7.0, "#EA8C00"),
    ("manhã", 7.0, 24.0, "#64748B"),
)
COLUNA = "distancia_px"


def configurar_estilo() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "axes.edgecolor": "#94A3B8",
        "axes.labelcolor": NAVY,
        "xtick.color": "#334155",
        "ytick.color": "#334155",
        "grid.color": "#CBD5E1",
        "grid.alpha": 0.45,
        "figure.facecolor": "white",
    })


def carregar(sessao: Path) -> pd.DataFrame:
    df = pd.read_csv(sessao / "telemetria.csv", low_memory=False)
    df = df[df["sinal_encontrado"] == 1].copy()
    hora = pd.to_datetime(df["data_hora"], format="mixed")
    df["hora_do_dia"] = hora.dt.hour + hora.dt.minute / 60 + hora.dt.second / 3600
    if df["hora_do_dia"].iloc[0] > df["hora_do_dia"].iloc[-1]:
        df.loc[df.index[df["hora_do_dia"] > 12], "hora_do_dia"] -= 24
    df[COLUNA] = pd.to_numeric(df[COLUNA], errors="coerce")
    return df.dropna(subset=[COLUNA])


def desenhar(df: pd.DataFrame, saida: Path, escuro: bool) -> Path:
    configurar_estilo()
    fig, (ax_t, ax_d) = plt.subplots(
        2, 1, figsize=(11, 8.2),
        gridspec_kw={"height_ratios": [1.5, 1], "hspace": 0.34,
                     "left": 0.09, "right": 0.97, "top": 0.93, "bottom": 0.08},
    )
    if escuro:
        fundo = fig.add_axes((0, 0, 1, 1), zorder=-100)
        fundo.set_facecolor(SLIDE_DARK)
        fundo.set_axis_off()
        fig.patch.set_facecolor(SLIDE_DARK)

    passos = max(3, int(120 * 5))
    rol = df[COLUNA].rolling(passos, min_periods=passos // 4, center=True)
    p10, p50, p90 = rol.quantile(0.1), rol.median(), rol.quantile(0.9)

    ax_t.fill_between(df["hora_do_dia"], p10, p90, color=NAVY, alpha=0.18, lw=0,
                      label="faixa p10 a p90")
    ax_t.plot(df["hora_do_dia"], p50, color=NAVY, lw=2.0, label="mediana móvel de 2 min")
    ax_t.axvspan(CREPUSCULO_H, NASCER_H, color=ORANGE, alpha=0.16, lw=0)
    ax_t.axvline(NASCER_H, color=ORANGE, lw=1.2, alpha=0.9)
    ax_t.text(NASCER_H, ax_t.get_ylim()[1], " nascer do sol", va="top", ha="left",
              fontsize=10, color=ORANGE, fontweight="bold")
    ax_t.set_title("Erro radial ao longo da sessão", loc="left")
    ax_t.set_ylabel("px")
    ax_t.set_xlabel("hora do dia")
    ax_t.set_ylim(bottom=0)
    ax_t.margins(x=0.01)
    ax_t.grid(True, axis="y", lw=0.6)
    ax_t.legend(loc="upper left", frameon=False, fontsize=10)
    ax_t.xaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{int(v) % 24:02d}:{int(round((v % 1) * 60)):02d}")
    )

    linhas = []
    for nome, inicio, fim, cor in BLOCOS:
        bloco = df[(df["hora_do_dia"] >= inicio) & (df["hora_do_dia"] < fim)][COLUNA]
        if bloco.empty:
            continue
        # Subamostra so para desenhar: a ECDF de 80 mil pontos e identica a de
        # 2 mil na largura desta figura, e o PNG fica dez vezes menor.
        ordenado = np.sort(bloco.to_numpy())
        idx = np.linspace(0, len(ordenado) - 1, min(len(ordenado), 2000)).astype(int)
        fracao = (idx + 1) / len(ordenado)
        mediana, p90b = np.median(ordenado), np.quantile(ordenado, 0.9)
        ax_d.plot(ordenado[idx], 100 * fracao, color=cor, lw=2.0,
                  label=f"{nome}: mediana {mediana:.2f} px, p90 {p90b:.2f} px")
        linhas.append((nome, cor, mediana, p90b))

    ax_d.axhline(50, color="#94A3B8", ls=":", lw=1.0)
    ax_d.axhline(90, color="#94A3B8", ls=":", lw=1.0)
    for nome, cor, mediana, p90b in linhas:
        for valor, altura in ((mediana, 50), (p90b, 90)):
            ax_d.plot([valor], [altura], "o", color=cor, ms=6,
                      markeredgecolor="white", markeredgewidth=1.4, zorder=5)
    ax_d.set_title("Distribuição do erro em cada fase", loc="left")
    ax_d.set_xlabel("erro radial (px)")
    ax_d.set_ylabel("% do tempo abaixo")
    ax_d.set_xlim(0, float(np.quantile(df[COLUNA], 0.995)))
    ax_d.set_ylim(0, 100)
    ax_d.grid(True, lw=0.6)
    ax_d.legend(loc="lower right", frameon=False, fontsize=10)

    for ax in (ax_t, ax_d):
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)
        if escuro:
            ax.set_facecolor("white")
            ax._left_title.set_color("white")
            ax.title.set_color("white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")
            ax.tick_params(colors="white")
            for spine in ax.spines.values():
                spine.set_color("white")

    saida.mkdir(parents=True, exist_ok=True)
    caminho = saida / ("erro_slide.png" if escuro else "erro.png")
    fig.savefig(caminho, dpi=170, facecolor=SLIDE_DARK if escuro else "white")
    plt.close(fig)
    return caminho


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessao", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    df = carregar(args.sessao)
    print(f"{len(df)} linhas com sinal")
    for escuro in (False, True):
        print("gravado:", desenhar(df, args.saida, escuro))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
