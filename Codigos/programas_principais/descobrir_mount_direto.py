"""Procura um caminho para falar com o mount SEM o servidor ASCOM.

O problema que isto resolve: depois de um reinicio inesperado, o eixo pode
seguir andando (um MoveAxis em curso nao para quando o servidor ASCOM cai,
medido neste mount em 2026-09-14). A tarefa de boot so consegue mandar
velocidade zero se o servidor ASCOM estiver no ar, e ele e um programa de
janela, que exige alguem logado. Numa conta com senha, isso deixa o mount a
deriva ate o operador conectar.

Mas o AM5 entende LX200 direto, por porta serial ou por rede. Um programa que
fale LX200 nao precisa de ASCOM, nem de desktop, nem de login: uma tarefa
agendada rodando como SYSTEM no boot consegue mandar o comando de parada.

Este programa so DESCOBRE o caminho. Ele nao move nada: pergunta o nome do
produto (:GVP#), que e uma leitura. Depois de saber por onde falar, o programa
de parada de emergencia pode ser escrito com confianca.

Uso, a partir da pasta Codigos:

    python programas_principais/descobrir_mount_direto.py
    python programas_principais/descobrir_mount_direto.py --ip 192.168.1.50

A parte serial precisa de pyserial:  pip install pyserial
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

# Comandos LX200 de LEITURA. Nenhum deles move o mount.
IDENTIDADE = b":GVP#"      # nome do produto
VERSAO = b":GVN#"          # versao do firmware

# Porta TCP que o AM5 abre no modo WiFi.
PORTA_REDE_PADRAO = 4030


def _legivel(bruto: bytes) -> str:
    return bruto.decode("ascii", "replace").strip().strip("#") or "(vazio)"


def tentar_rede(ip: str, porta: int, timeout: float = 2.0) -> str | None:
    """Pergunta a identidade por TCP. Devolve o nome, ou None."""
    try:
        with socket.create_connection((ip, porta), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(IDENTIDADE)
            nome = _legivel(s.recv(64))
            s.sendall(VERSAO)
            versao = _legivel(s.recv(64))
        return f"{nome} (firmware {versao})"
    except Exception as exc:
        print(f"    {ip}:{porta} -> {type(exc).__name__}: {exc}")
        return None


def tentar_serial(porta: str, baud: int, timeout: float = 2.0) -> str | None:
    """Pergunta a identidade por porta serial. Devolve o nome, ou None."""
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        print("    pyserial nao instalado: pip install pyserial")
        return None
    try:
        with serial.Serial(porta, baud, timeout=timeout) as s:
            s.reset_input_buffer()
            s.write(IDENTIDADE)
            nome = _legivel(s.read(64))
            if not nome or nome == "(vazio)":
                return None
            s.write(VERSAO)
            return f"{nome} (firmware {_legivel(s.read(64))})"
    except Exception as exc:
        print(f"    {porta} @ {baud} -> {type(exc).__name__}: {exc}")
        return None


def listar_seriais() -> list:
    try:
        from serial.tools import list_ports  # noqa: PLC0415
    except ImportError:
        return []
    return list(list_ports.comports())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", action="append", default=[],
                        help="IP do mount para tentar por rede; pode repetir")
    parser.add_argument("--porta-rede", type=int, default=PORTA_REDE_PADRAO)
    parser.add_argument("--baud", type=int, action="append", default=[],
                        help="velocidades seriais a tentar (padrao 9600 e 115200)")
    args = parser.parse_args()
    bauds = args.baud or [9600, 115200]

    achados = []

    print("PORTAS SERIAIS VISIVEIS")
    portas = listar_seriais()
    if not portas:
        print("  nenhuma (ou pyserial nao instalado: pip install pyserial)")
    for p in portas:
        print(f"  {p.device:10s} {p.description}")
    print()

    if portas:
        print("PERGUNTANDO A IDENTIDADE EM CADA PORTA SERIAL")
        for p in portas:
            for baud in bauds:
                nome = tentar_serial(p.device, baud)
                if nome:
                    print(f"    {p.device} @ {baud} -> {nome}")
                    achados.append(f"serial {p.device} @ {baud}")
                    break
        print()

    if args.ip:
        print("PERGUNTANDO A IDENTIDADE POR REDE")
        for ip in args.ip:
            nome = tentar_rede(ip, args.porta_rede)
            if nome:
                print(f"    {ip}:{args.porta_rede} -> {nome}")
                achados.append(f"rede {ip}:{args.porta_rede}")
        print()

    if achados:
        print("CAMINHO DIRETO ENCONTRADO:")
        for a in achados:
            print(f"  {a}")
        print()
        print("Da para parar o mount sem o servidor ASCOM, o que permite uma")
        print("tarefa de boot como SYSTEM agir sem ninguem logado.")
        return 0

    print("NENHUM caminho direto respondeu.")
    print()
    print("Se o mount estiver conectado por USB, a porta pode estar OCUPADA pelo")
    print("servidor ASCOM: feche-o e rode de novo. Se for por WiFi, descubra o IP")
    print("no app ASIAIR ou no roteador e passe --ip.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
