"""Descobre o que o AM5 aceita por LX200 direto, e mede o que isso custaria.

Tres perguntas, e elas nao valem o mesmo:

1. LATENCIA. Quanto custa a ida e volta pela serial contra o Alpaca por HTTP.
   Interessa pouco: o pulso minimo do controle e de 22 ms, entao uns poucos
   milissegundos de intermediario nao mudam o regime.

2. TAXA AJUSTAVEL. O LX200 classico move na taxa de uma CLASSE discreta
   (:RG guide, :RC center, :RM move, :RS slew), sem taxa arbitraria em deg/s.
   O MoveAxis do ASCOM aceita 0,001042 deg/s, que e o valor em que todo o
   controle fino foi calibrado. Se o AM5 nao deixar ajustar a taxa de guiding
   com granularidade fina, migrar custaria recalibrar o controle inteiro.

3. PULSO COM DURACAO (:Mg). E a que importa, e por SEGURANCA, nao por
   desempenho. Hoje o micropulso e cronometrado EM SOFTWARE: manda MoveAxis,
   conta o tempo, manda MoveAxis(0). Se o PC morrer entre as duas chamadas o
   eixo anda indefinidamente, e isso foi medido: 266 arcsec com o servidor
   ASCOM ja fechado. Um :Mg com duracao em milissegundos e cronometrado NO
   FIRMWARE: cada comando se encerra sozinho, e um PC morto deixa o mount
   parado em no maximo a duracao do pulso. E o efeito que um heartbeat daria,
   sem depender de alguem continuar mandando o heartbeat.

   De quebra apagaria a quantizacao de 22 ms do pulso minimo, que e a origem
   da zona morta diagonal remendada em 2026-09-15.

   Ha evidencia contra: o PulseGuide do ASCOM reporta CanPulseGuide = True e
   nao move nada neste mount. Se for limitacao do firmware, :Mg tambem nao vai
   funcionar. Se for defeito do driver, o LX200 cru funciona e ganhamos.

Por padrao o programa SO CONSULTA: manda apenas comandos :G, que pela convencao
do LX200 sao de leitura e, quando nao existem, devolvem vazio sem efeito. Os
testes que movem o mount exigem --mover e confirmacao digitada.

Uso, a partir da pasta Codigos:

    python programas_principais/sondar_mount_lx200.py
    python programas_principais/sondar_mount_lx200.py --mover

Precisa de pyserial:  python -m pip install pyserial
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

from modulos.controle.mount_em_uso import motivo_de_uso

PORTA_PADRAO = "COM5"
BAUD_PADRAO = 9600

# So comandos de leitura. A convencao do LX200 reserva :G para consultas.
CONSULTAS = [
    (":GVP#", "nome do produto"),
    (":GVN#", "versao do firmware"),
    (":GVD#", "data do firmware"),
    (":GVT#", "hora do firmware"),
    (":GZ#", "azimute"),
    (":GA#", "altitude"),
    (":GR#", "ascensao reta"),
    (":GD#", "declinacao"),
    (":GC#", "data local"),
    (":GL#", "hora local"),
    (":GG#", "fuso em relacao a UTC"),
    (":Gg#", "longitude"),
    (":Gt#", "latitude"),
    (":GW#", "estado do alinhamento"),
    (":GT#", "taxa de rastreio"),
    (":GAT#", "rastreio ativo"),
    (":Gm#", "lado do pilar"),
]

# Candidatos a extensao do fabricante. Podem simplesmente nao existir: o
# programa mostra o que voltou, sem afirmar que o comando e valido.
CANDIDATOS = [
    (":Ggr#", "taxa de guiding"),
    (":GgR#", "taxa de guiding"),
    (":Gu#", "estado resumido"),
    (":GU#", "estado resumido"),
    (":Gh#", "limite de altitude minima"),
    (":Go#", "limite de altitude maxima"),
]

GRAU_LX200 = chr(223)  # o LX200 usa 0xDF como separador de graus


def conversar(porta_serial, comando: str, espera: float = 0.25) -> str:
    porta_serial.reset_input_buffer()
    porta_serial.write(comando.encode("ascii"))
    time.sleep(espera)
    return porta_serial.read(128).decode("ascii", "replace").strip()


def arcsec(resposta: str) -> float | None:
    """Converte DDD*MM:SS# ou sDD*MM# em arcsec. None se nao reconhecer."""
    texto = resposta.strip().strip("#").replace(GRAU_LX200, "*")
    sinal = -1.0 if texto.startswith("-") else 1.0
    texto = texto.lstrip("+-")
    partes = texto.replace("*", ":").replace("'", ":").split(":")
    try:
        numeros = [float(p) for p in partes if p != ""]
    except ValueError:
        return None
    if not numeros:
        return None
    total = numeros[0] * 3600.0
    if len(numeros) > 1:
        total += numeros[1] * 60.0
    if len(numeros) > 2:
        total += numeros[2]
    return sinal * total


def secao(titulo: str) -> None:
    print()
    print(titulo)
    print("-" * len(titulo))


def medir_latencia(porta_serial, repeticoes: int = 30) -> None:
    secao("2. LATENCIA")
    tempos = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        porta_serial.reset_input_buffer()
        porta_serial.write(b":GZ#")
        resposta = porta_serial.read_until(b"#", 32)
        tempos.append((time.perf_counter() - inicio) * 1000.0)
        if not resposta:
            print("  sem resposta; abortando a medida de latencia")
            return
    tempos.sort()
    print(f"  serial, {repeticoes} idas e voltas de :GZ#")
    print(f"    mediana {statistics.median(tempos):6.1f} ms"
          f"    minima {tempos[0]:6.1f} ms"
          f"    p90 {tempos[int(0.9 * len(tempos))]:6.1f} ms")
    latencia_alpaca(repeticoes)
    print("  O pulso minimo do controle e de 22 ms: se a latencia for uma")
    print("  fracao pequena disso, o intermediario nao e o gargalo.")


def latencia_alpaca(repeticoes: int = 30) -> None:
    """A mesma medida pelo caminho atual, para a comparacao ser justa."""
    try:
        from modulos.controle.mount_ascom import call  # noqa: PLC0415

        tempos = []
        for _ in range(repeticoes):
            inicio = time.perf_counter()
            call("GET", "azimuth", timeout=3.0)
            tempos.append((time.perf_counter() - inicio) * 1000.0)
    except Exception as exc:
        print(f"  Alpaca indisponivel ({type(exc).__name__}); sem comparacao.")
        print("  Abra o servidor ASCOM e rode de novo para comparar os dois.")
        return
    tempos.sort()
    print(f"  Alpaca por HTTP, {repeticoes} leituras de azimuth")
    print(f"    mediana {statistics.median(tempos):6.1f} ms"
          f"    minima {tempos[0]:6.1f} ms")


def sondar_resolucao(porta_serial) -> None:
    secao("3. RESOLUCAO DA POSICAO")
    leituras = [arcsec(conversar(porta_serial, ":GZ#")) for _ in range(12)]
    validas = sorted({v for v in leituras if v is not None})
    print(f"  12 leituras de azimute, valores distintos: {validas}")
    if len(validas) > 1:
        passos = [b - a for a, b in zip(validas, validas[1:], strict=False)]
        print(f"  menor passo observado: {min(passos):.3f} arcsec")
    print("  O Alpaca ja entrega 1 arcsec exato, medido em 167337 leituras da")
    print("  sessao de 2026-09-15: a quantizacao e do firmware, nao da camada.")


def sondar_taxas(porta_serial) -> None:
    secao("4. CLASSES DE TAXA")
    print("  O LX200 classico so oferece classes discretas. Interessa saber se")
    print("  existe consulta ou ajuste de taxa com granularidade fina: o")
    print("  controle fino usa 0,001042 deg/s, ou 3,75 arcsec/s.")
    for comando, descricao in CANDIDATOS:
        resposta = conversar(porta_serial, comando)
        print(f"  {comando:8s} {descricao:28s} "
              f"{'(sem resposta)' if not resposta else repr(resposta)}")


def sondar_limites(porta_serial) -> None:
    """Le os limites de altitude do firmware. Nao escreve nada.

    O driver INDI do AM5 mostra que o protocolo tem :SLL e :SLH para ESCREVER
    os limites inferior e superior de altitude, e :SLE/:SLD para liga-los e
    desliga-los. Isso importa porque a interface do driver ASCOM so aceitava de
    0 a 30 graus, e o enlace opera a -0,042: descartamos o recurso por causa da
    interface, nao do firmware.

    Se o firmware aceitar um valor NEGATIVO, esse limite seria a unica protecao
    deste mount que age sem nenhum software rodando -- o proprio controlador
    barraria a deriva para baixo, que e a direcao perigosa, com o conjunto
    apontado quase na horizontal.

    So altitude: o protocolo nao expoe limite de azimute. E o eixo certo para
    proteger, pelos dois motivos: apontar para baixo encontra o chao e a
    estrutura, e foi onde a deriva se concentrou na sessao de 10 h de
    2026-09-15, com -10 arcsec em altitude contra +4 em azimute.

    Esta funcao e o primeiro passo e e inofensiva. Escrever um limite e outra
    conversa, e so vale depois de ver que numeros o firmware devolve.
    """
    secao("5. LIMITES DE ALTITUDE NO FIRMWARE")
    for comando, descricao in (
        (":GLC#", "controle de limite ligado?"),
        (":GLL#", "limite inferior"),
        (":GLH#", "limite superior"),
    ):
        resposta = conversar(porta_serial, comando)
        valor = arcsec(resposta)
        extra = f"   = {valor / 3600.0:+.3f} graus" if valor is not None else ""
        print(f"  {comando:8s} {descricao:28s} "
              f"{'(sem resposta)' if not resposta else repr(resposta)}{extra}")
    print()
    print("  Como ler:")
    print("    sem resposta nos tres -> o firmware nao expoe os limites por aqui")
    print("    limite inferior em 0 ou positivo -> e a mesma faixa que o ASCOM")
    print("      mostrava, e a pergunta passa a ser se aceita valor negativo")
    print("    limite inferior ja negativo -> o recurso existe e esta util")
    print()
    print("  O enlace opera a -0,042 graus de altitude. Um limite em -0,5 daria")
    print("  margem sem barrar a operacao normal.")


def sondar_pulso(porta_serial, duracoes_ms) -> None:
    secao("6. PULSO COM DURACAO (:Mg) -- MOVE O MOUNT")
    print("  Manda :Mg<direcao><ms> e mede o deslocamento pelo proprio encoder.")
    print("  Ida e volta em cada duracao, para nao acumular deriva.")
    print()
    print(f"  {'duracao':>10s} {'leste':>10s} {'oeste':>10s} {'taxa implicita':>18s}")
    for ms in duracoes_ms:
        deslocamentos = {}
        for rotulo, letra in (("leste", "e"), ("oeste", "w")):
            antes = arcsec(conversar(porta_serial, ":GZ#"))
            conversar(porta_serial, f":Mg{letra}{int(ms):04d}#", espera=0.05)
            time.sleep(ms / 1000.0 + 0.5)
            depois = arcsec(conversar(porta_serial, ":GZ#"))
            deslocamentos[rotulo] = (
                None if None in (antes, depois) else depois - antes
            )
        leste, oeste = deslocamentos["leste"], deslocamentos["oeste"]
        if leste is None or oeste is None:
            print(f"  {ms:8.0f} ms   leitura invalida")
            continue
        media = (abs(leste) + abs(oeste)) / 2.0
        taxa = media / (ms / 1000.0) if ms else 0.0
        print(f"  {ms:8.0f} ms {leste:+10.1f} {oeste:+10.1f} {taxa:13.2f} arcsec/s")
    print()
    print("  Como ler o resultado:")
    print("    deslocamento ZERO em todas as duracoes")
    print("      o firmware nao implementa :Mg, como ja acontece com o")
    print("      PulseGuide do ASCOM, e nao ha o que migrar")
    print("    deslocamento PROPORCIONAL a duracao")
    print("      o pulso e cronometrado no firmware; o controle pode passar a")
    print("      usa-lo, cada comando se encerra sozinho e um PC morto deixa o")
    print("      mount parado")
    print("    deslocamento IGUAL em todas as duracoes")
    print("      existe pulso, mas de tamanho fixo, e a duracao e ignorada")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--porta", default=PORTA_PADRAO)
    parser.add_argument("--baud", type=int, default=BAUD_PADRAO)
    parser.add_argument("--mover", action="store_true",
                        help="habilita os testes que movem o mount")
    parser.add_argument("--duracoes", type=float, nargs="+",
                        default=[200, 500, 1000, 2000],
                        help="duracoes de pulso a testar, em ms")
    args = parser.parse_args()

    uso = motivo_de_uso()
    if uso:
        print(f"Mount em uso ({uso}). Encerre antes de sondar.")
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
        secao("1. IDENTIDADE E CONSULTAS PADRAO")
        for comando, descricao in CONSULTAS:
            resposta = conversar(porta_serial, comando)
            valor = arcsec(resposta)
            extra = f"   = {valor:+.1f} arcsec" if valor is not None else ""
            print(f"  {comando:8s} {descricao:24s} {resposta!r}{extra}")

        medir_latencia(porta_serial)
        sondar_resolucao(porta_serial)
        sondar_taxas(porta_serial)
        sondar_limites(porta_serial)

        if not args.mover:
            print()
            print("Testes de movimento nao executados. Use --mover para inclui-los.")
            return 0

        print()
        print("OS PROXIMOS TESTES MOVEM O MOUNT, em pulsos curtos e simetricos.")
        posicao = arcsec(conversar(porta_serial, ":GZ#"))
        if posicao is None:
            print("  nao consegui ler o azimute; cancelando por seguranca.")
            return 1
        print(f"  azimute atual: {posicao:+.1f} arcsec")
        if input("Digite SIM para continuar: ").strip().upper() != "SIM":
            print("Cancelado; nada foi movido.")
            return 1

        sondar_pulso(porta_serial, args.duracoes)

        final = arcsec(conversar(porta_serial, ":GZ#"))
        if final is not None:
            print()
            print(f"  azimute final {final:+.1f} arcsec, "
                  f"liquido {final - posicao:+.1f} arcsec")
            print("  Os pulsos foram simetricos, entao o liquido deveria ser pequeno.")
            print("  Para zerar: python programas_principais/ir_para_posicao.py"
                  f" --recuar-az {final - posicao:+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
