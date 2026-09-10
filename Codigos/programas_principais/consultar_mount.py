"""Interroga o driver do mount: o que ele sabe fazer e que limites expoe.

SO LE. Nao envia nenhum comando de movimento, nao conecta camera, nao depende
do tracker. Pode rodar com o mount apontado para qualquer lugar.

Existe para responder duas perguntas de uma vez, sem chute:

1. Da para impor limite de posicao pelo proprio driver?
   O ASCOM ITelescopeV3 NAO tem propriedade padrao de limite de eixo. O que
   pode existir e especifico do fabricante e aparece em ``SupportedActions``,
   ou no passa-a-diante ``CommandString``. Este programa lista o que houver.

2. PulseGuide funciona neste mount?
   Ele seria a resposta ideal para o medo de deriva: o firmware conta o tempo e
   para sozinho, entao um PC que morre no meio do pulso nao deixa o eixo
   andando. Aqui so se le ``CanPulseGuide`` e as taxas de guiagem -- mover de
   fato exige o teste supervisionado descrito no README.

Uso:

    python programas_principais/consultar_mount.py
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_ascom import call, mount_address

# (propriedade Alpaca, rotulo, por que importa)
LEITURAS = [
    ("connected", "conectado", ""),
    ("name", "nome", ""),
    ("driverinfo", "driver", ""),
    ("driverversion", "versao do driver", ""),
    ("interfaceversion", "versao da interface", "3 = ITelescopeV3"),
    ("alignmentmode", "modo de alinhamento", "0=AltAz 1=Polar 2=GermanPolar"),
    ("tracking", "rastreando", "PulseGuide costuma exigir rastreio LIGADO"),
    ("canpulseguide", "CanPulseGuide", "prometeria pulso com prazo no firmware"),
    ("ispulseguiding", "IsPulseGuiding", ""),
    ("cansetguiderates", "CanSetGuideRates", ""),
    ("guideratedeclination", "taxa de guiagem Dec", "deg/s; zero explica pulso sem efeito"),
    ("guideraterightascension", "taxa de guiagem RA", "deg/s"),
    ("canslewaltaz", "CanSlewAltAz", ""),
    ("cansync", "CanSync", ""),
    ("canpark", "CanPark", "parada executada pelo mount"),
    ("cansetpark", "CanSetPark", "define a posicao de repouso"),
    ("canunpark", "CanUnpark", ""),
    ("atpark", "estacionado", ""),
    ("canmoveaxis", "CanMoveAxis", "o que o tracker usa hoje"),
    ("cansettracking", "CanSetTracking", ""),
    ("slewing", "movendo agora", ""),
    ("azimuth", "azimute", "graus"),
    ("altitude", "altitude", "graus"),
    ("siteelevation", "elevacao do sitio", ""),
]


def ler(comando, **extra):
    try:
        return call("GET", comando, timeout=4.0, **extra)
    except Exception as exc:
        return f"<indisponivel: {type(exc).__name__}>"


def main() -> int:
    print(f"Consultando {mount_address()}\n")
    print("=== PROPRIEDADES ===")
    for comando, rotulo, nota in LEITURAS:
        valor = ler(comando)
        sufixo = f"   ({nota})" if nota else ""
        print(f"  {rotulo:24s} = {valor}{sufixo}")

    print("\n=== FAIXAS DE VELOCIDADE POR EIXO ===")
    print("  (MoveAxis so aceita taxas dentro destas faixas)")
    for eixo, nome in ((0, "azimute/RA"), (1, "altitude/Dec"), (2, "terceiro")):
        print(f"  eixo {eixo} ({nome}): {ler('axisrates', params={'Axis': eixo})}")
        print(f"    CanMoveAxis: {ler('canmoveaxis', params={'Axis': eixo})}")

    print("\n=== EXTENSOES DO FABRICANTE ===")
    print("  (e AQUI que um limite de posicao apareceria, se existir)")
    acoes = ler("supportedactions")
    print(f"  SupportedActions = {acoes}")
    if isinstance(acoes, list) and acoes:
        print("  -> ha acoes especificas do driver; vale investigar cada uma")
    else:
        print("  -> nenhuma. Limite de posicao, se existir, so pelo app do")
        print("     fabricante (ASIAIR / ASI Mount) ou pelo CommandString.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
