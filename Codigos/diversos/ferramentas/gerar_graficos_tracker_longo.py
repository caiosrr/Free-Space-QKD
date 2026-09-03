"""Gera os graficos principais de uma sessao longa do tracker.

Exemplo, a partir da raiz do repositorio:

    python Codigos/diversos/ferramentas/gerar_graficos_tracker_longo.py \
        --sessao "Codigos/Link UFF/resultados/tracker/sessoes/tracker_..." \
        --saida "Arquivos/Tracker 6h/Graficos"
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.colors import LogNorm
from matplotlib.patches import Circle


NAVY = "#12263A"
BLUE = "#2563EB"
CYAN = "#0891B2"
GREEN = "#059669"
ORANGE = "#EA8C00"
RED = "#D1495B"
GRAY = "#64748B"
SLIDE_DARK = "#193638"


def configurar_estilo() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 16,
            "axes.titleweight": "bold",
            "axes.labelsize": 12,
            "axes.edgecolor": "#94A3B8",
            "axes.labelcolor": NAVY,
            "xtick.color": "#334155",
            "ytick.color": "#334155",
            "grid.color": "#CBD5E1",
            "grid.alpha": 0.45,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def fundo_escuro(fig: plt.Figure) -> None:
    background = fig.add_axes((0, 0, 1, 1), zorder=-100)
    background.set_facecolor(SLIDE_DARK)
    background.set_axis_off()
    fig.patch.set_facecolor(SLIDE_DARK)


def textos_externos_brancos(*axes: plt.Axes) -> None:
    for ax in axes:
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        ax._left_title.set_color("white")
        ax._right_title.set_color("white")
        ax.tick_params(axis="both", colors="white")
        for spine in ax.spines.values():
            spine.set_color("white")


def salvar(fig: plt.Figure, saida: Path, nome: str) -> Path:
    caminho = saida / f"{nome}.png"
    fig.savefig(caminho, dpi=180, bbox_inches=None, pad_inches=0, facecolor=SLIDE_DARK)
    plt.close(fig)
    return caminho


def rotulo_duracao(df: pd.DataFrame) -> tuple[str, str]:
    """Retorna um rotulo humano e outro seguro para nomes de arquivo."""
    total_minutes = max(1, int(float(df["tempo_decorrido_s"].iloc[-1]) / 60.0))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes:02d} min", f"{hours}h{minutes:02d}"
    if hours:
        return f"{hours} h", f"{hours}h"
    return f"{minutes} min", f"{minutes}min"


def carregar(sessao: Path) -> tuple[pd.DataFrame, dict]:
    csv_path = sessao / "telemetria.csv"
    resumo_path = sessao / "resumo.json"
    df = pd.read_csv(csv_path)
    resumo = json.loads(resumo_path.read_text(encoding="utf-8"))

    required = {
        "tempo_decorrido_s",
        "sinal_encontrado",
        "erro_x_filtrado_px",
        "erro_y_filtrado_px",
        "distancia_px",
        "velocidade_az_deg_s",
        "velocidade_alt_deg_s",
        "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg",
    }
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Colunas ausentes no CSV: {', '.join(missing)}")

    df["tempo_h"] = df["tempo_decorrido_s"] / 3600.0
    df["valido"] = df["sinal_encontrado"].eq(1)
    df["comando_ativo"] = np.hypot(
        df["velocidade_az_deg_s"], df["velocidade_alt_deg_s"]
    ).gt(1e-12)
    return df, resumo


def intervalos_sem_sinal(df: pd.DataFrame, minimo_s: float = 2.0):
    mask = ~df["valido"].to_numpy()
    time_s = df["tempo_decorrido_s"].to_numpy()
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])
    ends = np.flatnonzero(mask & ~np.r_[mask[1:], False])
    for start, end in zip(starts, ends, strict=False):
        if time_s[end] - time_s[start] >= minimo_s:
            yield time_s[start] / 3600.0, time_s[end] / 3600.0


def grafico_estabilidade(df: pd.DataFrame, resumo: dict, saida: Path) -> Path:
    hold_in = float(resumo["hold_enter_radius_px"])
    hold_out = float(resumo["hold_exit_radius_px"])
    valid = df["valido"]
    erro = df["distancia_px"].where(valid)

    dt = float(df["tempo_decorrido_s"].diff().median())
    rolling_window = max(5, int(round(120.0 / dt)))
    rolling = erro.rolling(rolling_window, center=True, min_periods=rolling_window // 5).median()
    duration_label, duration_slug = rotulo_duracao(df)

    fig, (ax, activity_ax) = plt.subplots(
        2,
        1,
        figsize=(12, 6.75),
        height_ratios=[4.5, 1.25],
        sharex=True,
        gridspec_kw={"hspace": 0.10},
    )
    fundo_escuro(fig)
    fig.suptitle(
        f"Estabilidade e atuação do tracker durante {duration_label}",
        x=0.07,
        y=0.97,
        ha="left",
        fontsize=22,
        fontweight="bold",
        color="white",
    )
    fig.text(
        0.07,
        0.91,
        f"sinal válido: {100.0 * valid.mean():.1f}%  |  mediana: {erro.median():.2f} px  |  P95: {erro.quantile(0.95):.2f} px",
        color="white",
        fontweight="bold",
    )

    for start, end in intervalos_sem_sinal(df):
        ax.axvspan(start, end, color=RED, alpha=0.10, lw=0)
    ax.plot(df["tempo_h"], erro, color=CYAN, alpha=0.55, lw=0.55, label="erro radial da média temporal")
    ax.plot(df["tempo_h"], rolling, color=NAVY, lw=2.0, label="mediana móvel de 2 min")
    ax.axhline(hold_in, color=GREEN, ls="--", lw=1.4, label=f"entrada em repouso: {hold_in:g} px")
    ax.axhline(hold_out, color=ORANGE, ls=":", lw=1.5, label=f"retomada da correção: {hold_out:g} px")
    ax.set_ylabel("Erro radial (px)")
    ax.set_ylim(0, max(3.4, float(erro.max()) * 1.12))
    ax.grid(True, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=2, frameon=False, loc="upper right")
    interval = df["tempo_decorrido_s"].shift(-1).sub(df["tempo_decorrido_s"]).fillna(dt).clip(0, 1)
    minute = np.floor(df["tempo_decorrido_s"] / 60.0).astype(int)
    correction = (interval * df["comando_ativo"].astype(float)).groupby(minute).sum()
    all_minutes = np.arange(int(math.ceil(df["tempo_decorrido_s"].iloc[-1] / 60.0)))
    correction_s = correction.reindex(all_minutes, fill_value=0.0).to_numpy()
    activity_ax.bar(all_minutes / 60.0, correction_s, width=0.013, color=ORANGE, alpha=0.90)
    activity_ax.set_ylabel("Comando por\nminuto (s)")
    activity_ax.set_xlabel("Tempo de sessão (h)")
    activity_ax.set_xlim(0, df["tempo_h"].iloc[-1])
    activity_ax.set_ylim(0, max(1.7, correction_s.max() * 1.18))
    activity_ax.grid(True, axis="y")
    activity_ax.spines[["top", "right"]].set_visible(False)
    textos_externos_brancos(ax, activity_ax)
    return salvar(fig, saida, f"02_estabilidade_sessao_{duration_slug}")


def grafico_mapa(df: pd.DataFrame, resumo: dict, saida: Path) -> Path:
    valid = df[df["valido"]]
    x = valid["erro_x_filtrado_px"]
    # O CSV preserva a convencao de imagem (Y positivo para baixo). Para leitura
    # humana, o grafico usa coordenadas cartesianas, com Y positivo para cima.
    y = -valid["erro_y_filtrado_px"]
    hold_in = float(resumo["hold_enter_radius_px"])
    hold_out = float(resumo["hold_exit_radius_px"])
    _, duration_slug = rotulo_duracao(df)
    extent = max(3.4, float(np.nanmax(np.abs(np.r_[x.to_numpy(), y.to_numpy()]))) * 1.12)

    fig = plt.figure(figsize=(7.6, 6.75))
    fundo_escuro(fig)
    ax = fig.add_axes((0.12, 0.12, 0.70, 0.76))
    fig.suptitle(
        "Distribuição da posição média do beacon",
        x=0.04,
        y=0.97,
        ha="left",
        fontsize=19,
        fontweight="bold",
        color="white",
    )
    density = ax.hexbin(
        x,
        y,
        gridsize=68,
        mincnt=1,
        cmap="Blues",
        norm=LogNorm(),
        extent=(-extent, extent, -extent, extent),
    )
    for radius, color, label in [
        (1.0, GREEN, "1 px"),
        (hold_in, BLUE, f"{hold_in:g} px"),
        (hold_out, ORANGE, f"{hold_out:g} px"),
    ]:
        ax.add_patch(Circle((0, 0), radius, fill=False, color=color, lw=1.6, ls="--"))
        ax.text(radius / math.sqrt(2), radius / math.sqrt(2), label, color=color, fontweight="bold")
    ax.scatter([0], [0], marker="+", s=230, color="#FF334F", lw=3.0, zorder=7, label="posição-alvo")
    ax.scatter([x.mean()], [y.mean()], marker="x", s=135, color="#FACC15", lw=2.8, zorder=8, label="posição média")
    ax.set(
        xlabel="Erro X em relação ao alvo (px)",
        ylabel="Erro vertical em relação ao alvo (px) · positivo para cima",
        xlim=(-extent, extent),
        ylim=(-extent, extent),
        aspect="equal",
    )
    ax.grid(True)
    ax.legend(frameon=False, loc="lower right")
    color_ax = fig.add_axes((0.845, 0.18, 0.025, 0.64))
    colorbar = fig.colorbar(density, cax=color_ax, label="densidade de registros válidos (escala log)")
    textos_externos_brancos(ax, color_ax)
    colorbar.ax.yaxis.label.set_color("white")
    return salvar(fig, saida, f"04_mapa_2d_distribuicao_{duration_slug}")


def grafico_mount(df: pd.DataFrame, resumo: dict, saida: Path) -> Path:
    smooth = (
        df.assign(bin_30s=(df["tempo_decorrido_s"] // 30).astype(int))
        .groupby("bin_30s", as_index=False)
        .median(numeric_only=True)
    )
    smooth["tempo_h"] = smooth["tempo_decorrido_s"] / 3600.0
    smooth["az_arcsec"] = smooth["deslocamento_az_desde_inicio_deg"] * 3600.0
    smooth["alt_arcsec"] = smooth["deslocamento_alt_desde_inicio_deg"] * 3600.0
    duration_label, duration_slug = rotulo_duracao(df)

    fig, (time_ax, path_ax) = plt.subplots(
        1, 2, figsize=(12, 6.75), gridspec_kw={"width_ratios": [1.45, 1], "wspace": 0.23}
    )
    fundo_escuro(fig)
    fig.suptitle(
        f"Atuação do mount durante {duration_label}",
        x=0.06,
        y=0.97,
        ha="left",
        fontsize=20,
        fontweight="bold",
        color="white",
    )
    time_ax.plot(smooth["tempo_h"], smooth["az_arcsec"], color=BLUE, lw=2.2, label="azimute")
    time_ax.plot(smooth["tempo_h"], smooth["alt_arcsec"], color=ORANGE, lw=2.2, label="altitude")
    time_ax.axhline(0, color=GRAY, lw=1)
    returned = bool(resumo.get("return_to_start", {}).get("success"))
    if returned:
        final_t = float(smooth["tempo_h"].iloc[-1])
        time_ax.plot([final_t, final_t], [smooth["alt_arcsec"].iloc[-1], 0], color=GREEN, ls="--", lw=1.7)
        time_ax.scatter([final_t], [0], color=GREEN, s=65, zorder=5, label="retorno seguro após a sessão")
    time_ax.set(xlabel="Tempo de sessão (h)", ylabel="Deslocamento desde o início (arcsec)")
    time_ax.set_title("Compensação acumulada", loc="left")
    time_ax.grid(True)
    time_ax.spines[["top", "right"]].set_visible(False)
    time_ax.legend(frameon=False, ncol=2)

    points = np.column_stack([smooth["az_arcsec"], smooth["alt_arcsec"]])
    points = np.vstack([[0.0, 0.0], points])
    path_times = np.r_[0.0, smooth["tempo_h"].to_numpy()]
    segments = np.stack([points[:-1], points[1:]], axis=1)
    collection = LineCollection(segments, cmap="viridis", norm=plt.Normalize(0, smooth["tempo_h"].iloc[-1]))
    collection.set_array(path_times[:-1])
    collection.set_linewidth(2.8)
    path_ax.add_collection(collection)
    path_ax.scatter(*points[0], s=95, color=GREEN, edgecolor="white", zorder=5, label="início")
    path_ax.scatter(*points[-1], s=95, color=ORANGE, edgecolor="white", zorder=5, label="fim do tracking")
    if returned:
        path_ax.plot([points[-1, 0], 0], [points[-1, 1], 0], color=GREEN, ls="--", lw=1.7, label="retorno seguro")
    xpad = max(1.5, np.ptp(points[:, 0]) * 0.2)
    ypad = max(1.5, np.ptp(points[:, 1]) * 0.2)
    path_ax.set_xlim(points[:, 0].min() - xpad, points[:, 0].max() + xpad)
    path_ax.set_ylim(points[:, 1].min() - ypad, points[:, 1].max() + ypad)
    path_ax.set(xlabel="Azimute (arcsec)", ylabel="Altitude (arcsec)")
    path_ax.set_title("Trajetória relativa do mount", loc="left")
    path_ax.grid(True)
    path_ax.spines[["top", "right"]].set_visible(False)
    path_ax.legend(frameon=False, fontsize=10)
    colorbar = fig.colorbar(collection, ax=path_ax, fraction=0.046, pad=0.04, label="tempo (h)")
    textos_externos_brancos(time_ax, path_ax, colorbar.ax)
    colorbar.ax.yaxis.label.set_color("white")
    return salvar(fig, saida, f"05_compensacao_mount_{duration_slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera os graficos 2, 4 e 5 de uma sessao longa do tracker.")
    parser.add_argument("--sessao", type=Path, required=True, help="pasta contendo telemetria.csv e resumo.json")
    parser.add_argument("--saida", type=Path, required=True, help="pasta em que os PNGs serao gravados")
    args = parser.parse_args()

    configurar_estilo()
    sessao = args.sessao.resolve()
    saida = args.saida.resolve()
    saida.mkdir(parents=True, exist_ok=True)
    df, resumo = carregar(sessao)
    caminhos = [
        grafico_estabilidade(df, resumo, saida),
        grafico_mapa(df, resumo, saida),
        grafico_mount(df, resumo, saida),
    ]
    print("Graficos gerados:")
    for caminho in caminhos:
        print(f"  - {caminho}")


if __name__ == "__main__":
    main()
