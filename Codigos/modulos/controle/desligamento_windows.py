"""Faz o Windows avisar o tracker antes de mata-lo, para ele parar o mount.

O problema: em 2026-09-10, as 03:44, uma sessao foi encerrada por reinicio do
Windows e o ``finally`` do tracker NAO rodou. Sem ele o mount fica sem o
``MoveAxis(0)`` de encerramento, e o eixo deriva -- medido neste mount, ele nao
para sozinho nem quando o servidor ASCOM cai.

A causa: o Windows avisa os processos de console antes de encerra-los, pelos
eventos ``CTRL_SHUTDOWN_EVENT`` e ``CTRL_LOGOFF_EVENT``, mas o Python nao os
trata. Sua tabela de sinais cobre Ctrl+C e Ctrl+Break; para os de desligamento,
o comportamento padrao termina o processo sem desenrolar a pilha, e nenhum
``finally`` roda.

A solucao: registrar um handler pelo ``SetConsoleCtrlHandler`` da API do
Windows. Ele ganha alguns segundos antes de o processo ser morto -- o bastante
para mandar velocidade zero, que e o que importa.

Nao substitui a tarefa de parada no boot nem o vigia: cobre o desligamento
ORDENADO (Windows Update, logoff, shutdown pedido). Queda de energia continua
sem aviso, mas ali o mount perde energia junto e para.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable

# Eventos do console do Windows.
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2      # a janela do console foi fechada no X
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6

NOMES = {
    CTRL_C_EVENT: "Ctrl+C",
    CTRL_BREAK_EVENT: "Ctrl+Break",
    CTRL_CLOSE_EVENT: "janela fechada",
    CTRL_LOGOFF_EVENT: "logoff",
    CTRL_SHUTDOWN_EVENT: "desligamento do Windows",
}

# O handler precisa sobreviver enquanto o processo existir: se o Python coletar
# o objeto, o Windows chama um ponteiro invalido no pior momento possivel.
_HANDLER_VIVO = None


def registrar(parada_de_emergencia: Callable[[str], None]) -> bool:
    """Chama ``parada_de_emergencia`` quando o Windows for encerrar o processo.

    A funcao recebe o NOME do evento que disparou. Sem isso nao da para
    distinguir "fecharam a janela" de "o Windows esta reiniciando", e os dois
    chegam pelo mesmo handler: um teste que so registra "fui chamado" concluiria
    que o reinicio esta coberto depois de um simples fechar de janela.

    Devolve se conseguiu registrar. Fora do Windows, devolve ``False`` sem
    levantar excecao: quem chama segue a vida.
    """
    global _HANDLER_VIVO
    if sys.platform != "win32":
        return False

    PROTOTIPO = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def _handler(evento: int) -> bool:
        if evento in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            nome = NOMES.get(evento, str(evento))
            print(f"\n{nome} detectado: parando o mount antes de sair.", flush=True)
            # Num thread separado com prazo: se o ASCOM travar, o processo ainda
            # e morto pelo Windows, e ficar preso aqui nao ajudaria em nada.
            t = threading.Thread(target=parada_de_emergencia, args=(nome,), daemon=True)
            t.start()
            t.join(timeout=4.0)
            print("mount parado." if not t.is_alive() else "tempo esgotado.", flush=True)
            # False deixa o Windows seguir com o encerramento, que e o certo:
            # ja fizemos o que dava, e segurar o desligamento irrita o operador.
            return False
        return False

    _HANDLER_VIVO = PROTOTIPO(_handler)
    try:
        return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(_HANDLER_VIVO, True))
    except Exception:
        return False
