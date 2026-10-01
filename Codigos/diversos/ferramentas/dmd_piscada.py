"""Mede quanto tempo os espelhos do DMD ficam ligados, pelos quadros da ASI.

Existe por causa dos testes de 2026-09-30: com a luz do DMD espalhada sobre o
sensor inteiro e exposicao curta (32 us), o obturador rolante transforma cada
linha da camera num instante de tempo, e um quadro mostra o ciclo inteiro dos
espelhos. Ali, o branco do gerador interno deixava os espelhos ligados quase o
quadro todo, e o branco pelo HDMI os deixava pulsando, com ~1/3 da luz.

Este programa resume cada grupo de quadros em dois numeros:

    ligado      fracao media do tempo com os espelhos ligados, medida contra a
                envoltoria local da mancha (1 = sempre ligado). A forma da
                mancha desfocada puxa o numero para baixo, entao ele so vale
                comparado com outro grupo na MESMA montagem: o gerador interno
                em branco deu ~0,69 e o HDMI ~0,24.
    trocas      transicoes liga/desliga a cada 100 linhas; pulsando, sobe.

Os quadros sao agrupados pelo horario das pastas do ASICap: uma pausa maior que
--pausa segundos abre um grupo novo. Anote o horario de cada teste.

Uso, a partir da pasta Codigos:

    python diversos/ferramentas/dmd_piscada.py
    python diversos/ferramentas/dmd_piscada.py --pasta C:\\Users\\voce\\ASICAP\\CapObj --pausa 20
"""

from __future__ import annotations

import argparse
import glob
import os
from datetime import datetime

import cv2
import numpy as np

PASTA_PADRAO = os.path.join(os.path.expanduser("~"), "ASICAP", "CapObj")


def _suavizar(v: np.ndarray, n: int) -> np.ndarray:
    return np.convolve(np.pad(v, n // 2, mode="edge"), np.ones(n) / n, mode="valid")


def _max_movel(v: np.ndarray, n: int) -> np.ndarray:
    p = np.pad(v, n // 2, mode="edge")
    janelas = np.lib.stride_tricks.sliding_window_view(p, n)
    return janelas.max(axis=1)[: len(v)]


def medir_quadro(img: np.ndarray, bayer: bool = True) -> dict:
    """Fracao ligada e trocas por 100 linhas, num quadro com a luz cobrindo o sensor."""
    r = img.astype(np.float64)
    if r.ndim == 3:
        r = r.mean(axis=2)
    if bayer:
        r = r[0::2, 0::2]  # vermelho do RGGB: o HeNe so acende esse canal
    col = r.mean(axis=0)
    colunas = col > 0.5 * col.max()
    perfil = _suavizar(r[:, colunas].mean(axis=1), 3)
    env = _suavizar(_max_movel(perfil, 151), 151)
    uteis = env > 0.25 * env.max()
    ligado = float(perfil[uteis].sum() / env[uteis].sum())
    on = (perfil > 0.5 * env) & uteis
    trocas = float(np.sum(np.diff(on.astype(int)) != 0) / max(uteis.sum(), 1) * 100)
    return {"ligado": ligado, "trocas": trocas, "linhas": int(uteis.sum()),
            "saturados": float(np.mean(r >= 250))}


def _instante(pasta: str) -> datetime | None:
    nome = os.path.basename(pasta.rstrip("\\/"))
    try:
        return datetime.strptime(nome, "%Y-%m-%d_%H_%M_%SZ")
    except ValueError:
        return None


def agrupar(arquivos: list[str], pausa_s: float) -> list[list[tuple[datetime, str]]]:
    itens = sorted((t, f) for f in arquivos if (t := _instante(os.path.dirname(f))) is not None)
    grupos: list[list[tuple[datetime, str]]] = []
    for t, f in itens:
        if grupos and (t - grupos[-1][-1][0]).total_seconds() <= pausa_s:
            grupos[-1].append((t, f))
        else:
            grupos.append([(t, f)])
    return grupos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pasta", default=PASTA_PADRAO, help="pasta CapObj do ASICap")
    parser.add_argument("--pausa", type=float, default=15.0,
                        help="segundos sem captura que separam dois grupos")
    parser.add_argument("--sem-bayer", action="store_true", help="camera monocromatica")
    args = parser.parse_args()

    # No Windows o glob ignora maiusculas: *.PNG e *.png achariam o mesmo arquivo.
    arquivos = sorted({os.path.normcase(f)
                       for f in glob.glob(os.path.join(args.pasta, "*", "*.[pP][nN][gG]"))})
    grupos = agrupar(arquivos, args.pausa)
    if not grupos:
        print(f"Nenhum quadro em {args.pasta}.")
        return 1
    print("horario (UTC do ASICap)  quadros  ligado  trocas/100 linhas  saturados")
    for g in grupos:
        medidas = [medir_quadro(cv2.imread(f, cv2.IMREAD_UNCHANGED), not args.sem_bayer)
                   for _, f in g]
        ligado = np.median([m["ligado"] for m in medidas])
        trocas = np.median([m["trocas"] for m in medidas])
        sat = max(m["saturados"] for m in medidas)
        aviso = "  SATURADO" if sat > 0.005 else ""
        print(f"{g[0][0]:%H:%M:%S} a {g[-1][0]:%H:%M:%S}   {len(g):5d}   {ligado:5.2f}   "
              f"{trocas:8.1f}          {sat:6.2%}{aviso}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
