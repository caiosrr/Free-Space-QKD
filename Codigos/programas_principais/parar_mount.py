"""Para os dois eixos do mount, pelo ASCOM ou pela serial. Sem camera nem tracker.

Dois caminhos, escolhidos com ``--via``. Ate 2026-10-10 eram dois programas; o
``parar_mount_direto.py`` entrou aqui como ``--via serial``, sem mudar nada do
que ele faz. Nenhum dos dois caminhos chama o outro: a escada que tenta um e
depois o outro e a do ``vigia_mount.py``, e fica la.

**A forma fundida nunca rodou em hardware.**

``--via ascom`` (padrao)
------------------------

Existe para um cenario especifico: o tracker foi morto sem chance de encerrar --
desligamento do Windows, queda de energia, logoff -- no meio de um pulso. O
ASCOM MoveAxis nao tem prazo: uma vez comandado, o eixo anda ATE alguem mandar
zero. Se o `MoveAxis(0)` nunca sair, o mount segue andando sozinho.

Quanto isso custa, com os limites deste projeto:

    pulso fino (0,001042 deg/s):    3,75 deg/h   ->  45 deg em 12 h
    teto do tracker (0,005 deg/s):  18 deg/h     -> 216 deg em 12 h

O teto era 0,10 deg/s (360 deg/h) ate 2026-09-16.

O watchdog de MAX_OFFSET_AZ_DEG/ALT existe, mas roda DENTRO do tracker: morre
junto com ele e nao protege exatamente do caso que mais assusta.

Tambem serve para parar o mount a distancia quando algo trava.

``--via serial``
----------------

Por que existe: um MoveAxis em curso NAO para quando o servidor ASCOM cai --
medido neste mount em 2026-09-14, com o servidor fechado no meio de um pulso o
eixo andou 266 arcsec quando 246 eram previstos. Depois de um reinicio o eixo
pode seguir andando, e o caminho ASCOM nao alcanca nada enquanto o servidor
ASCOM nao subir. Como ele e um programa de janela, numa conta com senha isso
significa esperar alguem fazer login.

Este caminho fala com o AM5 pela porta serial, que nao precisa de desktop nem
de login. Registrado como tarefa de boot rodando como SYSTEM
(``diversos/instalacao/registrar_tarefas_seguranca.ps1``), ele para o eixo com a
maquina trancada.

Identificado em 2026-09-14: AM5N em COM5 a 9600 baud.

A porta sai da configuracao da maquina, de preferencia pela identidade USB do
mount: num PC compartilhado, nenhum aparelho de outro grupo recebe bytes. Ver
modulos/configuracoes/serial_mount.py. Precisa de pyserial:
``python -m pip install pyserial``. No diario, as linhas deste caminho levam a
marca ``[direto]``, como no programa antigo.

DUAS TRAVAS contra parar o que nao devia:

1. Se houver telemetria sendo escrita, ha uma sessao viva e o programa nao toca
   em nada: parar seria interromper um rastreio legitimo. Vale nos dois
   caminhos, antes e durante a espera.
2. Se o servidor ASCOM estiver no ar, ele segura a porta serial com
   exclusividade e o caminho serial simplesmente nao abre. Isso e proposital:
   ele so consegue agir justamente no cenario em que e necessario.

Uso, a partir da pasta Codigos:

    python programas_principais/parar_mount.py
    python programas_principais/parar_mount.py --via serial
    python programas_principais/parar_mount.py --via serial --aguardar 600

Sai com codigo 0 se confirmou a parada ou se desistiu por uma trava, 1 caso
contrario, para que a tarefa agendada registre a falha.
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

from modulos.controle.mount_em_uso import motivo_de_uso

DIARIO = CODIGOS_DIR / "resultados" / "parar_mount.txt"
BAUD_PADRAO = 9600


def anotar(texto: str) -> None:
    """Deixa rastro em disco, porque como tarefa agendada ninguem le a tela.

    Sem isto nao havia como saber se a protecao de boot chegou a rodar: a
    unica evidencia era o LastTaskResult do agendador, que diz que o processo
    saiu com zero, nao o que ele encontrou no mount.
    """
    try:
        DIARIO.parent.mkdir(parents=True, exist_ok=True)
        with DIARIO.open("a", encoding="utf-8") as arquivo:
            momento = datetime.now().astimezone().isoformat(timespec="seconds")
            arquivo.write(f"{momento}  {texto}\n")
    except Exception:
        # Parar o mount importa mais do que registrar que parou.
        pass


def anotar_direto(texto: str) -> None:
    """A mesma linha, com a marca do caminho serial."""
    anotar(f"[direto] {texto}")


def tentar_parar() -> tuple[bool, str]:
    """Uma tentativa pelo ASCOM. Devolve (parou, descricao do que aconteceu)."""
    from modulos.controle.mount_ascom import call, stop_axes_safely  # noqa: PLC0415

    # Sonda o servidor antes de mandar parar. Sem isto os dois desfechos ruins
    # sao indistinguiveis: stop_axes_safely engole a excecao de cada eixo e
    # devolve False tanto quando o servidor esta fora do ar quanto quando o
    # mount ignora o comando. No boot de 2026-09-14 isso gerou um "verifique o
    # mount FISICAMENTE" quando o problema era so o ASCOM ainda nao ter subido,
    # e um alarme desses a cada reinicio ensina o operador a ignorar alarmes.
    try:
        call("GET", "connected", timeout=3.0)
    except Exception as exc:
        return False, f"servidor ASCOM fora do ar: {type(exc).__name__}: {exc}"
    try:
        if stop_axes_safely(attempts=3, timeout=3.0):
            return True, "eixos confirmados em velocidade zero"
        return False, "ALERTA: servidor no ar mas os eixos nao confirmaram parada"
    except Exception as exc:
        return False, f"sem contato com o mount: {type(exc).__name__}: {exc}"


def parar_via_ascom(args: argparse.Namespace) -> int:
    from modulos.controle.mount_ascom import mount_address  # noqa: PLC0415

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}); nada foi tocado.")
        anotar(f"abortado: {uso}")
        return 0

    print(f"Parando o mount em {mount_address()} ...")
    limite = time.monotonic() + max(0.0, args.aguardar)
    tentativas = 0
    while True:
        tentativas += 1
        parou, descricao = tentar_parar()
        if parou:
            print("Ambos os eixos confirmados em velocidade zero.")
            anotar(f"{descricao} (tentativa {tentativas})")
            return 0
        if time.monotonic() >= limite:
            break
        uso = motivo_de_uso()
        if uso:
            print(f"Mount passou a ser usado durante a espera ({uso}); desistindo.")
            anotar(f"abortado na espera: {uso}")
            return 0
        # Enquanto o servidor ASCOM nao responde, o eixo pode estar andando: um
        # MoveAxis em curso NAO para quando o servidor cai, medido neste mount
        # em 2026-09-14. Insistir e a unica forma de alcanca-lo.
        time.sleep(max(1.0, args.intervalo))

    print(descricao)
    if "sem contato" in descricao or "fora do ar" in descricao:
        print("Se o driver estiver fora do ar, o eixo pode seguir em movimento:")
        print("abra o servidor ASCOM e rode este programa de novo.")
    else:
        print("Verifique o mount FISICAMENTE.")
    anotar(f"{descricao} (desistiu apos {tentativas} tentativas)")
    return 1


def parar_via_serial(args: argparse.Namespace) -> int:
    from modulos.controle.parada_emergencia import parar_pela_serial  # noqa: PLC0415

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}); nada foi tocado.")
        anotar_direto(f"abortado: {uso}")
        return 0

    alvo = args.porta or "a porta que responder"
    print(f"Parando o mount por {alvo} a {args.baud} baud ...")
    limite = time.monotonic() + max(0.0, args.aguardar)
    tentativas = 0
    while True:
        tentativas += 1
        ok, descricao = parar_pela_serial(args.porta, args.baud)
        print(f"  {descricao}")
        if ok:
            anotar_direto(f"{descricao} (tentativa {tentativas})")
            return 0
        if time.monotonic() >= limite:
            break
        uso = motivo_de_uso()
        if uso:
            print(f"Mount passou a ser usado durante a espera ({uso}); desistindo.")
            anotar_direto(f"abortado na espera: {uso}")
            return 0
        time.sleep(max(1.0, args.intervalo))

    anotar_direto(f"{descricao} (desistiu apos {tentativas} tentativas)")
    print()
    print("Se a porta estiver ocupada, o servidor ASCOM a esta segurando:")
    print("nesse caso use parar_mount.py, que fala pelo proprio ASCOM.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--via", choices=["ascom", "serial"], default="ascom",
                        help="ascom (padrao): pelo servidor ASCOM; serial: LX200 direto")
    parser.add_argument(
        "--aguardar", type=float, default=0.0, metavar="SEGUNDOS",
        help=(
            "insiste ate conseguir falar com o mount. No boot o servidor ASCOM"
            " normalmente ainda nao subiu, e uma tentativa unica falha sempre;"
            " pela serial, insiste enquanto a porta estiver ocupada ou o mount"
            " nao responder."
        ),
    )
    parser.add_argument("--intervalo", type=float, default=10.0)
    parser.add_argument("--porta", default=None,
                        help="so com --via serial; sem isto, a porta configurada na "
                             "maquina (identidade USB do mount)")
    parser.add_argument("--baud", type=int, default=None,
                        help=f"so com --via serial (padrao {BAUD_PADRAO})")
    args = parser.parse_args()

    if args.via == "serial":
        if args.baud is None:
            args.baud = BAUD_PADRAO
        return parar_via_serial(args)
    if args.porta is not None or args.baud is not None:
        parser.error("--porta e --baud valem so com --via serial")
    return parar_via_ascom(args)


if __name__ == "__main__":
    raise SystemExit(main())
