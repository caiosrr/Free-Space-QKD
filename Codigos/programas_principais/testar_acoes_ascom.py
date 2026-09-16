"""Descobre o que as Actions do driver ASCOM fazem, vigiando o mount o tempo todo.

O `SupportedActions` deste driver declara duas, sem documentacao nenhuma:
`ShutdownIfIdle` e `BeginShutdown`. Sao a ultima capacidade declarada que nunca
foi olhada.

A expectativa e baixa, e vale dizer por que. Uma deriva descontrolada e o oposto
de ocioso, entao `ShutdownIfIdle`, se faz o que o nome diz, nunca dispararia no
caso que nos preocupa. E `BeginShutdown` nao tem condicao nem prazo: e para
encerrar a noite de proposito, nao para reagir a perda de contato.

O risco tambem nao e zero: sequencias de shutdown costumam ESTACIONAR antes de
desligar, e um slew ate a posicao de park e grande e imprevisivel. Por isso este
programa nao so invoca, ele VIGIA: amostra a posicao continuamente e aborta
sozinho se o mount se mexer mais que o limite.

O que o desligamento NAO custa: a posicao deste mount zera no arranque de
qualquer forma, e nao ha alinhamento guardado que importe para o enlace. Cortar
energia no botao nao danifica nada -- o harmonic drive nao retrocede sozinho.

`BeginShutdown` exige --perigoso alem do SIM, porque o nome nao deixa duvida
sobre o que ele faz e nao ha o que aprender invocando.

Uso, a partir da pasta Codigos, com o servidor ASCOM aberto e voce ao lado:

    python programas_principais/testar_acoes_ascom.py --listar
    python programas_principais/testar_acoes_ascom.py --acao ShutdownIfIdle
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
    mount_address,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_em_uso import motivo_de_uso

LIMITE_MOVIMENTO_DEG = 0.5    # aborta se o mount andar mais que isto
VIGIA_SEGUNDOS = 45.0
INTERVALO_S = 0.5


def abortar(motivo: str) -> None:
    print(f"\n  ABORTANDO: {motivo}")
    try:
        call("PUT", "abortslew", timeout=3.0)
        print("  AbortSlew enviado.")
    except Exception as exc:
        print(f"  AbortSlew falhou: {type(exc).__name__}: {exc}")
    try:
        stop_axes_safely(attempts=3, timeout=3.0)
        print("  velocidade zero enviada nos dois eixos.")
    except Exception as exc:
        print(f"  parada falhou: {type(exc).__name__}: {exc}")


def vigiar(az0: float, alt0: float) -> str:
    """Acompanha a posicao depois da acao. Devolve o que aconteceu."""
    t0 = time.perf_counter()
    ultimo_relato = 0.0
    while True:
        t = time.perf_counter() - t0
        if t >= VIGIA_SEGUNDOS:
            return "nada aconteceu em {:.0f} s".format(VIGIA_SEGUNDOS)
        try:
            az, alt = read_altaz()
        except Exception as exc:
            return f"o mount parou de responder ({type(exc).__name__}), provavelmente desligou"
        d_az = abs((az - az0 + 180.0) % 360.0 - 180.0)
        d_alt = abs(alt - alt0)
        if t - ultimo_relato >= 3.0:
            print(f"    t={t:5.1f}s  az {az:9.5f} ({d_az*3600:+7.0f}\")  "
                  f"alt {alt:+8.5f} ({d_alt*3600:+7.0f}\")")
            ultimo_relato = t
        if max(d_az, d_alt) > LIMITE_MOVIMENTO_DEG:
            abortar(f"o mount se moveu {max(d_az, d_alt):.3f} deg")
            return "COMECOU A SE MOVER e foi abortado"
        time.sleep(INTERVALO_S)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listar", action="store_true",
                        help="so mostra as acoes declaradas e sai")
    parser.add_argument("--acao", default=None)
    parser.add_argument("--parametros", default="")
    parser.add_argument("--perigoso", action="store_true",
                        help="libera acoes cujo nome ja diz que desligam")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de testar.")
        return 2

    ensure_connected()
    print(f"Mount em {mount_address()}")
    try:
        acoes = call("GET", "supportedactions", timeout=5.0)
    except Exception as exc:
        print(f"nao consegui ler SupportedActions: {type(exc).__name__}: {exc}")
        return 1
    print(f"  acoes declaradas: {acoes}")

    if args.listar or not args.acao:
        print("\nNada foi invocado. Use --acao NOME para testar uma.")
        return 0
    if args.acao not in (acoes or []):
        print(f"\n'{args.acao}' nao esta declarada pelo driver. Nao vou invocar.")
        return 1
    if "begin" in args.acao.lower() and not args.perigoso:
        print(f"\n'{args.acao}' tem nome de desligamento incondicional, e nao ha")
        print("o que aprender invocando. Se quiser mesmo, acrescente --perigoso.")
        return 1

    az0, alt0 = read_altaz()
    print(f"\n  posicao atual: az {az0:.5f}  alt {alt0:+.5f}")
    print(f"  vou invocar '{args.acao}' e vigiar por {VIGIA_SEGUNDOS:.0f} s")
    print(f"  aborto automatico se o mount andar mais de {LIMITE_MOVIMENTO_DEG} deg")
    print()
    print("FIQUE AO LADO DO MOUNT, COM A MAO NO BOTAO DE ENERGIA.")
    print("Desligar no botao nao danifica: a posicao zera no arranque de qualquer")
    print("forma, e o harmonic drive nao retrocede sozinho.")
    if input("Digite SIM para invocar: ").strip().upper() != "SIM":
        print("Cancelado; nada foi invocado.")
        return 1

    try:
        dados = {"Action": args.acao, "Parameters": args.parametros}
        resposta = call("PUT", "action", data=dados, timeout=10.0)
        print(f"\n  resposta da acao: {resposta!r}")
    except Exception as exc:
        print(f"\n  a acao falhou: {type(exc).__name__}: {exc}")
        print("  (falhar aqui tambem e resultado: o driver declara e nao implementa)")
        return 0

    print("  vigiando:")
    desfecho = vigiar(az0, alt0)
    print(f"\n  DESFECHO: {desfecho}")

    try:
        az1, alt1 = read_altaz()
        print(f"  posicao final: az {az1:.5f}  alt {alt1:+.5f}")
        d = abs((az1 - az0 + 180.0) % 360.0 - 180.0) * 3600
        print(f"  deslocamento: az {d:.0f} arcsec  alt {(alt1-alt0)*3600:+.0f} arcsec")
    except Exception:
        print("  o mount nao responde mais; religue no botao quando quiser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
