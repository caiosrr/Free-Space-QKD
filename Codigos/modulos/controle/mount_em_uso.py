"""O mount esta sendo comandado por alguem agora?

As paradas de emergencia existem para alcancar um eixo que ficou andando
sozinho. Mandar velocidade zero enquanto um programa legitimo conduz o mount
transforma a protecao em avaria: uma calibracao de 9 minutos morre, ou um
rastreio de madrugada para sem ninguem por perto para reiniciar.

Em 2026-09-14 isso quase aconteceu. A trava olhava so a telemetria do tracker,
e uma parada direta rodada durante uma CALIBRACAO passou reto: a calibracao nao
escreve telemetria de tracker nenhuma. O mount estava entre varreduras e nada se
perdeu, mas a protecao teria interrompido a corrida se o timing fosse outro.

Por isso a pergunta aqui nao e "o tracker esta gravando", e sim "alguem esta
comandando o mount", e cada programa que o conduz precisa aparecer neste
arquivo. Em caso de duvida responde que NAO esta em uso: deixar de parar um
mount a deriva e pior do que uma leitura de disco que falhou.
"""

from __future__ import annotations

import time
from pathlib import Path

from modulos.configuracoes import saidas

# O tracker escreve telemetria a cada segundo; 30 s cobrem qualquer engasgo.
# Lidos de saidas.py: os mesmos caminhos em que o tracker e a calibracao gravam,
# com qualquer camera. Ver ali o furo que isto fechou.
TRACKER_SESSOES = saidas.TRACKER_SESSOES_DIR
TRACKER_VIVO_S = 30.0

# A calibracao nao tem escrita continua: ela grava por varredura, e cada
# varredura com seu retorno leva dezenas de segundos. A janela precisa cobrir o
# intervalo entre gravacoes, nao o intervalo entre quadros.
CALIBRACAO_RUNS = saidas.CALIBRACAO_RUNS_DIR
CALIBRACAO_VIVA_S = 180.0


def _mais_recente(raiz: Path, padrao: str) -> float | None:
    """Instante da escrita mais nova sob ``raiz``, ou ``None``."""
    try:
        if not raiz.is_dir():
            return None
        marcas = [caminho.stat().st_mtime for caminho in raiz.glob(padrao)]
        return max(marcas) if marcas else None
    except Exception:
        return None


def motivo_de_uso() -> str | None:
    """Descreve quem esta comandando o mount, ou ``None`` se ninguem estiver."""
    agora = time.time()

    marca = _mais_recente(TRACKER_SESSOES, "*/telemetria.csv")
    if marca is not None and agora - marca < TRACKER_VIVO_S:
        return f"tracker gravando ha {agora - marca:.0f} s"

    # Qualquer arquivo da corrida serve: auditoria de varredura, bins, imagem.
    marca = _mais_recente(CALIBRACAO_RUNS, "*/**/*")
    if marca is not None and agora - marca < CALIBRACAO_VIVA_S:
        return f"calibracao escreveu ha {agora - marca:.0f} s"

    return None


def em_uso() -> bool:
    return motivo_de_uso() is not None
