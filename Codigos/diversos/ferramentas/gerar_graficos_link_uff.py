"""Gera figuras de apresentacao a partir da telemetria final do Link UFF.

Uso, a partir da raiz do repositorio:

    python "Codigos/diversos/ferramentas/gerar_graficos_link_uff.py"

Por padrao, o CSV e as figuras ficam em ``Arquivos/Link UFF Final``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.colors import LogNorm
from matplotlib.patches import Circle


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "Arquivos" / "Link UFF Final" / "telemetria_link_uff_final.csv"
DEFAULT_OUTPUT = REPO_ROOT / "Arquivos" / "Link UFF Final" / "Graficos"

NAVY = "#12263A"
BLUE = "#2563EB"
CYAN = "#0891B2"
GREEN = "#059669"
ORANGE = "#EA8C00"
RED = "#D1495B"
PURPLE = "#7C3AED"
GRAY = "#64748B"
LIGHT_GRAY = "#CBD5E1"
PALE = "#F5F7FA"
SLIDE_DARK = "#193638"


def _configure_style() -> None:
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


def _pt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _add_slide_background(fig: plt.Figure) -> None:
    """Adiciona o fundo sólido usado nos slides, preservando os eixos em branco."""
    background = fig.add_axes((0, 0, 1, 1), zorder=-100)
    background.set_facecolor(SLIDE_DARK)
    background.set_axis_off()
    fig.patch.set_facecolor(SLIDE_DARK)


def _style_axes_on_gradient(*axes: plt.Axes) -> None:
    """Mantém legíveis os textos que ficam fora da área branca dos eixos."""
    for ax in axes:
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        ax._left_title.set_color("white")
        ax._right_title.set_color("white")
        ax.tick_params(axis="both", colors="white")
        for spine in ax.spines.values():
            spine.set_color("white")


def _save(fig: plt.Figure, output: Path, name: str, full_bleed: bool = False) -> Path:
    path = output / f"{name}.png"
    if full_bleed:
        fig.savefig(path, dpi=180, bbox_inches=None, pad_inches=0, facecolor=SLIDE_DARK)
    else:
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    required = {
        "tempo_decorrido_s",
        "estado",
        "sinal_encontrado",
        "erro_x_px",
        "erro_y_px",
        "distancia_px",
        "desvio_padrao_2d_px",
        "velocidade_az_deg_s",
        "velocidade_alt_deg_s",
        "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg",
        "loop_medicao_hz",
        "loop_controle_hz",
        "matriz_ativa",
        "zona_parada_ativa",
        "ilha_tocando_borda",
        "evento_seguranca",
    }
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Colunas ausentes no CSV: {', '.join(missing)}")

    df["tempo_min"] = df["tempo_decorrido_s"] / 60.0
    df["corrigindo"] = df["estado"].eq("RASTREANDO")
    df["comando_mdeg_s"] = (
        np.hypot(df["velocidade_az_deg_s"], df["velocidade_alt_deg_s"]) * 1000.0
    )
    return df


def _metrics(df: pd.DataFrame) -> dict[str, float | int]:
    errors = df["distancia_px"]
    return {
        "duration_s": float(df["tempo_decorrido_s"].iloc[-1]),
        "rows": int(len(df)),
        "signal_pct": float(100.0 * df["sinal_encontrado"].mean()),
        "rest_pct": float(100.0 * df["zona_parada_ativa"].mean()),
        "median_error_px": float(errors.median()),
        "rms_error_px": float(np.sqrt(np.mean(np.square(errors)))),
        "p90_error_px": float(errors.quantile(0.90)),
        "p95_error_px": float(errors.quantile(0.95)),
        "max_error_px": float(errors.max()),
        "within_6_pct": float(100.0 * errors.le(6.0).mean()),
        "within_10_pct": float(100.0 * errors.le(10.0).mean()),
        "measurement_hz": float(df.loc[df["tempo_decorrido_s"] > 10, "loop_medicao_hz"].median()),
        "control_hz": float(df.loc[df["tempo_decorrido_s"] > 10, "loop_controle_hz"].median()),
        "bias_x_px": float(df["erro_x_px"].mean()),
        "bias_y_px": float(df["erro_y_px"].mean()),
        "final_az_arcsec": float(df["deslocamento_az_desde_inicio_deg"].iloc[-1] * 3600.0),
        "final_alt_arcsec": float(df["deslocamento_alt_desde_inicio_deg"].iloc[-1] * 3600.0),
        "border_events": int(df["ilha_tocando_borda"].sum()),
        "safety_events": int(df["evento_seguranca"].fillna("").ne("").sum()),
    }


def _rolling_curves(df: pd.DataFrame, seconds: float = 60.0) -> tuple[pd.Series, pd.Series]:
    dt = float(df["tempo_decorrido_s"].diff().median())
    window = max(5, int(round(seconds / dt)))
    rolling = df["distancia_px"].rolling(window, center=True, min_periods=max(5, window // 5))
    return rolling.median(), rolling.quantile(0.90)


def _event_peaks(df: pd.DataFrame, count: int = 2, separation_s: float = 300.0) -> list[int]:
    selected: list[int] = []
    for index in df["distancia_px"].sort_values(ascending=False).index:
        time_s = float(df.at[index, "tempo_decorrido_s"])
        if all(abs(time_s - float(df.at[other, "tempo_decorrido_s"])) >= separation_s for other in selected):
            selected.append(int(index))
            if len(selected) == count:
                break
    return sorted(selected, key=lambda index: df.at[index, "tempo_decorrido_s"])


def _stable_recovery_seconds(df: pd.DataFrame, peak_index: int, radius_px: float = 6.0) -> float | None:
    peak_position = int(df.index.get_loc(peak_index))
    peak_time = float(df.at[peak_index, "tempo_decorrido_s"])
    for position in range(peak_position + 1, len(df) - 5):
        window = df.iloc[position : position + 5]
        if window["distancia_px"].le(radius_px).all():
            return float(window["tempo_decorrido_s"].iloc[0] - peak_time)
    return None


def _add_tracking_spans(ax: plt.Axes, x: np.ndarray, active: np.ndarray) -> None:
    if len(x) == 0:
        return
    starts = np.flatnonzero(active & ~np.r_[False, active[:-1]])
    ends = np.flatnonzero(active & ~np.r_[active[1:], False])
    for start, end in zip(starts, ends, strict=False):
        ax.axvspan(x[start], x[end], color=ORANGE, alpha=0.14, lw=0)


def plot_executive(df: pd.DataFrame, metrics: dict, output: Path, events: list[int]) -> Path:
    fig = plt.figure(figsize=(12, 6.75))
    _add_slide_background(fig)
    ax = fig.add_axes((0.09, 0.13, 0.88, 0.75))
    fig.suptitle("Erro absoluto do centro de massa", x=0.09, y=0.97, ha="left", fontsize=23, fontweight="bold", color="white")

    rolling_median, _ = _rolling_curves(df)
    ax.plot(
        df["tempo_min"], df["distancia_px"], color=CYAN, lw=0.85, alpha=0.58,
        label="erro absoluto instantâneo do centro de massa", zorder=2,
    )
    ax.plot(
        df["tempo_min"], rolling_median, color=NAVY, lw=2.3,
        label="mediana móvel (janela de 1 min)", zorder=3,
    )
    ax.axhline(6, color=ORANGE, lw=1.4, ls="--", label="referência de 6 px")
    ax.axhline(10, color=RED, lw=1.2, ls=":", label="referência de 10 px")
    for number, index in enumerate(events, start=1):
        x = float(df.at[index, "tempo_min"])
        y = float(df.at[index, "distancia_px"])
        ax.scatter([x], [y], s=85, color=RED, edgecolor="white", zorder=5)
        ax.annotate(f"Evento {number}\n{_pt(y)} px", (x, y), xytext=(8, 9), textcoords="offset points", color=RED, fontweight="bold")
    ax.set(
        xlabel="Tempo de sessão (min)",
        ylabel="Erro absoluto do centro de massa (px)",
        xlim=(0, df["tempo_min"].iloc[-1]),
        ylim=(0, max(12, metrics["max_error_px"] * 1.13)),
    )
    ax.grid(True, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=4, loc="upper right", frameon=False)
    _style_axes_on_gradient(ax)
    return _save(fig, output, "01_resumo_executivo", full_bleed=True)


def plot_overview(df: pd.DataFrame, metrics: dict, output: Path, events: list[int]) -> Path:
    fig, (ax, state_ax) = plt.subplots(2, 1, figsize=(12, 6.75), height_ratios=[4.5, 1.25], sharex=True, gridspec_kw={"hspace": 0.10})
    _add_slide_background(fig)
    ax.plot(df["tempo_min"], df["distancia_px"], color=CYAN, alpha=0.62, lw=0.75, label="erro absoluto instantâneo")
    ax.axhline(6, color=ORANGE, ls="--", lw=1.2, label="6 px")
    ax.axhline(10, color=RED, ls=":", lw=1.2, label="10 px")
    for number, index in enumerate(events, start=1):
        x = df.at[index, "tempo_min"]
        y = df.at[index, "distancia_px"]
        ax.scatter(x, y, s=70, color=RED, edgecolor="white", zorder=5)
        ax.annotate(f"{number}", (x, y), xytext=(0, 10), textcoords="offset points", ha="center", color=RED, fontweight="bold")
    ax.set_title("Erro absoluto e tempo de correção durante a sessão", loc="left")
    ax.set_ylabel("Erro absoluto do centro de massa (px)")
    ax.set_ylim(0, max(12, metrics["max_error_px"] * 1.12))
    ax.grid(True, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncol=3, frameon=False, loc="upper right")

    active = df["corrigindo"].to_numpy(dtype=bool)
    sample_period_s = float(df["tempo_decorrido_s"].diff().median())
    interval_s = df["tempo_decorrido_s"].shift(-1).sub(df["tempo_decorrido_s"]).fillna(sample_period_s)
    correction_bins = pd.DataFrame(
        {
            "minute": np.floor(df["tempo_min"]).astype(int),
            "correction_s": active.astype(float) * interval_s.to_numpy(),
        }
    ).groupby("minute")["correction_s"].sum()
    all_minutes = np.arange(int(math.ceil(df["tempo_min"].iloc[-1])))
    correction_s = correction_bins.reindex(all_minutes, fill_value=0.0).to_numpy()
    state_ax.bar(
        all_minutes + 0.5, correction_s, width=0.82,
        color=ORANGE, alpha=0.88, edgecolor="#C96F00", linewidth=0.45,
    )
    state_ax.set_ylabel("Correção por\nminuto (s)")
    state_ax.set_ylim(0, max(2.6, correction_s.max() * 1.18))
    state_ax.set_xlabel("Tempo de sessão (min)")
    state_ax.spines[["top", "right"]].set_visible(False)
    state_ax.grid(True, axis="y")
    _style_axes_on_gradient(ax, state_ax)
    return _save(fig, output, "02_estabilidade_sessao_completa", full_bleed=True)


def plot_events(df: pd.DataFrame, output: Path, events: list[int]) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(16, 9), height_ratios=[3.2, 1.25], sharex="col", gridspec_kw={"hspace": 0.10, "wspace": 0.16})
    fig.suptitle("Como o tracker reagiu aos dois eventos mais fortes", x=0.07, y=0.98, ha="left", fontsize=23, fontweight="bold", color=NAVY)
    fig.text(0.07, 0.93, "Laranja = controlador atuando • a telemetria não permite atribuir a causa física do distúrbio", color=GRAY, fontsize=12)

    for column, peak_index in enumerate(events):
        peak_time = float(df.at[peak_index, "tempo_decorrido_s"])
        half_window = 16.0 if column == 0 else 25.0
        view = df[df["tempo_decorrido_s"].between(peak_time - half_window, peak_time + half_window)].copy()
        relative_time = view["tempo_decorrido_s"].to_numpy() - peak_time
        active = view["corrigindo"].to_numpy(dtype=bool)
        peak_error = float(df.at[peak_index, "distancia_px"])
        recovery = _stable_recovery_seconds(df, peak_index)

        ax = axes[0, column]
        _add_tracking_spans(ax, relative_time, active)
        ax.plot(relative_time, view["distancia_px"], color=BLUE, lw=1.5)
        ax.scatter(relative_time, view["distancia_px"], color=BLUE, s=9, alpha=0.45)
        ax.axhline(6, color=ORANGE, ls="--", lw=1.2)
        ax.axvline(0, color=RED, ls=":", lw=1.2)
        ax.scatter([0], [peak_error], s=90, color=RED, edgecolor="white", zorder=5)
        recovery_text = f"primeiro retorno a ≤6 px por 5 registros em {_pt(recovery)} s" if recovery is not None else "sem retorno estável no recorte"
        ax.set_title(f"Evento {column + 1}: pico de {_pt(peak_error)} px", loc="left")
        ax.text(0.02, 0.93, recovery_text, transform=ax.transAxes, va="top", color=GREEN, fontweight="bold")
        ax.set_ylabel("Erro radial (px)")
        ax.set_ylim(bottom=0)
        ax.grid(True, axis="y")
        ax.spines[["top", "right"]].set_visible(False)

        command_ax = axes[1, column]
        _add_tracking_spans(command_ax, relative_time, active)
        command_ax.plot(relative_time, view["comando_mdeg_s"], color=PURPLE, lw=1.6)
        command_ax.fill_between(relative_time, 0, view["comando_mdeg_s"], color=PURPLE, alpha=0.12)
        command_ax.set(xlabel="Tempo relativo ao pico (s)", ylabel="Comando\n(mdeg/s)")
        command_ax.grid(True, axis="y")
        command_ax.spines[["top", "right"]].set_visible(False)

    fig.text(0.07, 0.015, "Os picos foram raros, não provocaram perda de identidade do alvo e foram corrigidos em escala de segundos.", color=NAVY, fontweight="bold", fontsize=12)
    return _save(fig, output, "03_eventos_fortes_e_recuperacao")


def plot_error_map(df: pd.DataFrame, metrics: dict, output: Path, events: list[int]) -> Path:
    fig = plt.figure(figsize=(7.6, 6.75))
    _add_slide_background(fig)
    map_ax = fig.add_axes((0.12, 0.12, 0.70, 0.76))
    fig.suptitle("Posição do centro de massa em torno do alvo", x=0.04, y=0.97, ha="left", fontsize=18, fontweight="bold", color="white")

    central = df[df["distancia_px"] <= 12]
    density = map_ax.hexbin(
        central["erro_x_px"], central["erro_y_px"], gridsize=72, mincnt=1,
        cmap="Blues", norm=LogNorm(), extent=(-12, 12, -12, 12),
    )
    for radius, color in [(4, GREEN), (6, ORANGE), (10, RED)]:
        map_ax.add_patch(Circle((0, 0), radius, fill=False, color=color, lw=1.7, ls="--" if radius != 10 else ":"))
        map_ax.text(radius / math.sqrt(2), radius / math.sqrt(2), f"{radius} px", color=color, fontsize=11, fontweight="bold")
    map_ax.scatter([0], [0], marker="+", s=230, color="#FF334F", lw=3.0, zorder=7, label="posição-alvo")
    map_ax.scatter([metrics["bias_x_px"]], [metrics["bias_y_px"]], marker="x", s=135, color="#FACC15", lw=2.8, zorder=8, label="posição média")

    map_ax.set(xlabel="Erro X em relação ao alvo (px)", ylabel="Erro Y em relação ao alvo (px)", xlim=(-12, 12), ylim=(-12, 12), aspect="equal")
    map_ax.grid(True)
    map_ax.legend(frameon=False, loc="lower right")
    color_ax = fig.add_axes((0.845, 0.18, 0.025, 0.64))
    colorbar = fig.colorbar(density, cax=color_ax, label="densidade de registros (escala log)")
    _style_axes_on_gradient(map_ax, color_ax)
    colorbar.ax.yaxis.label.set_color("white")
    return _save(fig, output, "04_mapa_2d_e_distribuicao", full_bleed=True)


def plot_mount_drift(df: pd.DataFrame, metrics: dict, output: Path) -> Path:
    smooth = df.assign(bin_30s=(df["tempo_decorrido_s"] // 30).astype(int)).groupby("bin_30s", as_index=False).median(numeric_only=True)
    smooth["tempo_min"] = smooth["tempo_decorrido_s"] / 60.0
    smooth["az_arcsec"] = smooth["deslocamento_az_desde_inicio_deg"] * 3600.0
    smooth["alt_arcsec"] = smooth["deslocamento_alt_desde_inicio_deg"] * 3600.0

    fig, (time_ax, path_ax) = plt.subplots(1, 2, figsize=(12, 6.75), gridspec_kw={"width_ratios": [1.45, 1], "wspace": 0.23})
    _add_slide_background(fig)

    time_ax.plot(smooth["tempo_min"], smooth["az_arcsec"], color=BLUE, lw=2.3, label="azimute")
    time_ax.plot(smooth["tempo_min"], smooth["alt_arcsec"], color=ORANGE, lw=2.3, label="altitude")
    time_ax.axhline(0, color=GRAY, lw=1)
    time_ax.scatter([smooth["tempo_min"].iloc[-1]], [smooth["az_arcsec"].iloc[-1]], color=BLUE, s=60)
    time_ax.scatter([smooth["tempo_min"].iloc[-1]], [smooth["alt_arcsec"].iloc[-1]], color=ORANGE, s=60)
    time_ax.set(xlabel="Tempo de sessão (min)", ylabel="Deslocamento desde o início (arcsec)")
    time_ax.set_title("Correção acumulada ao longo do tempo", loc="left")
    time_ax.grid(True)
    time_ax.spines[["top", "right"]].set_visible(False)
    time_ax.legend(frameon=False, ncol=2)

    # O primeiro bin representa a mediana dos primeiros 30 s e já pode conter
    # uma correção. Incluímos explicitamente a origem para a trajetória começar
    # na posição absoluta registrada no início da sessão.
    points = np.column_stack([smooth["az_arcsec"], smooth["alt_arcsec"]])
    points = np.vstack([[0.0, 0.0], points])
    path_times = np.r_[0.0, smooth["tempo_min"].to_numpy()]
    segments = np.stack([points[:-1], points[1:]], axis=1)
    collection = LineCollection(segments, cmap="viridis", norm=plt.Normalize(0, smooth["tempo_min"].iloc[-1]))
    collection.set_array(path_times[:-1])
    collection.set_linewidth(2.7)
    path_ax.add_collection(collection)
    path_ax.scatter(*points[0], s=100, color=GREEN, edgecolor="white", zorder=5, label="início")
    path_ax.scatter(*points[-1], s=100, color=RED, edgecolor="white", zorder=5, label="fim")
    padding = 3
    path_ax.set_xlim(points[:, 0].min() - padding, points[:, 0].max() + padding)
    path_ax.set_ylim(points[:, 1].min() - padding, points[:, 1].max() + padding)
    path_ax.set(xlabel="Azimute (arcsec)", ylabel="Altitude (arcsec)")
    path_ax.set_title("Trajetória relativa de compensação", loc="left")
    path_ax.grid(True)
    path_ax.spines[["top", "right"]].set_visible(False)
    path_ax.legend(frameon=False)
    colorbar = fig.colorbar(collection, ax=path_ax, fraction=0.046, pad=0.04, label="tempo (min)")
    _style_axes_on_gradient(time_ax, path_ax, colorbar.ax)
    colorbar.ax.yaxis.label.set_color("white")

    return _save(fig, output, "05_drift_compensado_pelo_mount", full_bleed=True)


def plot_stability_bins(df: pd.DataFrame, output: Path) -> Path:
    df = df.copy()
    df["bin_10min"] = (df["tempo_decorrido_s"] // 600).astype(int)
    grouped = df.groupby("bin_10min")
    labels = []
    values = []
    medians = []
    p90s = []
    correction_pct = []
    for number, group in grouped:
        start = int(number * 10)
        end = min(int((number + 1) * 10), int(math.ceil(df["tempo_min"].max())))
        labels.append(f"{start}–{end}")
        values.append(group["distancia_px"].to_numpy())
        medians.append(float(group["distancia_px"].median()))
        p90s.append(float(group["distancia_px"].quantile(0.90)))
        correction_pct.append(float(100.0 * group["corrigindo"].mean()))

    x = np.arange(1, len(labels) + 1)
    fig, (error_ax, activity_ax) = plt.subplots(2, 1, figsize=(16, 9), height_ratios=[3.4, 1.5], sharex=True, gridspec_kw={"hspace": 0.10})
    fig.suptitle("Estabilidade ao longo da sessão e margem de melhoria", x=0.07, y=0.98, ha="left", fontsize=23, fontweight="bold", color=NAVY)

    bp = error_ax.boxplot(values, tick_labels=labels, showfliers=False, patch_artist=True, widths=0.64)
    for box in bp["boxes"]:
        box.set(facecolor="#DBEAFE", edgecolor=BLUE, linewidth=1.2)
    for median in bp["medians"]:
        median.set(color=BLUE, linewidth=2)
    for whisker in bp["whiskers"]:
        whisker.set(color="#60A5FA")
    for cap in bp["caps"]:
        cap.set(color="#60A5FA")
    error_ax.plot(x, p90s, color=ORANGE, marker="o", lw=2.1, label="percentil 90")
    error_ax.plot(x, medians, color=BLUE, marker="o", lw=1.5, label="mediana")
    error_ax.axhline(6, color=RED, ls="--", lw=1.1, alpha=0.8)
    worst = int(np.argmax(p90s))
    error_ax.annotate("maior instabilidade\nentre 30 e 40 min", (x[worst], p90s[worst]), xytext=(22, 24), textcoords="offset points", color=RED, fontweight="bold", arrowprops={"arrowstyle": "->", "color": RED})
    error_ax.set_ylabel("Erro radial (px)")
    error_ax.set_title("Distribuição em blocos de 10 minutos", loc="left")
    error_ax.grid(True, axis="y")
    error_ax.spines[["top", "right"]].set_visible(False)
    error_ax.legend(frameon=False, ncol=2, loc="upper right")

    activity_ax.bar(x, correction_pct, color=ORANGE, alpha=0.80, width=0.62)
    for position, value in zip(x, correction_pct, strict=False):
        activity_ax.text(position, value + 0.08, f"{_pt(value)}%", ha="center", va="bottom", fontsize=9, color=NAVY)
    activity_ax.set(xlabel="Intervalo da sessão (min)", ylabel="Tempo em\ncorreção (%)", xticks=x, xticklabels=labels)
    activity_ax.set_ylim(0, max(correction_pct) * 1.35)
    activity_ax.grid(True, axis="y")
    activity_ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.07, 0.015, "Ponto para evolução: investigar por que a imagem ficou mais variável em alguns períodos, embora o controle tenha recuperado sem perder o alvo.", color=RED, fontweight="bold")
    return _save(fig, output, "06_estabilidade_por_periodo_e_margem")


def plot_variability(df: pd.DataFrame, output: Path) -> Path:
    quantile_bins = pd.qcut(df["desvio_padrao_2d_px"], 5, duplicates="drop")
    summary = df.groupby(quantile_bins, observed=True)["distancia_px"].agg(
        mediana="median", p90=lambda values: values.quantile(0.90)
    )
    labels = [f"{_pt(max(0.0, interval.left))}–{_pt(interval.right)}" for interval in summary.index]
    x = np.arange(len(summary))

    fig, (scatter_ax, bars_ax) = plt.subplots(1, 2, figsize=(16, 8.7), gridspec_kw={"wspace": 0.22})
    fig.suptitle("Onde ainda há espaço para melhorar", x=0.07, y=0.98, ha="left", fontsize=23, fontweight="bold", color=NAVY)

    sample = df.iloc[::5]
    scatter_ax.hexbin(sample["desvio_padrao_2d_px"], sample["distancia_px"], gridsize=48, mincnt=1, cmap="Purples", norm=LogNorm())
    scatter_ax.set(xlabel="Variabilidade recente da posição (px)", ylabel="Erro radial (px)", xlim=(0, 6), ylim=(0, 14))
    scatter_ax.set_title("Erro cresce quando a imagem fica mais variável", loc="left")
    scatter_ax.grid(True, alpha=0.25)
    scatter_ax.spines[["top", "right"]].set_visible(False)

    width = 0.36
    bars_ax.bar(x - width / 2, summary["mediana"], width, color=BLUE, label="mediana")
    bars_ax.bar(x + width / 2, summary["p90"], width, color=ORANGE, label="percentil 90")
    bars_ax.set_xticks(x, labels, rotation=18, ha="right")
    bars_ax.set(xlabel="Faixa de variabilidade (px)", ylabel="Erro radial (px)")
    bars_ax.set_title("Desempenho por nível de variabilidade", loc="left")
    bars_ax.grid(True, axis="y")
    bars_ax.spines[["top", "right"]].set_visible(False)
    bars_ax.legend(frameon=False)
    fig.text(0.07, 0.015, "Possíveis evoluções: filtragem adaptativa, diagnóstico da forma do spot e registro das condições atmosféricas. O CSV mostra associação, não determina a causa.", color=RED, fontweight="bold")
    return _save(fig, output, "07_variabilidade_e_oportunidades")


def generate(csv_path: Path, output: Path) -> list[Path]:
    _configure_style()
    output.mkdir(parents=True, exist_ok=True)
    df = _load(csv_path)
    metrics = _metrics(df)
    events = _event_peaks(df)

    paths = [
        plot_executive(df, metrics, output, events),
        plot_overview(df, metrics, output, events),
        plot_events(df, output, events),
        plot_error_map(df, metrics, output, events),
        plot_mount_drift(df, metrics, output),
        plot_stability_bins(df, output),
        plot_variability(df, output),
    ]

    summary_path = output / "metricas_resumidas.json"
    summary_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.append(summary_path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera gráficos da telemetria final do Link UFF.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="CSV de telemetria")
    parser.add_argument("--saida", type=Path, default=DEFAULT_OUTPUT, help="pasta de saída")
    args = parser.parse_args()

    paths = generate(args.csv.resolve(), args.saida.resolve())
    print("Gráficos gerados:")
    for path in paths:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
