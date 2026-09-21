"""O AM5 aceita LX200 pelo Wi-Fi, e isso daria um caminho de parada novo?

Hoje so existe UM caminho para parar um eixo a deriva sem o servidor ASCOM: a
serial, pelo ``parar_mount_direto.py``. Ele funciona, foi medido em 2026-09-14
(deteve um eixo com o servidor fora do ar), mas tem uma limitacao estrutural:
com o servidor ASCOM no ar a porta serial esta tomada com exclusividade e o
programa nem abre. Ou seja, o unico cenario em que ele NAO alcanca o mount e
justamente aquele em que o PC congelou com o ASCOM ainda segurando a porta.

O Wi-Fi do AM5 seria um segundo canal, independente da serial. Se ele aceitar
comandos enquanto o ASCOM conduz o mount, existe pela primeira vez uma parada
que nao depende de nada que roda no PC travado.

TRES PERGUNTAS, em ordem de importancia.

1. A SERIAL SOBREVIVE AO MODO ESTACAO. Se ligar o Wi-Fi fizer o mount
   despriorizar a serial, todo o experimento quebra. Esta pergunta vem antes de
   qualquer outra, e por isso o programa a responde mesmo sem ``--mover``.

2. O MOUNT RESPONDE LX200 POR TCP. Consulta pura, so comandos ``:G``.

3. UM ``:Q#`` POR TCP PARA UM EIXO QUE O ASCOM ESTA MOVENDO. E a pergunta que
   decide se isso vira protecao. Exige ``--mover``, com o servidor ASCOM no ar
   e um eixo em movimento de verdade.

O QUE ESTE PROGRAMA NAO DEFENDE: expor a porta 4030 na internet. Um canal que
aceita comando de movimento sem autenticacao nenhuma nao pode ficar acessivel
de fora. O uso que faz sentido e um vigia na PROPRIA rede local do mount.

Uso, a partir da pasta Codigos, com voce ao lado do mount:

    python programas_principais/sondar_mount_wifi.py --descobrir
    python programas_principais/sondar_mount_wifi.py --ip 192.168.0.42
    python programas_principais/sondar_mount_wifi.py --ip 192.168.0.42 --mover

Precisa de pyserial para a pergunta 1:  python -m pip install pyserial
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

PORTA_TCP_PADRAO = 4030
SERIAL_PADRAO = "COM6"
BAUD_PADRAO = 9600

VELOCIDADE = 0.05          # deg/s no teste de parada, 180 arcsec/s
SEGUNDOS_ANTES = 5.0       # movimento antes de mandar o :Q# pelo TCP
SEGUNDOS_DEPOIS = 8.0      # observacao depois do :Q#

LEITURAS = (
    (":GVP#", "nome do produto"),
    (":GVN#", "versao do firmware"),
    (":GZ#", "azimute"),
    (":GA#", "altitude"),
)


def secao(titulo: str) -> None:
    print()
    print(titulo)
    print("-" * len(titulo))


# ---------------------------------------------------------------- descoberta

def _ips_locais() -> list[str]:
    """Enderecos IPv4 desta maquina, para saber quais /24 varrer."""
    encontrados = []
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = info[4][0]
        if not ip.startswith("127.") and ip not in encontrados:
            encontrados.append(ip)
    return encontrados


def _porta_aberta(ip: str, porta: int, timeout: float) -> str | None:
    try:
        with socket.create_connection((ip, porta), timeout=timeout):
            return ip
    except Exception:
        return None


def descobrir(porta: int) -> list[str]:
    """Varre as /24 desta maquina procurando quem atende na porta do mount."""
    locais = _ips_locais()
    if not locais:
        print("Nao consegui descobrir nenhum IP local.")
        return []
    achados = []
    for local in locais:
        prefixo = local.rsplit(".", 1)[0]
        print(f"  varrendo {prefixo}.1-254 na porta {porta} (a partir de {local})")
        alvos = [f"{prefixo}.{i}" for i in range(1, 255)]
        with ThreadPoolExecutor(max_workers=128) as pool:
            for resultado in pool.map(lambda ip: _porta_aberta(ip, porta, 0.4), alvos):
                if resultado:
                    achados.append(resultado)
                    print(f"    responde: {resultado}")
    if not achados:
        print("    ninguem atendeu. O modo estacao esta ligado e conectado?")
    return achados


# ------------------------------------------------------------------ LX200 TCP

def conversar_tcp(ip: str, porta: int, comando: str, timeout: float = 2.0) -> str:
    """Manda um comando LX200 e le a resposta ate o '#', ou ate o timeout.

    Comandos de parada do LX200 nao respondem nada, entao silencio aqui nao e
    erro: quem julga o efeito e a posicao lida depois.
    """
    with socket.create_connection((ip, porta), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(comando.encode("ascii"))
        pedacos = b""
        fim = time.perf_counter() + timeout
        while time.perf_counter() < fim:
            try:
                dado = s.recv(64)
            except socket.timeout:
                break
            if not dado:
                break
            pedacos += dado
            if b"#" in pedacos:
                break
        return pedacos.decode("ascii", errors="replace").strip()


def sondar_leitura(ip: str, porta: int) -> bool:
    secao(f"2. O mount responde LX200 em {ip}:{porta}?")
    respondeu = False
    for comando, rotulo in LEITURAS:
        try:
            inicio = time.perf_counter()
            resposta = conversar_tcp(ip, porta, comando)
            ms = (time.perf_counter() - inicio) * 1000
        except Exception as exc:
            print(f"  {comando:9s} {rotulo:20s} FALHOU ({type(exc).__name__}: {exc})")
            continue
        marca = "vazio" if not resposta else repr(resposta)
        print(f"  {comando:9s} {rotulo:20s} {marca}  ({ms:.0f} ms)")
        respondeu = respondeu or bool(resposta)
    if not respondeu:
        print("  Nada respondeu. Ou nao e a porta certa, ou o mount nao fala")
        print("  LX200 por TCP, e ai a ideia toda cai por aqui.")
    return respondeu


# --------------------------------------------------------------------- serial

def checar_serial(porta: str, baud: int) -> None:
    """A pergunta 1: o Wi-Fi ligado tirou a serial do ar?"""
    secao("1. A serial continua funcionando com o Wi-Fi conectado?")
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        print("  pyserial ausente; nao da para responder.")
        print("  python -m pip install pyserial")
        return
    try:
        with serial.Serial(porta, baud, timeout=1.0) as s:
            s.reset_input_buffer()
            s.write(b":GVP#")
            time.sleep(0.3)
            resposta = s.read(64).decode("ascii", errors="replace").strip()
    except Exception as exc:
        print(f"  {porta} nao abriu ({type(exc).__name__}: {exc}).")
        print("  Se o servidor ASCOM estiver no ar, ele segura a porta e isto e")
        print("  esperado. Feche o ASCOM e rode de novo para responder de fato.")
        return
    if resposta:
        print(f"  {porta} respondeu {resposta!r}. A serial SOBREVIVEU ao modo estacao.")
    else:
        print(f"  {porta} abriu mas nao respondeu. ATENCAO: o modo estacao pode")
        print("  ter derrubado a serial, e isso quebraria a montagem atual.")


# ------------------------------------------------------- parada por TCP

def testar_parada_por_tcp(ip: str, porta: int) -> int:
    """A pergunta 3, com o ASCOM conduzindo o eixo e o :Q# indo pelo Wi-Fi."""
    from modulos.controle.mount_ascom import (  # noqa: PLC0415
        ensure_connected,
        ensure_not_tracking,
        ensure_unparked,
        mount_address,
        move_axis,
        read_altaz,
        stop_axes_safely,
    )
    from modulos.controle.mount_em_uso import motivo_de_uso  # noqa: PLC0415

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de testar.")
        return 2

    secao("3. Um :Q# pelo TCP para um eixo que o ASCOM esta movendo?")
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az0, alt0 = read_altaz()
    total = SEGUNDOS_ANTES + SEGUNDOS_DEPOIS
    print(f"  ASCOM em {mount_address()}, azimute inicial {az0:.5f}")
    print(f"  {SEGUNDOS_ANTES:.0f} s andando, :Q# pelo TCP, {SEGUNDOS_DEPOIS:.0f} s observando")
    print(f"  se o :Q# PARAR:   ~{VELOCIDADE * SEGUNDOS_ANTES * 3600:.0f} arcsec")
    print(f"  se for IGNORADO:  ~{VELOCIDADE * total * 3600:.0f} arcsec")
    print()
    print("ESTE TESTE MOVE O EIXO DE AZIMUTE. Fique ao lado do mount.")
    if input("Digite SIM para continuar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi movido.")
        return 1

    try:
        move_axis(0, VELOCIDADE, True)
        print(f"\n  eixo a {VELOCIDADE * 3600:.0f} arcsec/s")
        time.sleep(SEGUNDOS_ANTES)
        az_antes, _ = read_altaz()

        for comando in (":Q#", ":Qe#", ":Qw#"):
            try:
                conversar_tcp(ip, porta, comando, timeout=1.5)
                print(f"  {comando} enviado pelo TCP")
            except Exception as exc:
                print(f"  {comando} falhou pelo TCP ({type(exc).__name__}: {exc})")

        time.sleep(SEGUNDOS_DEPOIS)
        az_depois, _ = read_altaz()
    finally:
        # O ASCOM continua sendo o dono do movimento: a parada de verdade sai
        # por ele, aconteca o que acontecer com o experimento.
        stop_axes_safely(attempts=3, timeout=3.0)

    time.sleep(1.5)
    az_final, alt_final = read_altaz()
    percorrido = abs((az_final - az0 + 180.0) % 360.0 - 180.0) * 3600.0
    depois_do_q = abs((az_depois - az_antes + 180.0) % 360.0 - 180.0) * 3600.0
    parando = VELOCIDADE * SEGUNDOS_ANTES * 3600.0
    ignorando = VELOCIDADE * total * 3600.0

    print()
    print(f"  posicao final: az {az_final:.5f}  alt {alt_final:+.5f}")
    print(f"  percorrido no total: {percorrido:.0f} arcsec")
    print(f"    se tivesse parado no :Q#:  {parando:.0f}")
    print(f"    se tivesse ignorado:       {ignorando:.0f}")
    print(f"  andou DEPOIS do :Q#: {depois_do_q:.0f} arcsec "
          f"(esperado ~0 se parou, ~{VELOCIDADE * SEGUNDOS_DEPOIS * 3600:.0f} se nao)")
    print()
    if percorrido < (parando + ignorando) / 2:
        print("  O :Q# PELO WI-FI PAROU O EIXO.")
        print("  Existe um segundo caminho de parada, independente da serial e do")
        print("  PC. Vale montar um vigia na rede LOCAL do mount.")
    else:
        print("  O :Q# pelo Wi-Fi NAO parou o eixo. A ideia nao vira protecao.")
    print()
    print(f"  Para voltar: python programas_principais/mover_mount.py --para-az {az0:.5f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default=None, help="IP do mount na rede local")
    parser.add_argument("--porta", type=int, default=PORTA_TCP_PADRAO)
    parser.add_argument("--serial", default=SERIAL_PADRAO)
    parser.add_argument("--baud", type=int, default=BAUD_PADRAO)
    parser.add_argument("--descobrir", action="store_true",
                        help="varre a rede local procurando a porta do mount")
    parser.add_argument("--mover", action="store_true",
                        help="pergunta 3: move o eixo e tenta parar pelo TCP")
    args = parser.parse_args()

    checar_serial(args.serial, args.baud)

    ip = args.ip
    if args.descobrir or ip is None:
        secao(f"Procurando quem atende na porta {args.porta}")
        achados = descobrir(args.porta)
        if ip is None:
            if len(achados) != 1:
                print("\nInforme o IP com --ip para seguir.")
                return 1
            ip = achados[0]
            print(f"\nUsando {ip}.")

    if not sondar_leitura(ip, args.porta):
        return 1

    if not args.mover:
        print()
        print("Pergunta 3 nao executada. Acrescente --mover, com o servidor ASCOM")
        print("no ar e voce ao lado do mount.")
        return 0

    return testar_parada_por_tcp(ip, args.porta)


if __name__ == "__main__":
    raise SystemExit(main())
