"""Prova que o aviso de desligamento do Windows chega ao processo Python.

O ``desligamento_windows.registrar`` existe para o tracker parar o mount antes
de o Windows mata-lo num reinicio. Ele nunca foi verificado contra um reinicio
de verdade: so sabemos que o codigo registra o handler, nao que o Windows o
chama a tempo.

Este programa registra o MESMO handler, mas a "parada de emergencia" apenas
escreve uma linha num arquivo. Nao toca no mount, nao abre a camera, nao fala
com o ASCOM. Da para rodar com o telescopio desligado.

Como usar:

    1. rode este programa e deixe a janela aberta
    2. reinicie o Windows normalmente
    3. depois do boot, rode de novo com --ver

Se a linha estiver la, o handler funciona e o tracker vai parar o mount num
reinicio. Se nao estiver, a protecao e so teorica e a tarefa de boot com
parar_mount.py passa a ser a unica defesa.

Pode apagar este arquivo depois que o resultado estiver anotado.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle import desligamento_windows

MARCA = CODIGOS_DIR / "resultados" / "handler_desligamento.txt"


# Fechar a janela e reiniciar o Windows chegam pelo MESMO handler. Registrar
# so "fui chamado" faria um fechar de janela passar por prova de que o reinicio
# esta coberto, que e exatamente a conclusao errada.
EVENTO_DO_REINICIO = "desligamento do Windows"


def registrar_marca(evento: str) -> None:
    """Faz as vezes da parada de emergencia, sem tocar em hardware."""
    MARCA.parent.mkdir(parents=True, exist_ok=True)
    with MARCA.open("a", encoding="utf-8") as arquivo:
        momento = datetime.now().astimezone().isoformat(timespec="seconds")
        arquivo.write(f"{momento}  evento: {evento}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ver", action="store_true", help="so mostra o resultado e sai")
    args = parser.parse_args()

    if args.ver:
        if not MARCA.exists():
            print(f"Nenhum registro em {MARCA}.")
            print("Ou o programa nao estava rodando, ou o Windows nao avisou.")
            return 1
        conteudo = MARCA.read_text(encoding="utf-8").rstrip()
        print(f"Registro em {MARCA}:")
        print(conteudo)
        print()
        if EVENTO_DO_REINICIO in conteudo:
            print("O Windows avisou o processo ANTES de reiniciar.")
            print("O tracker vai parar o mount num reinicio ordenado.")
            return 0
        print("O handler funciona, mas NAO ha registro de desligamento do Windows.")
        print("Fechar a janela dispara o mesmo handler e nao prova nada sobre o")
        print("reinicio. Enquanto nao aparecer o evento acima, a protecao que")
        print("vale num reinicio e a tarefa de boot com parar_mount.py.")
        return 1

    ok = desligamento_windows.registrar(registrar_marca)
    print(f"Handler registrado: {'sim' if ok else 'NAO'}")
    if not ok:
        print("Sem handler nao ha o que testar; o restante nao vale.")
        return 2
    print(f"Marca sera escrita em {MARCA}")
    print()
    print("Agora reinicie o Windows. Depois do boot, rode:")
    print("    python programas_principais/testar_handler_desligamento.py --ver")
    print()
    print("Ctrl+C encerra sem testar nada.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nencerrado sem reinicio; nada foi testado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
