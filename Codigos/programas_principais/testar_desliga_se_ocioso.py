"""O `ShutdownIfIdle` ARMA um desligamento, ou so consulta e desiste?

Invocado com o cliente conectado e o mount parado, ele devolveu `BUSY:1` em
2026-09-16 e nao fez nada. O mount estava imovel e sem rastreio, entao "ocupado"
quase certamente quer dizer "tem um cliente conectado", que era o proprio
programa perguntando.

Isso inverte a leitura que eu tinha feito. Se ocioso significa "ninguem
conectado", esse e exatamente o nosso caso: o PC morre, o cliente some, o mount
fica ocioso. E desligar e uma parada perfeita, melhor que qualquer comando,
porque nao depende de mais nada acontecer.

Falta saber se a invocacao ARMA algo que dispara depois, ou se ela so olha o
estado na hora e desiste.

    ETAPA 1, com o mount parado: invoca, desconecta, espera, e ve se o mount
    ainda responde. Se desligou, o recurso arma.

    ETAPA 2, so se a 1 der positivo e so com --movendo: o mesmo com o eixo
    ANDANDO, que e o cenario real. Aqui o desligamento e o resultado desejado, e
    nao um acidente: e ele que pararia uma deriva sem ninguem por perto.

TRES NIVEIS DE DESCONEXAO, porque do ponto de vista do mount eles sao coisas
diferentes, e "ocupado" pode significar "tem alguem falando comigo":

    --nivel api        Connected = False. A porta serial CONTINUA ABERTA pelo
                       servidor, entao o mount pode seguir se achando ocupado.
                       E o mais fraco dos tres, e foi o unico ja testado.
    --nivel servidor   voce fecha o servidor ASCOM e a porta serial fecha.
    --nivel cabo       voce arranca o USB e o link some fisicamente.

Como saber se desligou: nos dois primeiros niveis o programa pergunta pela
serial, que so responde se o mount estiver vivo, e distingue isso de "servidor
ASCOM confuso". No nivel cabo nao ha serial para perguntar: quem observa e o
operador, pelo LED do mount, e a confirmacao vem ao religar.

Religar e no botao. Nao danifica: a posicao zera no arranque de qualquer forma e
o harmonic drive nao retrocede sozinho.

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto e voce ao lado:

    python programas_principais/testar_desliga_se_ocioso.py --nivel servidor
    python programas_principais/testar_desliga_se_ocioso.py --nivel cabo
    python programas_principais/testar_desliga_se_ocioso.py --nivel cabo --movendo
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import (
    call,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    mount_address,
    move_axis,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_em_uso import motivo_de_uso

PORTA_SERIAL = "COM6"
BAUD = 9600
VELOCIDADE = 0.05      # deg/s, so na etapa 2


def mount_vivo_pela_serial(porta: str) -> bool | None:
    """Pergunta a identidade pela serial. None se nao der para saber."""
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with serial.Serial(porta, BAUD, timeout=1.0) as s:
            s.reset_input_buffer()
            s.write(b":GVP#")
            time.sleep(0.3)
            return bool(s.read(32).strip())
    except Exception:
        # Porta ocupada quer dizer que o servidor ASCOM ainda a segura, o que
        # nao diz nada sobre o mount estar ligado.
        return None


def esperar_ocioso(minutos: float, porta: str) -> str:
    fim = time.perf_counter() + minutos * 60.0
    ultimo = 0.0
    while time.perf_counter() < fim:
        t = minutos * 60.0 - (fim - time.perf_counter())
        if t - ultimo >= 20.0:
            vivo = mount_vivo_pela_serial(porta)
            estado = {True: "vivo", False: "SEM RESPOSTA", None: "porta ocupada"}[vivo]
            print(f"    t={t:5.0f}s  serial: {estado}")
            if vivo is False:
                return "o mount DESLIGOU"
            ultimo = t
        time.sleep(2.0)
    return "continuou ligado"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--porta", default=PORTA_SERIAL)
    parser.add_argument("--minutos", type=float, default=3.0,
                        help="quanto esperar desconectado")
    parser.add_argument("--movendo", action="store_true",
                        help="ETAPA 2: poe o eixo a andar antes de desconectar")
    parser.add_argument("--nivel", choices=("api", "servidor", "cabo"), default="api",
                        help=(
                            "quao fundo desconectar. api: Connected=False, e a "
                            "porta serial continua aberta. servidor: voce fecha "
                            "o servidor ASCOM e a porta fecha. cabo: voce "
                            "arranca o USB e o link some."
                        ))
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de testar.")
        return 2

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    az0, alt0 = read_altaz()
    print(f"Mount em {mount_address()}")
    print(f"  posicao: az {az0:.5f}  alt {alt0:+.5f}")
    print(f"  etapa {'2, COM O EIXO ANDANDO' if args.movendo else '1, com o mount parado'}")
    print(f"  vai invocar ShutdownIfIdle, desconectar e esperar {args.minutos:.0f} min")
    print()
    if args.movendo:
        print("ETAPA 2: o eixo vai ficar andando desconectado. Se o mount NAO")
        print(f"desligar, ele percorre ate {VELOCIDADE*args.minutos*60*3600/3600:.1f} graus.")
    print("FIQUE AO LADO DO MOUNT, COM A MAO NO BOTAO.")
    if input("Digite SIM para comecar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi feito.")
        return 1

    movendo = False
    try:
        if args.movendo:
            move_axis(0, VELOCIDADE, True)
            movendo = True
            print(f"\n  eixo em movimento a {VELOCIDADE*3600:.0f} arcsec/s")
            time.sleep(2.0)

        resposta = call("PUT", "action",
                        data={"Action": "ShutdownIfIdle", "Parameters": ""},
                        timeout=10.0)
        print(f"  ShutdownIfIdle devolveu: {resposta!r}")

        if args.nivel == "api":
            call("PUT", "connected", data={"Connected": False}, timeout=5.0)
            print("  Connected = False enviado (a porta serial segue aberta)")
        else:
            alvo = ("FECHE o servidor ASCOM agora" if args.nivel == "servidor"
                    else "ARRANQUE o cabo USB agora")
            print(f"\n  >>> {alvo} <<<")
            input("  e aperte Enter aqui: ")

        print(f"  esperando {args.minutos:.0f} min\n")
        if args.nivel == "cabo":
            # Sem cabo nao ha serial para perguntar. Quem observa e o operador:
            # o LED do mount diz na hora, e o proprio COM6 desaparece do Windows
            # se ele desligar.
            print("  OLHE O LED DO MOUNT. Ele apaga se desligar.")
            time.sleep(args.minutos * 60.0)
            desfecho = "veja o LED, e a resposta abaixo ao religar o cabo"
        else:
            desfecho = esperar_ocioso(args.minutos, args.porta)
        print(f"\n  DESFECHO: {desfecho}")
    finally:
        if args.nivel != "api":
            volta = ("REABRA o servidor ASCOM" if args.nivel == "servidor"
                     else "RELIGUE o cabo USB e reabra o servidor ASCOM")
            print(f"\n  >>> {volta} <<<")
            input("  e aperte Enter aqui: ")
        print("\n  reconectando e parando os eixos, por seguranca")
        try:
            call("PUT", "connected", data={"Connected": True}, timeout=5.0)
            time.sleep(1.0)
            if movendo:
                stop_axes_safely(attempts=3, timeout=3.0)
            az1, alt1 = read_altaz()
            d = abs((az1 - az0 + 180.0) % 360.0 - 180.0) * 3600
            print(f"  posicao final: az {az1:.5f}  alt {alt1:+.5f}")
            print(f"  deslocamento em azimute: {d:.0f} arcsec")
            if movendo:
                previsto = VELOCIDADE * args.minutos * 60 * 3600
                print(f"  se tivesse andado o tempo todo: {previsto:.0f} arcsec")
        except Exception as exc:
            print(f"  nao reconectou ({type(exc).__name__}): o mount deve estar")
            print("  desligado. Religue no botao; a posicao zera no arranque.")

    print()
    print("Como ler:")
    print(f"  (nivel testado: {args.nivel})")
    print("  DESLIGOU na etapa 1  -> o recurso ARMA, e vale rodar a etapa 2")
    print("  DESLIGOU na etapa 2  -> achamos a protecao: o mount se desliga")
    print("     sozinho quando o cliente some, mesmo com um comando ativo")
    print("  continuou ligado     -> a acao so consulta o estado na hora, e o")
    print("     assunto fecha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
