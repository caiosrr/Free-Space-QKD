"""Publica o estado da sessao do tracker num arquivo, para consulta remota.

Existe para saber de longe se a sessao ainda esta gravando, sem abrir o acesso
remoto. Em 2026-09-10 uma sessao morreu as 03:44 e so se descobriu horas
depois, ao chegar no PC.

Este programa roda NA maquina de bancada, a cada minuto, e escreve dois
arquivos pequenos. Apontado para uma pasta sincronizada (Google Drive,
OneDrive), eles aparecem no seu celular sem nenhum acesso remoto.

Por padrao reporta SO o experimento: se ha telemetria sendo escrita, qual
sessao, ha quanto tempo. Nada sobre pessoas.

``--incluir-ociosidade`` acrescenta o tempo desde o ultimo teclado ou mouse,
util para saber se a maquina esta livre. Esta DESLIGADO por padrao de
proposito: numa maquina compartilhada isso e informacao sobre a presenca de
colegas, e publicar sem que eles saibam nao e razoavel. Se for usar, avise o
pessoal do laboratorio primeiro. A frase e simples e ninguem se opoe: "deixei
um script que publica se o PC esta livre e se meu experimento esta rodando,
para eu nao precisar ficar entrando pelo AnyDesk".

Em nenhum modo ele le tela, registra teclas ou identifica o que alguem faz.

Uso, a partir da pasta Codigos:

    # so aviso no Telegram, sem arquivo nenhum
    python programas_principais/publicar_estado_pc.py --telegram-token TOKEN --telegram-chat ID

    # tambem grava os arquivos numa pasta sincronizada
    python programas_principais/publicar_estado_pc.py --saida "G:/Meu Drive/uff"
Para rodar sozinho, registre como tarefa agendada repetindo a cada 1 minuto.
As instrucoes ficam no final deste arquivo.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import socket
import subprocess
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def segundos_ocioso() -> float | None:
    """Tempo desde o ultimo teclado ou mouse, em segundos.

    Usa GetLastInputInfo do Windows. Devolve ``None`` fora do Windows ou se a
    chamada falhar, para o resto do relatorio continuar valendo.
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        agora_ms = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (agora_ms - info.dwTime) / 1000.0)
    except Exception:
        return None


# Sob pythonw.exe o processo nao tem console, e cada programa de console que
# ele lanca ganha uma JANELA NOVA. Era a origem do terminal que piscava a cada
# minuto no PC da bancada, mesmo com a tarefa agendada ja usando pythonw: nao
# era o Python aparecendo, era o tasklist.
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def processos() -> list[str]:
    """Nomes dos processos relevantes que estao no ar."""
    interessantes = ("python.exe", "AnyDesk.exe", "soffice.bin", "Code.exe")
    try:
        saida = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=20,
            creationflags=SEM_JANELA,
        ).stdout
    except Exception:
        return []
    presentes = []
    for linha in saida.splitlines():
        nome = linha.split('","')[0].strip('"') if '","' in linha else ""
        if nome in interessantes and nome not in presentes:
            presentes.append(nome)
    return presentes


def sessao_do_tracker() -> dict:
    """Ha uma sessao gravando agora? Olha a telemetria mais recente."""
    raiz = CODIGOS_DIR / "Link UFF" / "resultados" / "tracker" / "sessoes"
    if not raiz.is_dir():
        return {"gravando": False, "motivo": "pasta de sessoes ausente"}
    telemetrias = list(raiz.glob("*/telemetria.csv"))
    if not telemetrias:
        return {"gravando": False, "motivo": "nenhuma sessao"}
    recente = max(telemetrias, key=lambda p: p.stat().st_mtime)
    idade = (datetime.now().timestamp() - recente.stat().st_mtime)
    gravando = idade < 90
    extras = numeros_da_sessao(recente) if gravando else {}
    return {
        **extras,
        # Gravando de verdade escreve a cada segundo; 90 s de folga cobre
        # qualquer engasgo sem dar falso positivo numa sessao ja encerrada.
        "gravando": gravando,
        "sessao": recente.parent.name,
        "segundos_desde_a_ultima_linha": round(idade, 1),
        "tamanho_mb": round(recente.stat().st_size / 1048576, 1),
    }


def ultimas_linhas(caminho: Path, quantas: int = 400) -> list[dict]:
    """Le so o fim do CSV. Um arquivo de 30 MB nao cabe na memoria a cada minuto."""
    try:
        tamanho = caminho.stat().st_size
        with caminho.open("rb") as f:
            cabecalho = f.readline().decode("utf-8", "replace").strip().split(",")
            f.seek(max(0, tamanho - 220 * quantas))
            bruto = f.read().decode("utf-8", "replace")
        linhas = bruto.splitlines()[1:]
        return [
            dict(zip(cabecalho, valores, strict=False))
            for valores in csv.reader(linhas)
            if len(valores) == len(cabecalho)
        ]
    except Exception:
        return []


def numeros_da_sessao(caminho: Path) -> dict:
    """Erro, exposicao e disponibilidade nos ultimos instantes gravados."""
    linhas = ultimas_linhas(caminho)
    if not linhas:
        return {}
    def numero(chave):
        vals = []
        for linha in linhas:
            try:
                vals.append(float(linha.get(chave, "")))
            except ValueError:
                pass
        return sorted(vals)[len(vals) // 2] if vals else None
    com_sinal = sum(1 for linha in linhas if linha.get("sinal_encontrado") == "1")
    return {
        "horas_de_sessao": round(float(linhas[-1].get("tempo_decorrido_s", 0)) / 3600, 2),
        "erro_mediano_px": round(numero("distancia_px") or 0.0, 2),
        "exposicao_us": round(numero("exposicao_us") or 0.0),
        "cnr": round(numero("cnr_autoexposicao") or 0.0, 1),
        "percentual_com_sinal": round(100.0 * com_sinal / len(linhas), 1),
        "estado": linhas[-1].get("estado", "?"),
    }


def avisar_telegram(token: str, chat: str, texto: str) -> tuple[bool, str]:
    """Envia uma mensagem. Nunca levanta: avisar nao pode derrubar nada.

    Devolve ``(enviou, detalhe)``. O detalhe existe porque, numa tarefa
    agendada, ninguem le a saida do programa: o motivo da falha precisa chegar
    ao diario, ou um token errado passa semanas despercebido.
    """
    try:
        dados = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
        pedido = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=dados
        )
        with urllib.request.urlopen(pedido, timeout=15) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def montar(incluir_ociosidade: bool) -> dict:
    ocioso = segundos_ocioso() if incluir_ociosidade else None
    ativos = processos()
    tracker = sessao_do_tracker()
    if tracker["gravando"]:
        veredito = "TRACKER GRAVANDO"
    elif not incluir_ociosidade:
        veredito = "tracker parado"
    elif ocioso is None:
        veredito = "INDETERMINADO: nao consegui ler o tempo de ociosidade"
    elif ocioso < 300:
        veredito = "EM USO: alguem mexeu ha menos de 5 min"
    elif ocioso < 3600:
        veredito = "PROVAVELMENTE LIVRE: sem interacao ha mais de 5 min"
    else:
        veredito = "LIVRE: sem interacao ha mais de 1 h"
    estado = {
        "maquina": socket.gethostname(),
        "momento": datetime.now().astimezone().isoformat(timespec="seconds"),
        "momento_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "veredito": veredito,
        "tracker": tracker,
        "python_no_ar": "python.exe" in ativos,
    }
    if incluir_ociosidade:
        estado["usuario_logado"] = os.environ.get("USERNAME", "?")
        estado["minutos_sem_interacao"] = (
            None if ocioso is None else round(ocioso / 60, 1)
        )
        estado["anydesk_no_ar"] = "AnyDesk.exe" in ativos
    return estado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--saida", type=Path, default=None,
        help="pasta onde gravar os arquivos; sem ela, so o aviso do Telegram",
    )
    parser.add_argument(
        "--telegram-token", default=os.environ.get("QKD_TELEGRAM_TOKEN"),
        help="token do bot; tambem lido de QKD_TELEGRAM_TOKEN",
    )
    parser.add_argument(
        "--telegram-chat", default=os.environ.get("QKD_TELEGRAM_CHAT"),
        help="id do chat; tambem lido de QKD_TELEGRAM_CHAT",
    )
    parser.add_argument(
        "--testar-telegram", action="store_true",
        help="envia uma mensagem agora e relata o erro, para conferir token e chat",
    )
    parser.add_argument(
        "--incluir-ociosidade", action="store_true",
        help=(
            "acrescenta o tempo desde o ultimo teclado ou mouse. Numa maquina "
            "compartilhada, avise o pessoal do laboratorio antes de ligar."
        ),
    )
    args = parser.parse_args()
    if args.testar_telegram:
        if not (args.telegram_token and args.telegram_chat):
            print("Informe --telegram-token e --telegram-chat.")
            return 2
        print(f"token com {len(args.telegram_token)} caracteres, chat {args.telegram_chat}")
        ok, detalhe = avisar_telegram(
            args.telegram_token, args.telegram_chat,
            "Teste do vigia do tracker. Se voce recebeu isto, esta configurado.",
        )
        print("Mensagem enviada." if ok else f"NAO enviou: {detalhe}")
        return 0 if ok else 1

    estado = montar(args.incluir_ociosidade)
    # Sem --saida so o Telegram avisa, mas ainda e preciso um lugar para o
    # marcador de estado: sem ele o programa nao sabe o que mudou.
    somente_aviso = args.saida is None
    if somente_aviso:
        args.saida = CODIGOS_DIR / "resultados" / "estado_pc"
    args.saida.mkdir(parents=True, exist_ok=True)

    if not somente_aviso:
        # Escrita atomica: um leitor na nuvem nunca pega o arquivo pela metade.
        destino = args.saida / "estado_pc_uff.json"
        temporario = destino.with_suffix(".json.tmp")
        temporario.write_text(
            json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporario.replace(destino)

    # Versao legivel sem abrir JSON, para olhar do celular.
    linhas = [
        estado["veredito"],
        "",
        f"maquina            : {estado['maquina']}",
        f"momento            : {estado['momento']}",
    ]
    if args.incluir_ociosidade:
        linhas += [
            f"sem interacao ha   : {estado['minutos_sem_interacao']} min",
            f"anydesk no ar      : {'sim' if estado['anydesk_no_ar'] else 'nao'}",
        ]
    t = estado["tracker"]
    if t.get("gravando"):
        linhas.append(f"tracker            : GRAVANDO {t['sessao']} ({t['tamanho_mb']} MB)")
        for rotulo, chave, sufixo in (
            ("erro mediano", "erro_mediano_px", " px"),
            ("exposicao", "exposicao_us", " us"),
            ("CNR", "cnr", ""),
            ("com sinal", "percentual_com_sinal", "%"),
            ("horas de sessao", "horas_de_sessao", " h"),
            ("estado", "estado", ""),
        ):
            if chave in t:
                linhas.append(f"{rotulo:19s}: {t[chave]}{sufixo}")
    else:
        linhas.append(f"tracker            : parado ({t.get('motivo', 'ultima sessao encerrada')})")
    if not somente_aviso:
        (args.saida / "estado_pc_uff.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print("\n".join(linhas))

    # Avisa apenas quando o estado MUDA. Uma mensagem por minuto viraria ruido
    # e o operador deixaria de ler justamente a que importa.
    marcador = args.saida / ".ultimo_estado"
    anterior = marcador.read_text(encoding="utf-8").strip() if marcador.exists() else ""
    atual = "gravando" if t.get("gravando") else "parado"

    if not (args.telegram_token and args.telegram_chat):
        nota = f"{atual}; sem token ou chat, nenhum aviso configurado"
        marcador.write_text(atual, encoding="utf-8")
    elif atual == anterior:
        nota = f"{atual}; sem mudanca"
    else:
        texto = None
        if atual == "gravando":
            texto = f"Tracker COMECOU a gravar\n{t['sessao']}"
        elif anterior:
            texto = (
                "Tracker PAROU de gravar\n"
                f"ultima sessao: {t.get('sessao', '?')}\n"
                f"sem escrever ha {t.get('segundos_desde_a_ultima_linha', 0):.0f} s"
            )
        if texto is None:
            # Primeira execucao nao avisa: nao houve mudanca, so falta de
            # historico, e um alarme falso na estreia mina a confianca.
            nota = f"{atual}; primeira execucao, so gravando a linha de base"
            marcador.write_text(atual, encoding="utf-8")
        else:
            enviou, detalhe = avisar_telegram(
                args.telegram_token, args.telegram_chat, texto
            )
            nota = f"{anterior or 'sem historico'} -> {atual}; {detalhe}"
            if enviou:
                print("  (aviso enviado ao Telegram)")
                marcador.write_text(atual, encoding="utf-8")
            else:
                # De proposito NAO avanca o marcador: a mudanca continua
                # pendente e a proxima execucao tenta de novo. Avancar aqui
                # apagaria para sempre justamente o alarme que importa.
                print(f"  (FALHA ao avisar: {detalhe}; tentara de novo)")

    # Uma linha por execucao. E o unico lugar onde da para descobrir por que um
    # aviso nao chegou, ja que a tarefa agendada joga fora a saida do programa.
    diario = args.saida / "diario.txt"
    if diario.exists() and diario.stat().st_size > 1_000_000:
        recentes = diario.read_text(encoding="utf-8").splitlines()[-2000:]
        diario.write_text("\n".join(recentes) + "\n", encoding="utf-8")
    with diario.open("a", encoding="utf-8") as arquivo:
        arquivo.write(f"{estado['momento']}  {nota}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Para rodar sozinho a cada minuto, no PowerShell da maquina de bancada:
#
#   $acao = New-ScheduledTaskAction -Execute "python" `
#     -Argument 'programas_principais\publicar_estado_pc.py --saida "C:\Users\SEU\Meu Drive\uff"' `
#     -WorkingDirectory "C:\caminho\para\Free-Space-QKD\Codigos"
#   $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date) `
#     -RepetitionInterval (New-TimeSpan -Minutes 1)
#   Register-ScheduledTask -TaskName "Publicar estado do PC" -Action $acao `
#     -Trigger $gatilho -RunLevel Limited
#
# RunLevel Limited de proposito: a tarefa precisa rodar na sessao do usuario
# para enxergar a ociosidade do teclado. Como SYSTEM ela leria sempre zero.
# ---------------------------------------------------------------------------
