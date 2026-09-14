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

Uso:

    python programas_principais/publicar_estado_pc.py --saida "C:/Users/.../Meu Drive/uff"

Para rodar sozinho, registre como tarefa agendada repetindo a cada 1 minuto.
As instrucoes ficam no final deste arquivo.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
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


def processos() -> list[str]:
    """Nomes dos processos relevantes que estao no ar."""
    interessantes = ("python.exe", "AnyDesk.exe", "soffice.bin", "Code.exe")
    try:
        saida = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=20,
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
    return {
        # Gravando de verdade escreve a cada segundo; 90 s de folga cobre
        # qualquer engasgo sem dar falso positivo numa sessao ja encerrada.
        "gravando": idade < 90,
        "sessao": recente.parent.name,
        "segundos_desde_a_ultima_linha": round(idade, 1),
        "tamanho_mb": round(recente.stat().st_size / 1048576, 1),
    }


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
        "--saida", type=Path, required=True,
        help="pasta onde gravar (aponte para uma pasta sincronizada na nuvem)",
    )
    parser.add_argument(
        "--incluir-ociosidade", action="store_true",
        help=(
            "acrescenta o tempo desde o ultimo teclado ou mouse. Numa maquina "
            "compartilhada, avise o pessoal do laboratorio antes de ligar."
        ),
    )
    args = parser.parse_args()
    estado = montar(args.incluir_ociosidade)
    args.saida.mkdir(parents=True, exist_ok=True)

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
    else:
        linhas.append(f"tracker            : parado ({t.get('motivo', 'ultima sessao encerrada')})")
    (args.saida / "estado_pc_uff.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print("\n".join(linhas))
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
