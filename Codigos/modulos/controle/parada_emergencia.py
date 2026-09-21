"""A escada de parada do mount, do caminho mais leve ao mais bruto.

Existe porque cada caminho de parada sozinho tem um cenario em que nao alcanca
o mount, e os cenarios sao diferentes:

    Alpaca      falha quando o servidor ASCOM cai ou congela, e e justamente
                ai que o eixo costuma ficar andando: medido em 2026-09-14, com
                o servidor fechado no meio de um pulso o eixo andou 266 arcsec
                quando 246 eram previstos.
    serial      funciona sem desktop e sem login, mas o servidor ASCOM segura
                a porta com EXCLUSIVIDADE. Enquanto ele estiver de pe, este
                caminho nem abre.

Os dois furos se cancelam, e a ordem certa sai sozinha: tenta o Alpaca; se ele
nao resolver, encerra o servidor, o que LIBERA a serial; e ai a serial resolve.

Encerrar o servidor NAO para o eixo por si so, isso esta medido. Ele e
derrubado pelo efeito colateral de soltar a porta, nao pela esperanca de que o
driver se despeca direito.

Cada degrau e confirmado lendo a POSICAO duas vezes, com folga entre elas. Se a
posicao mudar, o degrau falhou e a escada continua, em vez de declarar vitoria
porque um comando foi aceito.

Quem usa: ``vigia_mount.py`` (escada inteira, sozinho, de madrugada) e
``parar_mount_direto.py`` (so o degrau da serial, por escolha do operador).
"""

from __future__ import annotations

import subprocess
import time

from modulos.configuracoes.alpaca import ALPACA_ADDRESS

BAUD_PADRAO = 9600

# Todos os comandos de parada do LX200. O driver pode estar movendo por eixo ou
# por direcao; mandar os cinco cobre as duas formas sem precisar adivinhar.
PARADAS = (b":Q#", b":Qe#", b":Qw#", b":Qn#", b":Qs#")
# Leituras de posicao em AltAz, para conferir se ainda esta andando.
POSICAO = (b":GZ#", b":GA#")

# Sem janela: a escada roda de tarefa agendada e como SYSTEM, e um console
# piscando na tela do laboratorio ja assustou gente antes.
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------- serial

def _ler_posicao(porta_serial) -> str:
    """Azimute e altitude como o mount os devolve, sem interpretar."""
    leituras = []
    for comando in POSICAO:
        porta_serial.reset_input_buffer()
        porta_serial.write(comando)
        leituras.append(porta_serial.read(32).decode("ascii", "replace").strip())
    return " ".join(leituras)


def portas_candidatas() -> list[str]:
    """Portas seriais do sistema, mais provaveis primeiro.

    O mount responde em COM5 num PC e COM6 no outro, e uma constante fixa num
    caminho de seguranca e uma armadilha esperando a proxima maquina.
    """
    try:
        from serial.tools import list_ports  # noqa: PLC0415
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]


def descobrir_porta(baud: int = BAUD_PADRAO, timeout: float = 1.0) -> str | None:
    """Primeira porta que responde a identificacao do LX200."""
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        return None
    for porta in portas_candidatas():
        try:
            with serial.Serial(porta, baud, timeout=timeout) as s:
                s.reset_input_buffer()
                s.write(b":GVP#")
                time.sleep(0.3)
                if s.read(32).strip():
                    return porta
        except Exception:
            # Porta ocupada ou de outro dispositivo; seguir adiante.
            continue
    return None


def parar_pela_serial(
    porta: str | None = None,
    baud: int = BAUD_PADRAO,
    timeout: float = 2.0,
) -> tuple[bool, str]:
    """Manda os cinco comandos de parada e confere pela posicao."""
    try:
        import serial  # noqa: PLC0415
    except ImportError:
        return False, "pyserial ausente: python -m pip install pyserial"
    if porta is None:
        porta = descobrir_porta(baud)
        if porta is None:
            return False, "nenhuma porta serial respondeu ao LX200"
    try:
        with serial.Serial(porta, baud, timeout=timeout) as s:
            for comando in PARADAS:
                s.write(comando)
                time.sleep(0.05)
            # Confere movendo o relogio, nao a fe: duas leituras separadas. Se
            # a posicao mudar entre elas, algo ainda esta girando.
            antes = _ler_posicao(s)
            time.sleep(1.5)
            depois = _ler_posicao(s)
        if not antes or not depois:
            return False, f"{porta} respondeu vazio; parada NAO confirmada"
        if antes == depois:
            return True, f"parado e confirmado em {porta} (posicao {depois})"
        return False, f"AINDA EM MOVIMENTO em {porta}: {antes} -> {depois}"
    except Exception as exc:
        return False, f"{porta}: {type(exc).__name__}: {exc}"


# ----------------------------------------------------- servidor ASCOM/Alpaca

def pid_do_servidor(endereco: str = ALPACA_ADDRESS) -> int | None:
    """PID de quem escuta na porta do Alpaca, pelo netstat.

    Pelo nome do processo seria mais simples e bem menos confiavel: o servidor
    pode ser o ASCOM Remote, o driver nativo ou um simulador, cada um com seu
    executavel. Quem escuta na porta e sempre o certo.
    """
    porta = endereco.rsplit(":", 1)[-1]
    try:
        saida = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=15,
            creationflags=SEM_JANELA,
        ).stdout
    except Exception:
        return None
    for linha in saida.splitlines():
        campos = linha.split()
        if len(campos) < 5 or campos[3].upper() != "LISTENING":
            continue
        if campos[1].rsplit(":", 1)[-1] != porta:
            continue
        try:
            return int(campos[-1])
        except ValueError:
            continue
    return None


def encerrar_servidor(endereco: str = ALPACA_ADDRESS) -> tuple[bool, str]:
    """Derruba o servidor para LIBERAR A PORTA SERIAL, nao para parar o eixo.

    Esta distincao importa: encerrar o servidor nao detem nada sozinho, e isso
    foi medido. O ganho e que a serial deixa de estar tomada.
    """
    pid = pid_do_servidor(endereco)
    if pid is None:
        return False, f"ninguem escutando em {endereco}; nada a encerrar"
    try:
        resultado = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True, timeout=20,
            creationflags=SEM_JANELA,
        )
    except Exception as exc:
        return False, f"taskkill falhou: {type(exc).__name__}: {exc}"
    if resultado.returncode != 0:
        detalhe = (resultado.stderr or resultado.stdout or "").strip()
        return False, f"taskkill recusou o pid {pid}: {detalhe}"
    # O Windows fecha os descritores de forma assincrona; sem esta folga a
    # serial ainda aparece ocupada no degrau seguinte.
    time.sleep(2.0)
    return True, f"servidor encerrado (pid {pid}); a serial deve estar livre"


# -------------------------------------------------------------------- escada

def escalar_parada(
    porta_serial: str | None = None,
    baud: int = BAUD_PADRAO,
    permitir_encerrar_servidor: bool = True,
    relatar=print,
) -> tuple[bool, list[str]]:
    """Percorre a escada ate o eixo parar. Devolve (parou, degraus tentados)."""
    from modulos.controle.mount_ascom import stop_axes_safely  # noqa: PLC0415

    degraus: list[str] = []

    def registrar(texto: str) -> None:
        degraus.append(texto)
        if relatar is not None:
            relatar(f"  {texto}")

    # 1. Alpaca, o caminho normal e o unico que respeita o driver.
    try:
        if stop_axes_safely(attempts=3, timeout=3.0):
            registrar("1. Alpaca: eixos zerados")
            return True, degraus
        registrar("1. Alpaca: nao confirmou a parada")
    except Exception as exc:
        registrar(f"1. Alpaca: {type(exc).__name__}: {exc}")

    # 2. Serial direto. Pode funcionar sem passar pelo degrau 3 se o servidor
    #    ja tiver morrido sozinho, que e um cenario comum.
    ok, descricao = parar_pela_serial(porta_serial, baud)
    registrar(f"2. serial: {descricao}")
    if ok:
        return True, degraus

    if not permitir_encerrar_servidor:
        registrar("3. encerrar servidor: desabilitado por opcao")
        return False, degraus

    # 3. Derrubar o servidor para soltar a porta, e repetir a serial.
    ok_servidor, descricao = encerrar_servidor()
    registrar(f"3. encerrar servidor: {descricao}")
    if not ok_servidor:
        return False, degraus

    ok, descricao = parar_pela_serial(porta_serial, baud)
    registrar(f"4. serial apos encerrar: {descricao}")
    return ok, degraus
