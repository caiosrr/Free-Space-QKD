"""Le, escreve e restaura os limites de altitude do firmware do AM5.

NAO MOVE O MOUNT. Escreve configuracao, le de volta para conferir, e sabe
desfazer.

Por que interessa: e a unica protecao deste mount que agiria sem NENHUM software
rodando. Todas as outras que construimos -- tarefa de boot, vigia, parada direta
-- dependem de algum programa vivo em algum lugar. Um limite no firmware nao.

O estado encontrado em 2026-09-16, com o telescopio na UFF:

    :GLC#  '0#'    controle de limite DESLIGADO
    :GLL#  '0#'    limite inferior   0 graus
    :GLH#  '90#'   limite superior  90 graus

Dois fatos que ditam a ordem das operacoes:

1. A resolucao e de 1 GRAU. Os valores voltam como inteiros, nao como
   sexagesimal, entao nao existe -0,5: as opcoes sao 0 ou -1.

2. O enlace opera a -0,042 graus, que ja esta ABAIXO do limite inferior atual
   de 0. Habilitar o controle sem antes baixar o limite deixaria o mount
   instantaneamente fora da propria faixa permitida, e nao ha documentacao do
   que o firmware faz nessa situacao: pode parar, pode recusar comandos, ou
   pode tentar voltar sozinho para dentro da faixa, o que seria um movimento
   grande e nao pedido.

   Por isso este programa RECUSA habilitar enquanto a altitude atual nao
   estiver com folga dentro dos limites gravados.

Uso, a partir da pasta Codigos, com o servidor ASCOM FECHADO:

    python programas_principais/limite_altitude_firmware.py
    python programas_principais/limite_altitude_firmware.py --inferior -1
    python programas_principais/limite_altitude_firmware.py --habilitar
    python programas_principais/limite_altitude_firmware.py --restaurar

Precisa de pyserial:  python -m pip install pyserial
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_em_uso import motivo_de_uso

PORTA_PADRAO = "COM5"
BAUD_PADRAO = 9600

# Estado de fabrica encontrado em 2026-09-16, usado por --restaurar.
INFERIOR_ORIGINAL = 0
SUPERIOR_ORIGINAL = 90

# Folga exigida entre a altitude atual e o limite inferior antes de habilitar.
FOLGA_MINIMA_GRAUS = 0.5


def conversar(porta_serial, comando: str, espera: float = 0.3) -> str:
    porta_serial.reset_input_buffer()
    porta_serial.write(comando.encode("ascii"))
    time.sleep(espera)
    return porta_serial.read(64).decode("ascii", "replace").strip()


def inteiro(resposta: str) -> int | None:
    texto = resposta.strip().strip("#")
    try:
        return int(float(texto))
    except ValueError:
        return None


def graus(resposta: str) -> float | None:
    """Converte a altitude, que vem em sexagesimal, para graus."""
    texto = resposta.strip().strip("#").replace(chr(223), "*")
    sinal = -1.0 if texto.startswith("-") else 1.0
    partes = texto.lstrip("+-").replace("*", ":").split(":")
    try:
        numeros = [float(p) for p in partes if p != ""]
    except ValueError:
        return None
    if not numeros:
        return None
    total = numeros[0]
    if len(numeros) > 1:
        total += numeros[1] / 60.0
    if len(numeros) > 2:
        total += numeros[2] / 3600.0
    return sinal * total


def ler_estado(porta_serial) -> dict:
    estado = {
        "habilitado": inteiro(conversar(porta_serial, ":GLC#")),
        "inferior": inteiro(conversar(porta_serial, ":GLL#")),
        "superior": inteiro(conversar(porta_serial, ":GLH#")),
        "altitude": graus(conversar(porta_serial, ":GA#")),
    }
    print("  controle de limite : "
          + {0: "desligado", 1: "LIGADO"}.get(estado["habilitado"], "?"))
    print(f"  limite inferior    : {estado['inferior']} graus")
    print(f"  limite superior    : {estado['superior']} graus")
    print(f"  altitude atual     : {estado['altitude']:+.3f} graus"
          if estado["altitude"] is not None else "  altitude atual     : ?")
    return estado


def escrever_limite(porta_serial, comando_escrita: str, comando_leitura: str,
                    valor: int, rotulo: str) -> bool:
    """Escreve e CONFERE lendo de volta.

    O formato vem do driver INDI: ``:SLL%02d#`` e ``:SLH%02d#``, ou seja inteiro
    com dois digitos e zero a esquerda. Escrever ``:SLL0#`` em vez de
    ``:SLL00#`` pode ser recusado por um firmware que espere largura fixa. Em
    -1 o printf entrega "-1", que ja ocupa os dois caracteres.

    O driver INDI considera a escrita aceita quando a resposta e '1'.
    """
    resposta = conversar(porta_serial, f"{comando_escrita}{valor:02d}#")
    lido = inteiro(conversar(porta_serial, comando_leitura))
    print(f"  {rotulo}: enviei {comando_escrita}{valor:02d}#, "
          f"o mount respondeu {resposta!r}, e agora le {lido}")
    if lido == valor:
        print("    aceito.")
        return True
    print("    NAO aceito. O firmware pode recusar esse valor, ou o formato do")
    print("    comando pode ser outro. Nada foi habilitado.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--porta", default=PORTA_PADRAO)
    parser.add_argument("--baud", type=int, default=BAUD_PADRAO)
    parser.add_argument("--inferior", type=int, default=None,
                        help="novo limite inferior, em graus inteiros")
    parser.add_argument("--superior", type=int, default=None,
                        help="novo limite superior, em graus inteiros")
    parser.add_argument("--habilitar", action="store_true",
                        help="liga o controle de limite (:SLE#)")
    parser.add_argument("--desabilitar", action="store_true",
                        help="desliga o controle de limite (:SLD#)")
    parser.add_argument("--restaurar", action="store_true",
                        help=f"volta a {INFERIOR_ORIGINAL}/{SUPERIOR_ORIGINAL} e desliga")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de mexer nos limites.")
        return 2
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        print("pyserial ausente: python -m pip install pyserial")
        return 2
    try:
        porta_serial = serial.Serial(args.porta, args.baud, timeout=1.0)
    except Exception as exc:
        print(f"Nao abri {args.porta}: {type(exc).__name__}: {exc}")
        print("Se o servidor ASCOM estiver aberto, ele segura a porta: feche-o.")
        return 1

    with porta_serial:
        print("ESTADO ATUAL")
        estado = ler_estado(porta_serial)

        if args.restaurar:
            print()
            print("RESTAURANDO O ESTADO DE FABRICA")
            conversar(porta_serial, ":SLD#")
            escrever_limite(porta_serial, ":SLL", ":GLL#", INFERIOR_ORIGINAL, "inferior")
            escrever_limite(porta_serial, ":SLH", ":GLH#", SUPERIOR_ORIGINAL, "superior")
            print()
            print("ESTADO FINAL")
            ler_estado(porta_serial)
            return 0

        if args.desabilitar:
            print()
            conversar(porta_serial, ":SLD#")
            print("Controle de limite desligado.")
            ler_estado(porta_serial)
            return 0

        mexeu = False
        if args.inferior is not None:
            print()
            print("ESCREVENDO O LIMITE INFERIOR")
            mexeu = escrever_limite(
                porta_serial, ":SLL", ":GLL#", args.inferior, "inferior") or mexeu
        if args.superior is not None:
            print()
            print("ESCREVENDO O LIMITE SUPERIOR")
            mexeu = escrever_limite(
                porta_serial, ":SLH", ":GLH#", args.superior, "superior") or mexeu

        if args.habilitar:
            print()
            print("HABILITANDO O CONTROLE DE LIMITE")
            atual = ler_estado(porta_serial)
            altitude, inferior = atual["altitude"], atual["inferior"]
            if altitude is None or inferior is None:
                print("  Nao consegui ler altitude ou limite. NAO vou habilitar.")
                return 1
            folga = altitude - inferior
            print(f"  folga ate o limite inferior: {folga:+.3f} graus")
            if folga < FOLGA_MINIMA_GRAUS:
                print()
                print(f"  RECUSADO. Exijo pelo menos {FOLGA_MINIMA_GRAUS} grau de folga.")
                print("  Habilitar com o mount fora ou na beirada da faixa e o caso")
                print("  sem documentacao: o firmware pode parar, recusar comandos,")
                print("  ou tentar voltar sozinho para dentro da faixa, que seria um")
                print("  movimento grande e nao pedido.")
                print(f"  Baixe o limite antes:  --inferior {int(altitude) - 1}")
                return 1
            conversar(porta_serial, ":SLE#")
            print()
            print("ESTADO FINAL")
            final = ler_estado(porta_serial)
            if final["habilitado"] == 1:
                print()
                print("  Controle LIGADO. O firmware agora deveria barrar a descida")
                print(f"  abaixo de {final['inferior']} graus, sem depender de software.")
                print("  Isso ainda NAO foi provado: falta comandar descida e ver se")
                print("  ele realmente para. Faca isso com os olhos no equipamento.")
                print("  Para desfazer:  --restaurar")
            else:
                print()
                print("  O firmware NAO ligou o controle. O recurso pode nao valer")
                print("  para este modo de alinhamento.")
            return 0

        if not mexeu:
            print()
            print("Nada foi alterado. Use --inferior, --habilitar ou --restaurar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
