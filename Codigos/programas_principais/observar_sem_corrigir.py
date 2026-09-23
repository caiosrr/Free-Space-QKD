"""Observa o beacon a noite inteira SEM corrigir, para medir o que a correcao faz.

Este programa NAO importa nada do mount. Ele nao pode move-lo nem por engano:
a unica coisa que ele faz e olhar.

Responde tres perguntas de uma aquisicao so.

1. QUANTO O FEIXE DERIVA SEM CORRECAO. Hoje so sabemos isso por reconstrucao,
   somando ao residuo no sensor o que o mount compensou, o que herda o filtro do
   controlador: passos quantizados de 0,216 px e atraso da janela de 120 s. Com
   o mount parado a deriva e medida crua.

   Da reconstrucao da sessao de 2026-09-15, esperam-se uns 28 px em 10 h, contra
   1,19 px de erro mantido com correcao. A ROI tem 256 px, entao a noite inteira
   cabe com folga.

2. QUANTOS QUADROS PRECISAM SER SOMADOS PARA A TURBULENCIA PARAR DE IMPORTAR.
   A cada intervalo o programa acumula uma soma corrente e mede o centro de
   massa dela em 1, 2, 4, 8... quadros. Isso e diferente de promediar centros
   individuais: e o que uma medida de verdade entrega, e a diferenca aparece
   quando a forma da mancha muda.

   Previsao, a partir do que ja foi medido (1,30 px por quadro, 0,85 px na media
   de 2 s, tempo de correlacao de 6,9 s): a curva cai rapido ate uns 10 quadros,
   ACHATA entre 10 e 200, e so volta a cair depois disso. Dois regimes, nao um.
   Se confirmar, a janela de 2 s esta na pior regiao: paga o atraso de 2 s e
   ganha 1,5x, quando 60 quadros independentes dariam 7,7x.

3. COMO FICA A IMAGEM EMPILHADA. Somando os quadros em coordenadas FIXAS, sem
   realinhar, a deriva vira um rastro. Com o tracker ativo a mesma soma fica
   compacta. A comparacao das duas e a demonstracao visual do que o controle faz,
   e as metricas de forma a tornam quantitativa.

O que NAO fazer com o resultado: comparar a imagem empilhada daqui com a de uma
sessao de tracker de duracao diferente. O rastro cresce com o tempo; so a mesma
duracao compara.

Uso, a partir da pasta Codigos, com o mount APONTADO e PARADO:

    python programas_principais/observar_sem_corrigir.py --camera ids --horas 10
    python programas_principais/observar_sem_corrigir.py --camera ids --horas 2 --intervalo-rajada 5
    python programas_principais/observar_sem_corrigir.py --camera ids --horas 3.25 --exposicao-us 300000 --ganho 8

Sem ``--camera`` ele pergunta, como os outros programas da pasta.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import cv2
import numpy as np

from programas_principais._iniciador import aplicar_camera, perguntar_camera

from modulos.configuracoes import saidas  # noqa: E402

SAIDA_RAIZ = saidas.SEM_CORRECAO_OUTPUT_DIR

# Potencias de 2 ate uns 2 minutos de quadros, que e onde a previsao diz que a
# curva volta a cair. Passar muito disso so gastaria tempo de rajada.
QUADROS_DA_RAJADA = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]

LIMIAR_CENTROIDE = 0.45  # a mesma fracao do pico que o detector usa em producao


def centroide_da_soma(soma: np.ndarray) -> tuple[float, float] | None:
    """Centro de massa da imagem somada, com a regra usada em producao.

    O detector de producao trabalha sobre quadros vivos e mantem uma ilha
    travada; a imagem somada nao e um quadro, entao aqui se aplica diretamente a
    regra que ele usa por dentro: limiar em 45% do pico e pesos proporcionais ao
    sinal. Fica comparavel com o que o tracker mede, sem depender da maquinaria
    de travamento.
    """
    if soma is None or not np.any(soma):
        return None
    pico = float(soma.max())
    if pico <= 0.0:
        return None
    pesos = np.where(soma >= pico * LIMIAR_CENTROIDE, soma, 0.0)
    total = float(pesos.sum())
    if total <= 0.0:
        return None
    yy, xx = np.indices(pesos.shape, dtype=np.float64)
    return float((xx * pesos).sum() / total), float((yy * pesos).sum() / total)


def forma_da_soma(soma: np.ndarray) -> dict:
    """Area e raios RMS do empilhamento. E aqui que o rastro vira numero."""
    centro = centroide_da_soma(soma)
    if centro is None:
        return {}
    pico = float(soma.max())
    mascara = soma >= pico * LIMIAR_CENTROIDE
    pesos = np.where(mascara, soma, 0.0)
    total = float(pesos.sum())
    yy, xx = np.indices(soma.shape, dtype=np.float64)
    dx, dy = xx - centro[0], yy - centro[1]
    var_x = float((dx * dx * pesos).sum() / total)
    var_y = float((dy * dy * pesos).sum() / total)
    # Covariancia diz a DIRECAO do rastro, que e o que distingue deriva de
    # alargamento simetrico por turbulencia.
    cov = float((dx * dy * pesos).sum() / total)
    matriz = np.array([[var_x, cov], [cov, var_y]])
    autov = np.linalg.eigvalsh(matriz)
    maior, menor = float(np.sqrt(max(autov[1], 0))), float(np.sqrt(max(autov[0], 0)))
    return {
        "centro_x_px": centro[0],
        "centro_y_px": centro[1],
        "area_px": int(np.count_nonzero(mascara)),
        "rms_x_px": float(np.sqrt(var_x)),
        "rms_y_px": float(np.sqrt(var_y)),
        "eixo_maior_px": maior,
        "eixo_menor_px": menor,
        "alongamento": float(maior / menor) if menor > 0 else None,
        "angulo_graus": float(np.degrees(0.5 * np.arctan2(2 * cov, var_x - var_y))),
    }


def salvar_empilhada(soma: np.ndarray, destino: Path, rotulo: str) -> None:
    """Grava a soma em cinza e em cor, com estiramento que revela a cauda.

    Estiramento por raiz: o rastro e muito mais fraco que o nucleo, porque cada
    posicao intermediaria recebe luz por pouco tempo. Em escala linear ele
    sumiria, que e exatamente o que a figura precisa mostrar.
    """
    if not np.any(soma):
        return
    norm = soma / float(soma.max())
    esticada = np.sqrt(norm)
    cinza = (esticada * 255).astype(np.uint8)
    cv2.imwrite(str(destino.with_name(f"{destino.stem}_{rotulo}.png")), cinza)
    cor = cv2.applyColorMap(cinza, cv2.COLORMAP_INFERNO)
    cv2.imwrite(str(destino.with_name(f"{destino.stem}_{rotulo}_cor.png")), cor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["asi", "ids", "zwo"], default=None)
    parser.add_argument("--horas", type=float, default=10.0)
    parser.add_argument("--intervalo-rajada", type=float, default=10.0,
                        help="minutos entre medidas de convergencia")
    parser.add_argument("--intervalo-imagem", type=float, default=60.0,
                        help="minutos entre gravacoes da imagem empilhada")
    parser.add_argument("--exposicao-us", type=float, default=None,
                        help="exposicao inicial; sobrepoe a do perfil da camera")
    parser.add_argument("--ganho", type=float, default=None,
                        help="ganho analogico; sobrepoe o do perfil da camera")
    return parser.parse_args()


def main(args: argparse.Namespace) -> int:
    # Importados aqui, e nao no topo, porque ``tracker_camera`` le a exposicao
    # do ambiente no momento do import. Sem o perfil da camera ja aplicado o
    # backend cai no padrao ``alpaca`` e o programa tenta conectar a ASI pelo
    # servidor ASCOM, que foi como isto falhou em 2026-09-17.
    from modulos.configuracoes.tracker import (  # noqa: PLC0415
        AUTO_EXPOSURE_MAX_US,
        AUTO_EXPOSURE_MIN_US,
        roi_size_for_backend,
    )
    from modulos.controle.cameras.backend import backend_name  # noqa: PLC0415
    from modulos.controle.mount_em_uso import motivo_de_uso  # noqa: PLC0415
    from modulos.controle.tracker_aquisicao import medir_laser  # noqa: PLC0415
    from modulos.controle.tracker_camera import (  # noqa: PLC0415
        EXPOSURE_SECONDS,
        capture_frame,
        connect_camera,
        disconnect_camera,
        escolher_referencia_tracker,
        latest_raw_frame,
        set_camera_roi_validated,
    )
    from modulos.controle.tracker_exposicao import AutoExposureController  # noqa: PLC0415
    from modulos.visao import detector_ilhas as foco  # noqa: PLC0415

    uso = motivo_de_uso()
    if uso:
        print(f"Camera ou mount em uso ({uso}). Encerre antes de observar.")
        return 2

    sessao = SAIDA_RAIZ / f"sem_correcao_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    sessao.mkdir(parents=True, exist_ok=True)
    print(f"Gravando em {sessao}")
    print("ESTE PROGRAMA NAO MOVE O MOUNT. Ele nao importa nada do mount.")
    print("Deixe o telescopio apontado e parado.\n")

    connect_camera()
    foco.set_focus_mode("dual")
    foco.reset_focus_lock()
    alvo = escolher_referencia_tracker()
    # Mesma ROI e mesma chamada do tracker, de proposito: as duas sessoes so
    # comparam se o recorte do sensor for o mesmo.
    janela = roi_size_for_backend(backend_name())
    largura, altura, alvo_x, alvo_y = set_camera_roi_validated(
        janela,
        janela,
        alvo.x_px,
        alvo.y_px,
        alvo.focus_signature,
    )
    print(f"ROI {largura}x{altura}, alvo em ({alvo_x:.1f}, {alvo_y:.1f})")

    exposicao_us = EXPOSURE_SECONDS * 1e6
    # O teto da config e trava de seguranca pensada para a operacao normal. Se
    # a noite exigir uma exposicao inicial acima dele, prender o controlador
    # abaixo do ponto de partida so faria a imagem escurecer.
    teto_us = max(AUTO_EXPOSURE_MAX_US, exposicao_us)
    if teto_us > AUTO_EXPOSURE_MAX_US:
        print(f"Teto de exposicao elevado para {teto_us:.0f} us "
              f"(config: {AUTO_EXPOSURE_MAX_US:.0f} us).")
    controlador = AutoExposureController(exposicao_us, started_at=time.perf_counter())

    soma_total = np.zeros((altura, largura), dtype=np.float64)
    soma_rajada = np.zeros((altura, largura), dtype=np.float64)
    quadros_rajada = 0
    rajada_ativa = True          # a primeira comeca imediatamente
    proxima_rajada = 0.0
    proxima_imagem = args.intervalo_imagem * 60.0

    csv_path = sessao / "quadros.csv"
    conv_path = sessao / "convergencia.csv"
    arquivo = csv_path.open("w", newline="", encoding="utf-8")
    escritor = csv.writer(arquivo)
    escritor.writerow(["t_s", "data_hora", "x_px", "y_px", "exposicao_us",
                       "pico_bruto", "fundo", "cnr", "hz"])
    conv_arquivo = conv_path.open("w", newline="", encoding="utf-8")
    conv = csv.writer(conv_arquivo)
    conv.writerow(["t_s", "quadros", "segundos", "x_px", "y_px", "exposicao_us"])

    t0 = time.perf_counter()
    n_quadros = 0
    ultimo_t = None
    hz = 0.0
    perdidos = 0
    try:
        while True:
            agora = time.perf_counter() - t0
            if agora >= args.horas * 3600:
                print("\nDuracao pedida atingida.")
                break

            frame = capture_frame(exposicao_us * 1e-6)
            t_amostra = time.perf_counter() - t0
            if ultimo_t is not None:
                instante = 1.0 / max(t_amostra - ultimo_t, 1e-4)
                hz = instante if hz <= 0 else 0.1 * instante + 0.9 * hz
            ultimo_t = t_amostra

            centro = medir_laser(frame)
            bruto = latest_raw_frame()
            estatisticas = foco.LAST_CAPTURE_STATS or {}
            pedestal = float(estatisticas.get("pedestal", 0.0))

            decisao = controlador.observe(
                time.perf_counter(),
                bruto if bruto is not None else frame,
                target_center=centro,
                target_diameter_px=None,
                trusted_target=centro is not None,
                target_present=centro is not None,
            )
            if decisao.changed:
                exposicao_us = float(
                    np.clip(decisao.exposure_us, AUTO_EXPOSURE_MIN_US, teto_us)
                )

            if centro is None:
                perdidos += 1
            else:
                n_quadros += 1
                m = decisao.metrics
                escritor.writerow([
                    f"{t_amostra:.3f}", datetime.now().isoformat(timespec="milliseconds"),
                    f"{centro[0]:.4f}", f"{centro[1]:.4f}", f"{exposicao_us:.0f}",
                    "" if m is None else f"{m.peak:.1f}",
                    "" if m is None else f"{m.local_background:.1f}",
                    "" if m is None else f"{m.cnr:.2f}",
                    f"{hz:.1f}",
                ])
                if bruto is not None and bruto.shape == soma_total.shape:
                    # Normaliza pela exposicao: com a autoexposicao mexendo ao
                    # longo da noite, somar contagens cruas daria mais peso aos
                    # trechos de exposicao longa e falsearia o rastro.
                    fluxo = np.clip(bruto - pedestal, 0.0, None) / max(exposicao_us, 1.0)
                    soma_total += fluxo
                    if rajada_ativa:
                        soma_rajada += fluxo
                        quadros_rajada += 1
                        if quadros_rajada in QUADROS_DA_RAJADA:
                            c = centroide_da_soma(soma_rajada)
                            if c is not None:
                                conv.writerow([
                                    f"{t_amostra:.1f}", quadros_rajada,
                                    f"{quadros_rajada / max(hz, 1e-6):.2f}",
                                    f"{c[0]:.4f}", f"{c[1]:.4f}", f"{exposicao_us:.0f}",
                                ])
                        if quadros_rajada >= max(QUADROS_DA_RAJADA):
                            rajada_ativa = False
                            proxima_rajada = t_amostra + args.intervalo_rajada * 60.0
                            conv_arquivo.flush()
                            print(f"  [{t_amostra/60:6.1f} min] rajada de "
                                  f"{quadros_rajada} quadros concluida")

            if not rajada_ativa and t_amostra >= proxima_rajada:
                soma_rajada = np.zeros_like(soma_rajada)
                quadros_rajada = 0
                rajada_ativa = True

            if t_amostra >= proxima_imagem:
                salvar_empilhada(soma_total, sessao / "empilhada",
                                 f"{int(t_amostra/60):04d}min")
                arquivo.flush()
                f = forma_da_soma(soma_total)
                print(f"  [{t_amostra/60:6.1f} min] empilhada salva; "
                      f"eixo maior {f.get('eixo_maior_px', 0):.1f} px, "
                      f"alongamento {f.get('alongamento') or 0:.2f}, "
                      f"exposicao {exposicao_us:.0f} us")
                proxima_imagem += args.intervalo_imagem * 60.0
    except KeyboardInterrupt:
        print("\nInterrompido pelo operador.")
    finally:
        arquivo.close()
        conv_arquivo.close()
        duracao = time.perf_counter() - t0
        salvar_empilhada(soma_total, sessao / "empilhada", "final")
        np.save(sessao / "empilhada_final.npy", soma_total)
        forma = forma_da_soma(soma_total)
        resumo = {
            "duracao_h": duracao / 3600,
            "quadros_validos": n_quadros,
            "quadros_sem_alvo": perdidos,
            "taxa_hz": n_quadros / max(duracao, 1e-6),
            "roi": [largura, altura],
            "exposicao_final_us": exposicao_us,
            "forma_empilhada": forma,
        }
        (sessao / "resumo.json").write_text(
            json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")
        disconnect_camera()
        print(f"\n{n_quadros} quadros validos em {duracao/3600:.2f} h "
              f"({n_quadros/max(duracao,1e-6):.1f} Hz), {perdidos} sem alvo")
        if forma:
            print(f"empilhada: eixo maior {forma['eixo_maior_px']:.1f} px, "
                  f"menor {forma['eixo_menor_px']:.1f} px, "
                  f"alongamento {forma['alongamento']:.2f}, "
                  f"angulo {forma['angulo_graus']:+.0f} graus")
        print(f"tudo em {sessao}")
    return 0


def aplicar_sobreposicoes(args: argparse.Namespace) -> None:
    """Exposicao e ganho pela linha de comando, sem editar o perfil da camera.

    Precisa rodar DEPOIS de ``aplicar_camera`` e ANTES de qualquer import de
    ``modulos``: a exposicao e lida do ambiente na hora do import. Cada camera
    le variaveis com prefixo proprio, entao a sobreposicao segue o backend.
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

if __name__ == "__main__":
    _args = parse_args()
    _escolha = _args.camera or perguntar_camera({"1": "asi", "2": "ids", "3": "zwo"}, "2")
    print(f"Observando com {aplicar_camera(_escolha)}.")
    aplicar_sobreposicoes(_args)
    raise SystemExit(main(_args))
