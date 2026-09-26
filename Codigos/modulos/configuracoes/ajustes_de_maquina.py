"""Ajustes de cada maquina, fora do git, por cima dos valores versionados.

O problema que isto resolve: exposicao e ganho sao ajustados NA MAQUINA (a UFF
com a IDS, o laboratorio com a ASI), e o resto do arquivo de configuracao e
codigo, que muda pelo git. Com os dois no mesmo arquivo, qualquer ajuste local
fazia o git pull recusar ("your local changes would be overwritten").

Agora cada arquivo de configuracao de camera le, se existir, um irmao com o
sufixo _local.py (camera_ids_local.py, camera_asi_local.py). Ele esta no
.gitignore: e seu, de cada PC, e o git nunca toca nele. So os nomes listados em
cada configuracao podem ser sobrepostos, para um erro de digitacao no arquivo
local nao passar despercebido.
"""

from __future__ import annotations

import importlib


def sobrepor(destino: dict, modulo_local: str, nomes: tuple[str, ...]) -> list[str]:
    """Copia de modulo_local para destino os nomes permitidos que ele definir.

    Devolve os nomes sobrepostos. Um nome desconhecido no arquivo local levanta
    erro em vez de ser ignorado em silencio.
    """
    try:
        local = importlib.import_module(f"modulos.configuracoes.{modulo_local}")
    except ModuleNotFoundError as exc:
        if exc.name == f"modulos.configuracoes.{modulo_local}":
            return []
        raise
    definidos = [n for n in vars(local) if n.isupper()]
    desconhecidos = sorted(set(definidos) - set(nomes))
    if desconhecidos:
        raise ValueError(
            f"{modulo_local}.py define {desconhecidos}, que nao sao ajustes "
            f"reconhecidos. Permitidos: {list(nomes)}."
        )
    for nome in definidos:
        destino[nome] = getattr(local, nome)
    return definidos
