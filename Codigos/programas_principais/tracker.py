"""Inicia o tracker principal com a camera ASI ou IDS.

    python programas_principais/tracker.py --camera ids --horas 0.5 --sem-autoteste
    python programas_principais/tracker.py --camera zwo --horas 0.5 --sem-autoteste

``asi`` e a ASI pelo ASCOM; ``zwo`` e a ASI pelo SDK nativo da ZWO.

Blocos alternados com e sem correcao, para medir o efeito do controle:

    python programas_principais/tracker.py --camera ids --horas 8 --blocos-minutos 15

A/B entre o regime lento em uso e o braco de janela curta (45 s, limiar em
arcsec), alternando a cada N minutos; os dois bracos corrigem:

    python programas_principais/tracker.py --camera ids --horas 8 --ab-janela-curta 10

Blocos com e sem correcao, corrigindo com o braco de janela curta:

    python programas_principais/tracker.py --camera ids --horas 7 --blocos-minutos 15 --regime lento_janela_curta

Sessao inteira SEM correcao, para medir a deriva crua com o mount apontado e
parado. Captura, mede e grava a telemetria como o tracker, com correcao_ativa
em 0, e nao move o mount: nenhum pulso, nenhum autoteste, nenhum retorno a
posicao inicial. Substitui o observar_sem_corrigir.py desde 2026-10-10, e
**nunca rodou em hardware nesta forma**:

    python programas_principais/tracker.py --camera ids --horas 10 --sem-correcao
    python programas_principais/tracker.py --camera ids --horas 3 --sem-correcao --exposicao-us 9000 --ganho 8

``--exposicao-us`` e ``--ganho`` valem em qualquer sessao e sobrepoem o perfil
da camera. Na IDS a autoexposicao continua presa a faixa da configuracao.
"""

import sys
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import argparse
import os
import subprocess

from programas_principais._iniciador import aplicar_camera, perguntar_camera

VIGIA = CODIGOS_DIR / "programas_principais" / "vigia_mount.py"
VIGIA_LOG = CODIGOS_DIR / "resultados" / "vigia_mount.log"
AVISO = CODIGOS_DIR / "programas_principais" / "publicar_estado_pc.py"
AVISO_LOG = CODIGOS_DIR / "resultados" / "estado_pc" / "acompanhante.log"
# Folga entre a ultima linha de telemetria e o disparo. Os 25 s padrao do vigia
# podem cair em cima do retorno a posicao inicial, que roda DEPOIS de a
# telemetria parar: na sessao de 2026-09-15 o retorno levou 2 s, mas precisou de
# duas tentativas, e com mais tentativas os 25 s ficariam apertados.
VIGIA_DISPARO_S = 60.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids", "zwo"], default=None)
    parser.add_argument(
        "--horas",
        type=float,
        default=None,
        help="Duracao maxima da sessao. Sem isso, o programa pergunta.",
    )
    autoteste = parser.add_mutually_exclusive_group()
    autoteste.add_argument("--autoteste", dest="autoteste", action="store_true")
    autoteste.add_argument("--sem-autoteste", dest="autoteste", action="store_false")
    parser.set_defaults(autoteste=None)
    parser.add_argument(
        "--blocos-minutos", type=float, default=None,
        help="alterna blocos COM e SEM correcao dessa duracao; o primeiro corrige",
    )
    parser.add_argument(
        "--ab-janela-curta", type=float, default=None, metavar="MIN",
        help="alterna lento_ganho_baixo e lento_janela_curta a cada MIN minutos (>= 4)",
    )
    parser.add_argument(
        "--regime", default=None,
        choices=["atual", "lento_ganho_alto", "lento_ganho_baixo", "lento_janela_curta"],
        help="regime de controle da sessao (padrao: lento_ganho_baixo)",
    )
    parser.add_argument(
        "--ganho-curta", type=float, default=None, metavar="F",
        help="fracao corrigida por pulso no braco lento_janela_curta (padrao 0,35)",
    )
    vigia = parser.add_mutually_exclusive_group()
    vigia.add_argument("--vigia", dest="vigia", action="store_true")
    vigia.add_argument("--sem-vigia", dest="vigia", action="store_false")
    parser.set_defaults(vigia=True)
    parser.add_argument(
        "--sem-correcao", action="store_true",
        help="observa a sessao inteira sem mover o mount (sem pulso, autoteste nem retorno)",
    )
    parser.add_argument("--exposicao-us", type=float, default=None,
                        help="exposicao inicial; sobrepoe a do perfil da camera")
    parser.add_argument("--ganho", type=float, default=None,
                        help="ganho analogico; sobrepoe o do perfil da camera")
    args = parser.parse_args()
    if args.sem_correcao:
        for opcao, valor in (("--autoteste", args.autoteste),
                             ("--blocos-minutos", args.blocos_minutos),
                             ("--ab-janela-curta", args.ab_janela_curta)):
            if valor:
                parser.error(f"{opcao} nao combina com --sem-correcao")
    return args


def aplicar_sobreposicoes(args: argparse.Namespace) -> None:
    """Exposicao e ganho pela linha de comando, sem editar o perfil da camera.

    Precisa rodar DEPOIS de ``aplicar_camera`` e ANTES de qualquer import de
    ``modulos``: a exposicao e lida do ambiente na hora do import. Cada camera
    le variaveis com prefixo proprio, entao a sobreposicao segue o backend.
    Veio do observar_sem_corrigir.py, sem mudanca.
    """
    zwo = os.environ.get("QKD_CAMERA_BACKEND") == "zwo_sdk"
    if args.exposicao_us is not None:
        if zwo:
            # No modo de video da ZWO a taxa segue a exposicao sozinha.
            os.environ["QKD_ZWO_EXPOSURE_US"] = str(args.exposicao_us)
            print(f"Exposicao inicial {args.exposicao_us:.0f} us.")
        else:
            os.environ["QKD_IDS_EXPOSURE_US"] = str(args.exposicao_us)
            # O limite de aquisicao do perfil (50 fps) e impossivel com
            # exposicao longa, e a camera recusa a combinacao. 10% de folga.
            fps = 0.9 / (args.exposicao_us * 1e-6)
            fps = min(fps, float(os.environ.get("QKD_IDS_FPS", fps)))
            os.environ["QKD_IDS_FPS"] = str(round(fps, 3))
            print(f"Exposicao inicial {args.exposicao_us:.0f} us, "
                  f"taxa limitada a {fps:.2f} fps.")
    if args.ganho is not None:
        os.environ["QKD_ZWO_GAIN" if zwo else "QKD_IDS_ANALOG_GAIN"] = str(args.ganho)
        print(f"Ganho {args.ganho:g}.")


def iniciar_vigia() -> subprocess.Popen | None:
    """Sobe o vigia como processo FILHO, para nao depender de outra janela.

    O arranjo e proposital nos dois desfechos. Se o tracker terminar de forma
    ordenada, inclusive por Ctrl+C, o ``finally`` mata o vigia e ele nao chega a
    interferir no retorno a posicao inicial. Se o tracker for morto sem rodar o
    ``finally``, que e o caso que o vigia existe para cobrir, o filho sobrevive e
    para o mount sozinho.

    Roda em grupo de processos proprio para o Ctrl+C do console nao chegar nele
    direto: quem decide encerra-lo e o ``finally``, e nao a ordem em que o
    Windows entrega o sinal.
    """
    try:
        VIGIA_LOG.parent.mkdir(parents=True, exist_ok=True)
        saida = VIGIA_LOG.open("a", encoding="utf-8", buffering=1)
        return subprocess.Popen(
            [sys.executable, str(VIGIA), "--disparar-em", str(VIGIA_DISPARO_S)],
            cwd=str(CODIGOS_DIR), stdout=saida, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        # Sem vigia a sessao ainda roda, e as outras duas protecoes seguem de pe.
        print(f"AVISO: nao consegui iniciar o vigia ({type(exc).__name__}: {exc}).")
        return None


def iniciar_aviso_telegram() -> subprocess.Popen | None:
    """Sobe o aviso pelo Telegram, sem janela, so durante esta sessao.

    Ao contrario do vigia, ele NAO e encerrado no fim: e ele que avisa o fim,
    e sai sozinho depois de avisar. Por ser outro processo, avisa tambem quando
    o tracker morre. Sem janela de proposito: ate 2026-10 o aviso era uma
    tarefa agendada que abria um console a cada 15 min no PC compartilhado da
    UFF. Token e chat vem das variaveis de ambiente (ver publicar_estado_pc.py).
    """
    if not (os.environ.get("QKD_TELEGRAM_TOKEN") and os.environ.get("QKD_TELEGRAM_CHAT")):
        print("Aviso pelo Telegram desligado: faltam QKD_TELEGRAM_TOKEN e QKD_TELEGRAM_CHAT.")
        return None
    try:
        AVISO_LOG.parent.mkdir(parents=True, exist_ok=True)
        saida = AVISO_LOG.open("a", encoding="utf-8", buffering=1)
        return subprocess.Popen(
            [sys.executable, "-u", str(AVISO), "--acompanhar-sessao"],
            cwd=str(CODIGOS_DIR), stdout=saida, stderr=subprocess.STDOUT,
            # Sem console proprio: nao abre janela, e fechar a janela do
            # tracker no X nao o leva junto.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        print(f"AVISO: nao consegui iniciar o aviso pelo Telegram ({type(exc).__name__}: {exc}).")
        return None


if __name__ == "__main__":
    args = parse_args()
    escolha = args.camera or perguntar_camera({"1": "asi", "2": "ids", "3": "zwo"}, "1")
    camera = aplicar_camera(escolha)
    print(f"Iniciando tracker com {camera}.")
    aplicar_sobreposicoes(args)
    if args.sem_correcao:
        # Lido pela configuracao na hora do import, como os blocos.
        os.environ["QKD_SEM_CORRECAO"] = "1"
        args.autoteste = False
        print("Sessao SEM CORRECAO: o mount nao sera movido em momento nenhum.")
        # Como o antigo observar_sem_corrigir.py: nao observa por cima de quem
        # estiver usando o mount.
        from modulos.controle.mount_em_uso import motivo_de_uso

        uso = motivo_de_uso()
        if uso:
            print(f"Camera ou mount em uso ({uso}). Encerre antes de observar.")
            raise SystemExit(2)
    if args.blocos_minutos:
        # Lido pela configuracao na hora do import: precisa vir antes dele.
        os.environ["QKD_BLOCOS_CORRECAO"] = "1"
        os.environ["QKD_BLOCOS_CORRECAO_S"] = str(args.blocos_minutos * 60.0)
        print(f"Blocos de {args.blocos_minutos:g} min: com correcao, sem, com, ...")
    if args.regime:
        os.environ["QKD_REGIME"] = args.regime
        print(f"Regime de controle pedido: {args.regime}")
    if args.ganho_curta is not None:
        if not 0 < args.ganho_curta <= 1:
            raise SystemExit("--ganho-curta precisa estar entre 0 e 1.")
        os.environ["QKD_CURTA_GANHO"] = str(args.ganho_curta)
        print(f"Ganho do braco de janela curta: {args.ganho_curta:g}")
    if args.ab_janela_curta:
        # O bloco precisa encher a janela de 120 s do braco em uso duas vezes;
        # a configuracao recusa menos que isso ao importar.
        if args.ab_janela_curta < 4:
            raise SystemExit("--ab-janela-curta precisa de pelo menos 4 minutos.")
        os.environ["QKD_AB_CONTROLE"] = "1"
        os.environ["QKD_AB_REGIMES"] = "lento_ganho_baixo,lento_janela_curta"
        os.environ["QKD_AB_BLOCO_S"] = str(args.ab_janela_curta * 60.0)
        print(f"A/B de {args.ab_janela_curta:g} min: lento_ganho_baixo, "
              "lento_janela_curta, ...")
        if args.blocos_minutos:
            print("AVISO: com --blocos-minutos junto, metade de cada braco fica sem "
                  "correcao; a comparacao entre bracos perde dados.")

    from modulos.controle.tracker_sessao import main

    vigia = iniciar_vigia() if args.vigia else None
    if vigia is not None:
        print(f"Vigia do mount ativo (pid {vigia.pid}), saida em {VIGIA_LOG}.")
    aviso = iniciar_aviso_telegram()
    if aviso is not None:
        print(f"Aviso pelo Telegram ativo (pid {aviso.pid}), saida em {AVISO_LOG}.")
    try:
        main(session_hours=args.horas, executar_autoteste=args.autoteste)
    finally:
        if vigia is not None and vigia.poll() is None:
            vigia.terminate()
            print("Vigia encerrado junto com a sessao.")
