"""Figura do amanhecer: o que o ceu clareando fez com a medida e com o controle.

Quatro paineis empilhados sobre o mesmo eixo de horario, e nao quatro curvas num
eixo so. Exposicao em us, fundo em contagens, CNR adimensional e erro em pixels
nao compartilham escala: sobrepor tudo exigiria dois eixos y, que e a forma mais
comum de fazer um grafico mentir sobre correlacao.

Cada painel tem UMA serie, entao a identidade vem do titulo do painel e nao da
cor. Isso dispensa legenda e remove o problema de separar azul de ciano para
quem tem deuteranopia -- as duas cores que a paleta da casa traz mais proximas.
A cor fica reservada para o que carrega significado: a faixa de CNR e o
crepusculo.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/gerar_grafico_amanhecer.py         --sessao "Link UFF/resultados/tracker/sessoes/tracker_2026-09-15_00-09-46"         --saida "Arquivos/Tracker amanhecer"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

NAVY = "#12263A"
GREEN = "#059669"
ORANGE = "#EA8C00"
SLIDE_DARK = "#193638"

# Crepusculo civil e nascer do sol no Rio em meados de setembro.
CREPUSCULO_H = 5.6
NASCER_H = 6.05

# coluna, titulo, unidade, faixa alvo, escala log
PAINEIS = (
    # Log na exposicao: a busca por sinal chega a 44000 us e, em escala linear,
    # esse pico unico achata a descida de 3000 para 750 us, que e o que a figura
    # existe para mostrar. Em log a descida vira uma reta e o pico continua la.
    ("exposicao_us", "Exposição", "µs", None, True),
    ("fundo_local_autoexposicao", "Fundo local do céu", "contagens", None, False),
    ("cnr_autoexposicao", "CNR", "", (8.0, 16.0), False),
    ("raio_controle_px", "Erro que o controlador enxerga", "px", None, False),
    ("correcoes_por_hora", "Correções por hora", "correções/h", None, False),
)

# Janela para a taxa de correcao. Com 45 correcoes por hora, uma janela de 15
# min pega umas 11: curta o bastante para mostrar o pico do crepusculo, longa o
# bastante para a contagem nao virar ruido de Poisson.
JANELA_TAXA_MIN = 15.0


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
    # A sessao comeca antes da meia-noite virar; sem isto as horas voltam a zero.
    if df["hora_do_dia"].iloc[0] > df["hora_do_dia"].iloc[-1]:
        df.loc[df.index[df["hora_do_dia"] > 12], "hora_do_dia"] -= 24
    df["correcoes_por_hora"] = taxa_de_correcao(df)
    return df


def taxa_de_correcao(df: pd.DataFrame) -> pd.Series:
    """Correcoes por hora, de uma contagem acumulada que so cresce.

    ``ciclos_correcao`` conta desde o inicio da sessao; a taxa e a diferenca
    dentro de uma janela deslizante, dividida pelo tempo que a janela cobre.
    """
    ciclos = pd.to_numeric(df["ciclos_correcao"], errors="coerce").ffill()
    tempo_h = pd.to_numeric(df["tempo_decorrido_s"], errors="coerce") / 3600.0
    passos = max(3, int(JANELA_TAXA_MIN * 60 * 5))
    feitas = ciclos - ciclos.shift(passos)
    decorrido = tempo_h - tempo_h.shift(passos)
    return (feitas / decorrido).where(decorrido > 0)


def suavizar(df: pd.DataFrame, coluna: str, janela_s: float = 120.0) -> pd.Series:
    """Mediana movel. A curva crua a 5 Hz vira uma mancha nesta largura."""
    valores = pd.to_numeric(df[coluna], errors="coerce")
    passos = max(3, int(janela_s * 5))
    return valores.rolling(passos, min_periods=passos // 4, center=True).median()


def desenhar(df: pd.DataFrame, saida: Path, escuro: bool) -> Path:
    configurar_estilo()
    fig, axes = plt.subplots(
        len(PAINEIS), 1, figsize=(11, 11.5), sharex=True,
        gridspec_kw={"hspace": 0.32, "left": 0.09, "right": 0.97, "top": 0.93, "bottom": 0.07},
    )
    if escuro:
        fundo = fig.add_axes((0, 0, 1, 1), zorder=-100)
        fundo.set_facecolor(SLIDE_DARK)
        fundo.set_axis_off()
        fig.patch.set_facecolor(SLIDE_DARK)

    for ax, (coluna, titulo, unidade, faixa, log) in zip(axes, PAINEIS, strict=True):
        cru = pd.to_numeric(df[coluna], errors="coerce")
        if coluna == "correcoes_por_hora":
            # Ja e uma taxa numa janela de 15 min: suavizar de novo esconderia
            # justamente o pico que ela existe para mostrar.
            ax.fill_between(df["hora_do_dia"], 0, cru, color=NAVY, alpha=0.18, lw=0)
            ax.plot(df["hora_do_dia"], cru, color=NAVY, lw=1.6)
            ax.set_ylim(bottom=0)
        else:
            ax.plot(df["hora_do_dia"], cru, color=NAVY, lw=0.4, alpha=0.18)
            ax.plot(df["hora_do_dia"], suavizar(df, coluna), color=NAVY, lw=2.0)
        if log:
            ax.set_yscale("log")
        if faixa is not None:
            ax.axhspan(*faixa, color=GREEN, alpha=0.12, lw=0)
            for limite in faixa:
                ax.axhline(limite, color=GREEN, ls="--", lw=1.1, alpha=0.8)
            # Dentro da faixa e rente ao piso: e a unica regiao que a curva do
            # CNR nao visita nesta sessao, porque ela passa a noite acima de 16.
            # Fora dos eixos o rotulo era cortado pela margem da figura.
            ax.text(df["hora_do_dia"].iloc[0] + 0.15, faixa[0] + 0.4, "faixa alvo",
                    va="bottom", ha="left", fontsize=9, color=GREEN,
                    fontweight="bold")
        # O crepusculo em todos os paineis: e o alinhamento vertical que deixa
        # ler se a exposicao caiu ANTES ou DEPOIS de o erro mudar.
        ax.axvspan(CREPUSCULO_H, NASCER_H, color=ORANGE, alpha=0.16, lw=0)
        ax.axvline(NASCER_H, color=ORANGE, lw=1.2, alpha=0.9)
        ax.set_title(titulo, loc="left")
        ax.set_ylabel(unidade)
        ax.grid(True, axis="y", lw=0.6)
        ax.margins(x=0.01)
        for lado in ("top", "right"):
            ax.spines[lado].set_visible(False)

    axes[0].text(NASCER_H, axes[0].get_ylim()[1], " nascer do sol",
                 va="top", ha="left", fontsize=10, color=ORANGE, fontweight="bold")
    axes[-1].set_xlabel("hora do dia")
    axes[-1].xaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f"{int(v) % 24:02d}:{int(round((v % 1) * 60)):02d}")
    )

    if escuro:
        for ax in axes:
            ax.set_facecolor("white")
            # loc="left" guarda o texto em _left_title; mexer so em ax.title
            # deixava os titulos pretos sobre o fundo escuro do slide.
            ax.title.set_color("white")
            ax._left_title.set_color("white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")
            ax.tick_params(colors="white")
            for spine in ax.spines.values():
                spine.set_color("white")

    saida.mkdir(parents=True, exist_ok=True)
    caminho = saida / ("amanhecer_slide.png" if escuro else "amanhecer.png")
    fig.savefig(caminho, dpi=170, facecolor=SLIDE_DARK if escuro else "white")
    plt.close(fig)
    return caminho


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessao", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()

    df = carregar(args.sessao)
    print(f"{len(df)} linhas com sinal, de {df['hora_do_dia'].iloc[0]:.2f} h a "
          f"{df['hora_do_dia'].iloc[-1]:.2f} h")
    for escuro in (False, True):
        print("gravado:", desenhar(df, args.saida, escuro))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
