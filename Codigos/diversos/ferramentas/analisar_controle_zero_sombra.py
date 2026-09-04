"""Analisa quando um estimador lento encontraria erro persistente no tracker.

Esta ferramenta nao simula a resposta fechada do mount. Ela marca oportunidades
em que uma futura malha buscando zero teria evidencia suficiente para considerar
uma correcao. Uma correcao real mudaria os dados seguintes, portanto as regioes
marcadas nao representam duracao nem numero previsto de comandos.
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


WINDOWS_S = (5, 10, 15, 20, 30)
THRESHOLDS_PX = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
REFERENCE_WINDOW_S = 15
REFERENCE_THRESHOLD_PX = 1.0
MIN_COVERAGE = 0.80
PERSISTENCE_S = 10

NAVY = "#12263A"
CYAN = "#0891B2"
BLUE = "#2563EB"
ORANGE = "#EA8C00"
RED = "#D1495B"
GRAY = "#64748B"
GREEN = "#059669"
SLIDE_DARK = "#193638"


def _configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 16,
            "axes.titleweight": "bold",
            "axes.labelsize": 12,
            "grid.color": "#CBD5E1",
            "grid.alpha": 0.45,
        }
    )


def _add_background(fig: plt.Figure) -> None:
    background = fig.add_axes((0, 0, 1, 1), zorder=-100)
    background.set_facecolor(SLIDE_DARK)
    background.set_axis_off()
    fig.patch.set_facecolor(SLIDE_DARK)


def _style_external_text(*axes: plt.Axes) -> None:
    for ax in axes:
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        ax._left_title.set_color("white")
        ax._right_title.set_color("white")
        ax.tick_params(axis="both", colors="white")
        for spine in ax.spines.values():
            spine.set_color("white")


def _load_one_second(csv_path: Path) -> pd.DataFrame:
    raw = pd.read_csv(csv_path)
    required = {
        "tempo_decorrido_s",
        "sinal_encontrado",
        "qualidade_optica",
        "erro_x_filtrado_px",
        "erro_y_filtrado_px",
        "velocidade_az_deg_s",
        "velocidade_alt_deg_s",
    }
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"Colunas ausentes no CSV: {', '.join(missing)}")

    raw["segundo"] = np.floor(raw["tempo_decorrido_s"]).astype(int)
    raw["confiavel"] = raw["sinal_encontrado"].eq(1) & raw["qualidade_optica"].eq("normal")
    raw["x_confiavel"] = raw["erro_x_filtrado_px"].where(raw["confiavel"])
    # O CSV usa Y positivo para baixo; a analise apresentada usa Y positivo para cima.
    raw["y_visual_confiavel"] = (-raw["erro_y_filtrado_px"]).where(raw["confiavel"])
    raw["comando_ativo"] = np.hypot(
        raw["velocidade_az_deg_s"], raw["velocidade_alt_deg_s"]
    ).gt(1e-12)

    duration_s = int(raw["segundo"].max())
    second = (
        raw.groupby("segundo")
        .agg(
            x_observado_px=("x_confiavel", "median"),
            y_visual_observado_px=("y_visual_confiavel", "median"),
            fracao_confiavel=("confiavel", "mean"),
            comando_antigo_ativo=("comando_ativo", "max"),
        )
        .reindex(range(duration_s + 1))
    )
    second.index.name = "tempo_s"
    second["tempo_h"] = second.index.to_numpy() / 3600.0
    second["erro_observado_radial_px"] = np.hypot(
        second["x_observado_px"], second["y_visual_observado_px"]
    )
    return second


def _slow_estimate(second: pd.DataFrame, window_s: int) -> pd.DataFrame:
    minimum = int(math.ceil(MIN_COVERAGE * window_s))
    result = pd.DataFrame(index=second.index)
    result["x_px"] = second["x_observado_px"].rolling(window_s, min_periods=minimum).median()
    result["y_px"] = second["y_visual_observado_px"].rolling(window_s, min_periods=minimum).median()
    result["radial_px"] = np.hypot(result["x_px"], result["y_px"])
    result["confiavel"] = result["radial_px"].notna()
    return result


def _confirmed(mask: pd.Series, persistence_s: int) -> pd.Series:
    values = mask.fillna(False).to_numpy(dtype=bool)
    confirmed = np.zeros(len(values), dtype=bool)
    run_start = None
    for index, value in enumerate(values):
        if value and run_start is None:
            run_start = index
        elif not value:
            run_start = None
        if run_start is not None and (index - run_start + 1) >= persistence_s:
            confirmed[index] = True
    return pd.Series(confirmed, index=mask.index)


def _episodes(mask: pd.Series) -> list[tuple[int, int, int]]:
    values = mask.to_numpy(dtype=bool)
    starts = np.flatnonzero(values & ~np.r_[False, values[:-1]])
    ends = np.flatnonzero(values & ~np.r_[values[1:], False])
    return [(int(start), int(end), int(end - start + 1)) for start, end in zip(starts, ends, strict=False)]


def _sensitivity(second: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    for window_s in WINDOWS_S:
        estimate = _slow_estimate(second, window_s)
        for threshold_px in THRESHOLDS_PX:
            raw = estimate["confiavel"] & estimate["radial_px"].ge(threshold_px)
            confirmed = _confirmed(raw, PERSISTENCE_S)
            episodes = _episodes(confirmed)
            rows.append(
                {
                    "janela_s": window_s,
                    "limiar_px": threshold_px,
                    "cobertura_confiavel_pct": 100.0 * estimate["confiavel"].mean(),
                    "tempo_acima_limiar_pct": 100.0 * raw.mean(),
                    "tempo_com_persistencia_pct": 100.0 * confirmed.mean(),
                    "episodios_confirmados": len(episodes),
                    "maior_episodio_min": max((item[2] for item in episodes), default=0) / 60.0,
                }
            )
    table = pd.DataFrame(rows)
    reference = table[
        table["janela_s"].eq(REFERENCE_WINDOW_S)
        & table["limiar_px"].eq(REFERENCE_THRESHOLD_PX)
    ].iloc[0]
    summary = {
        "interpretation": (
            "Observador sombra: identifica erro lento persistente, sem prever comandos "
            "ou a trajetoria fechada que resultaria deles."
        ),
        "reference_window_s": REFERENCE_WINDOW_S,
        "reference_threshold_px": REFERENCE_THRESHOLD_PX,
        "persistence_s": PERSISTENCE_S,
        "minimum_window_coverage": MIN_COVERAGE,
        "reference_reliable_coverage_pct": float(reference["cobertura_confiavel_pct"]),
        "reference_time_above_threshold_pct": float(reference["tempo_acima_limiar_pct"]),
        "reference_time_confirmed_pct": float(reference["tempo_com_persistencia_pct"]),
        "reference_confirmed_episodes": int(reference["episodios_confirmados"]),
        "warning": (
            "Os episodios nao equivalem ao numero de correcoes: uma correcao real "
            "alteraria as amostras posteriores."
        ),
    }
    return table, summary


def _merged_episodes(mask: pd.Series, merge_gap_s: int = 30) -> list[tuple[int, int, int]]:
    episodes = _episodes(mask)
    if not episodes:
        return []
    merged = [list(episodes[0])]
    for start, end, _ in episodes[1:]:
        if start - merged[-1][1] - 1 <= merge_gap_s:
            merged[-1][1] = end
            merged[-1][2] = end - merged[-1][0] + 1
        else:
            merged.append([start, end, end - start + 1])
    return [tuple(item) for item in merged]


def _opportunity_band(ax: plt.Axes, mask: pd.Series, time_h: pd.Series, y: float) -> None:
    episodes = _merged_episodes(mask)
    for number, (start, end, _) in enumerate(episodes):
        ax.plot(
            [time_h.iloc[start], time_h.iloc[end]],
            [y, y],
            color=ORANGE,
            lw=5,
            solid_capstyle="butt",
            alpha=0.82,
            label="evidência persistente para correção lenta" if number == 0 else None,
        )


def _plot(second: pd.DataFrame, estimate: pd.DataFrame, confirmed: pd.Series, summary: dict, output: Path) -> Path:
    fig, (radial_ax, components_ax) = plt.subplots(
        2,
        1,
        figsize=(12, 6.75),
        height_ratios=[3.1, 1.7],
        sharex=True,
        gridspec_kw={"hspace": 0.10},
    )
    _add_background(fig)
    fig.suptitle(
        "Onde uma malha lenta encontraria erro persistente",
        x=0.07,
        y=0.97,
        ha="left",
        fontsize=22,
        fontweight="bold",
        color="white",
    )
    fig.subplots_adjust(top=0.80, bottom=0.12, left=0.10, right=0.98)
    fig.text(
        0.07,
        0.905,
        "Análise sombra: faixas laranja indicam oportunidade de correção, não comandos previstos",
        color="white",
        fontsize=11,
    )
    fig.text(
        0.07,
        0.865,
        f"estimativa confiável em {summary['reference_reliable_coverage_pct']:.1f}% da sessão  |  "
        f"erro lento acima de 1 px em {summary['reference_time_above_threshold_pct']:.1f}%",
        color="white",
        fontweight="bold",
        fontsize=10,
    )

    radial_ax.plot(
        second["tempo_h"],
        second["erro_observado_radial_px"],
        color=CYAN,
        lw=0.55,
        alpha=0.38,
        label="erro observado (média temporal de 2 s)",
    )
    radial_ax.plot(
        second["tempo_h"],
        estimate["radial_px"],
        color=NAVY,
        lw=2.0,
        label=f"estimativa lenta robusta ({REFERENCE_WINDOW_S} s)",
    )
    radial_ax.axhline(
        REFERENCE_THRESHOLD_PX,
        color=ORANGE,
        ls="--",
        lw=1.4,
        label=f"evidência a partir de {REFERENCE_THRESHOLD_PX:g} px",
    )
    radial_ax.set_ylabel("Erro radial (px)")
    radial_max = max(3.4, float(second["erro_observado_radial_px"].max()) * 1.1)
    radial_ax.set_ylim(0, radial_max)
    _opportunity_band(radial_ax, confirmed, second["tempo_h"], radial_max * 0.975)
    radial_ax.grid(True, axis="y")
    radial_ax.spines[["top", "right"]].set_visible(False)
    radial_ax.legend(
        frameon=True, facecolor="white", edgecolor="none", framealpha=0.90,
        ncol=3, loc="upper right",
    )
    components_ax.plot(second["tempo_h"], estimate["x_px"], color=BLUE, lw=1.25, label="X lento")
    components_ax.plot(second["tempo_h"], estimate["y_px"], color=GREEN, lw=1.25, label="Y lento · positivo para cima")
    command_episodes = _episodes(second["comando_antigo_ativo"].fillna(False))
    for number, (start, _, _) in enumerate(command_episodes):
        command_time = second["tempo_h"].iloc[start]
        components_ax.axvline(
            command_time,
            color=RED,
            lw=0.8,
            alpha=0.45,
            label="atuação registrada do tracker antigo" if number == 0 else None,
        )
    components_ax.axhline(0, color=GRAY, lw=1)
    components_ax.set(xlabel="Tempo de sessão (h)", ylabel="Componentes lentas (px)")
    components_ax.grid(True, axis="y")
    components_ax.spines[["top", "right"]].set_visible(False)
    components_ax.legend(
        frameon=True, facecolor="white", edgecolor="none", framealpha=0.90,
        ncol=3, loc="upper right",
    )
    _style_external_text(radial_ax, components_ax)

    path = output / "06_oportunidades_controle_zero_sombra.png"
    fig.savefig(path, dpi=180, bbox_inches=None, pad_inches=0, facecolor=SLIDE_DARK)
    plt.close(fig)
    return path


def generate(session: Path, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    second = _load_one_second(session / "telemetria.csv")
    estimate = _slow_estimate(second, REFERENCE_WINDOW_S)
    raw_opportunity = estimate["confiavel"] & estimate["radial_px"].ge(REFERENCE_THRESHOLD_PX)
    confirmed = _confirmed(raw_opportunity, PERSISTENCE_S)
    sensitivity, summary = _sensitivity(second)

    shadow = second.copy()
    shadow[f"drift_x_{REFERENCE_WINDOW_S}s_px"] = estimate["x_px"]
    shadow[f"drift_y_visual_{REFERENCE_WINDOW_S}s_px"] = estimate["y_px"]
    shadow[f"drift_radial_{REFERENCE_WINDOW_S}s_px"] = estimate["radial_px"]
    shadow["estimativa_confiavel"] = estimate["confiavel"].astype(int)
    shadow["oportunidade_persistente"] = confirmed.astype(int)

    shadow_path = output / f"observador_sombra_{REFERENCE_WINDOW_S}s.csv"
    sensitivity_path = output / "sensibilidade_janelas_limiares.csv"
    summary_path = output / "resumo_observador_sombra.json"
    readme_path = output / "LEIA_ME.md"
    shadow.reset_index().to_csv(shadow_path, index=False)
    sensitivity.to_csv(sensitivity_path, index=False)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    readme_path.write_text(
        "# Observador sombra do controle buscando zero\n\n"
        "Esta analise aplica uma mediana lenta aos dados ja medidos pelo tracker. "
        "Ela marca quando havia erro medio persistente, mas nao simula a resposta "
        "fechada do mount. As faixas marcadas nao sao comandos previstos.\n\n"
        f"Configuracao de referencia: janela de {REFERENCE_WINDOW_S} s, limiar de "
        f"{REFERENCE_THRESHOLD_PX:g} px, persistencia de {PERSISTENCE_S} s e pelo "
        f"menos {100 * MIN_COVERAGE:.0f}% de amostras confiaveis.\n\n"
        f"- estimativa disponivel em {summary['reference_reliable_coverage_pct']:.1f}% da sessao;\n"
        f"- erro lento acima de 1 px em {summary['reference_time_above_threshold_pct']:.1f}% da sessao;\n"
        f"- evidencia confirmada apos persistencia em {summary['reference_time_confirmed_pct']:.1f}% da sessao.\n\n"
        "O resultado mostra margem para uma malha lenta buscar melhor o zero. Ele "
        "nao informa quanto o mount trabalharia, pois cada micropulso alteraria as "
        "medicoes posteriores. O passo seguro seguinte e executar o mesmo observador "
        "ao vivo, ainda sem enviar comandos.\n\n"
        "Arquivos:\n\n"
        "- `06_oportunidades_controle_zero_sombra.png`: leitura visual;\n"
        "- `observador_sombra_15s.csv`: serie temporal em 1 Hz;\n"
        "- `sensibilidade_janelas_limiares.csv`: comparacao de parametros;\n"
        "- `resumo_observador_sombra.json`: configuracao e numeros principais.\n",
        encoding="utf-8",
    )
    plot_path = _plot(second, estimate, confirmed, summary, output)
    return [plot_path, shadow_path, sensitivity_path, summary_path, readme_path]


def main() -> None:
    parser = argparse.ArgumentParser(description="Analisa oportunidades para uma futura malha lenta buscando zero.")
    parser.add_argument("--sessao", type=Path, required=True, help="pasta da sessao do tracker")
    parser.add_argument("--saida", type=Path, required=True, help="pasta de saida")
    args = parser.parse_args()
    _configure_style()
    paths = generate(args.sessao.resolve(), args.saida.resolve())
    print("Analise sombra gerada:")
    for path in paths:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
