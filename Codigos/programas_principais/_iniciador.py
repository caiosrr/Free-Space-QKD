"""Base comum dos iniciadores de ``programas_principais``.

Objetivo: escolher o perfil de camera e ler opcoes da linha de comando uma so
vez, em vez de repetir o mesmo prompt em cada programa.
Efeitos: define as variaveis de ambiente que os modulos internos leem.
Seguranca: nao conecta camera nem move o mount; so seleciona configuracao.

Todos os programas aceitam argumentos e continuam perguntando quando eles nao
sao informados, para o botao Play do VS Code seguir funcionando.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))


def aplicar_camera(escolha: str) -> str:
    """Aplica o perfil da camera escolhida e devolve seu nome legivel.

    ``escolha`` aceita ``ids``, ``asi``/``alpaca`` e ``zwo``. As opcoes
    numericas antigas (``1``/``2``) continuam valendo nos prompts.
    """
    normalizada = str(escolha).strip().lower()
    if normalizada == "ids":
        from modulos.configuracoes import camera_ids

        camera_ids.apply_environment()
        return "IDS"
    if normalizada in {"asi", "alpaca", "ascom"}:
        os.environ["QKD_CAMERA_BACKEND"] = "alpaca"
        return "ASI/ASCOM"
    if normalizada in {"zwo", "zwo_sdk"}:
        from modulos.configuracoes.camera_asi import EXPOSURE_US, GAIN

        os.environ["QKD_CAMERA_BACKEND"] = "zwo_sdk"
        os.environ["QKD_ZWO_EXPOSURE_US"] = str(EXPOSURE_US)
        os.environ["QKD_ZWO_GAIN"] = str(GAIN)
        return "ZWO SDK"
    raise ValueError(f"Camera desconhecida: {escolha!r}.")


def perguntar_camera(opcoes: dict[str, str], padrao: str) -> str:
    """Pergunta qual camera usar, no formato numerico ja conhecido na bancada."""
    rotulos = ", ".join(f"{chave}={nome.upper()}" for chave, nome in opcoes.items())
    escolha = input(f"Camera ({rotulos}) [{padrao}]: ").strip() or padrao
    if escolha not in opcoes:
        raise ValueError(f"Escolha uma das opcoes: {rotulos}.")
    return opcoes[escolha]


def ler_sim_nao(pergunta: str, padrao: bool = False) -> bool:
    sufixo = "S/n" if padrao else "s/N"
    resposta = input(f"{pergunta} ({sufixo}): ").strip().lower()
    if not resposta:
        return padrao
    if resposta in {"s", "sim"}:
        return True
    if resposta in {"n", "nao", "não"}:
        return False
    raise ValueError("Responda 's' ou 'n'.")
