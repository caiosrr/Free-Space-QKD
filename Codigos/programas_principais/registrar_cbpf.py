"""Registrador do CBPF: grava onde o beacon da UFF chega e quanto acopla na fibra.

Roda no PC do CBPF, a noite toda, sem mexer em nada. Nao conversa com a UFF: os
dois lados gravam com o relogio do Windows sincronizado, e a analise cruza os
arquivos pelo horario. Cada linha tem o instante em segundos UTC (t_unix), que
e a chave dessa juncao.

O que ele grava, por linha:

    camera        centroide do ponto, pico, fundo, fluxo e pixels saturados.
                  Se a camera ve o feixe ANTES da fibra, o centroide e a
                  posicao do feixe da UFF; se ve a saida da fibra, o fluxo ja
                  diz o acoplamento.
    power meter   potencia em watts. Mede o acoplamento na fibra, que e a
                  figura de merito do enlace.

Cruzado com a telemetria do tracker, que marca os blocos com e sem correcao
(coluna correcao_ativa), responde se o controle melhora o acoplamento no CBPF,
e se o feixe fica parado la quando o erro na UFF fica pequeno (reciprocidade).

Nao importa nada do mount. Precisa do IDS peak Cockpit e do app da Thorlabs
FECHADOS, porque cada um segura o seu instrumento.

Uso, a partir da pasta Codigos:

    python programas_principais/registrar_cbpf.py --teste
    python programas_principais/registrar_cbpf.py --horas 10
    python programas_principais/registrar_cbpf.py --horas 10 --sem-camera
    python programas_principais/registrar_cbpf.py --horas 10 --exposicao-us 5000 --ganho 2
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parent.parent
if str(CODIGOS_DIR) not in sys.path:
    sys.path.insert(0, str(CODIGOS_DIR))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from modulos.configuracoes import saidas  # noqa: E402
from programas_principais._iniciador import aplicar_camera  # noqa: E402

COMPRIMENTO_DE_ONDA_NM = 632.8      # beacon vermelho da UFF; conferir
SINAL_MINIMO = 10.0                  # contagens acima do fundo para haver ponto
IMAGEM_A_CADA_S = 600.0

COLUNAS = ["t_unix", "data_hora", "x_px", "y_px", "pico", "fundo", "fluxo",
           "saturados", "potencia_w", "erro"]


def medir_ponto(quadro: np.ndarray, sinal_minimo: float = SINAL_MINIMO) -> dict | None:
    """Centroide do ponto mais brilhante, ignorando reflexos fantasmas.

    Tira o fundo pela mediana, suaviza para o ruido de pixel nao decidir onde
    esta o maximo, e pega so a mancha CONECTADA ao maximo acima de meia
    altura. Um reflexo separado, como os da estrutura da camera na bancada da
    USP, fica fora da conta mesmo passando da meia altura.
    """
    f = quadro.astype(np.float64)
    if f.ndim == 3:
        f = f.mean(axis=2)
    fundo = float(np.median(f))
    liquido = f - fundo
    suave = cv2.GaussianBlur(liquido, (0, 0), 2.0)
    pico_suave = float(suave.max())
    if pico_suave < sinal_minimo:
        return None
    mascara = (suave > 0.5 * pico_suave).astype(np.uint8)
    _, rotulos = cv2.connectedComponents(mascara)
    iy, ix = np.unravel_index(int(np.argmax(suave)), suave.shape)
    mancha = rotulos == rotulos[iy, ix]
    ys, xs = np.nonzero(mancha)
    pesos = np.clip(liquido[mancha], 0.0, None)
    if pesos.sum() <= 0:
        return None
    saturacao = 255 if quadro.dtype == np.uint8 else float(np.iinfo(quadro.dtype).max)
    return {
        "x_px": float(np.sum(xs * pesos) / pesos.sum()),
        "y_px": float(np.sum(ys * pesos) / pesos.sum()),
        "pico": float(f.max()),
        "fundo": fundo,
        "fluxo": float(pesos.sum()),
        "saturados": int((quadro >= saturacao).sum()),
    }


def estado_do_relogio() -> str:
    """Saida do w32tm, para saber depois quao confiavel era o relogio."""
    try:
        return subprocess.run(["w32tm", "/query", "/status"], capture_output=True,
                              text=True, timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as exc:
        return f"w32tm indisponivel: {type(exc).__name__}: {exc}"


def aplicar_sobreposicoes(args: argparse.Namespace) -> None:
    """Exposicao e ganho pela linha de comando, antes de importar a camera."""
    zwo = os.environ.get("QKD_CAMERA_BACKEND") == "zwo_sdk"
    if args.exposicao_us is not None:
        os.environ["QKD_ZWO_EXPOSURE_US" if zwo else "QKD_IDS_EXPOSURE_US"] = str(args.exposicao_us)
        if not zwo:
            fps = min(50.0, 0.9 / (args.exposicao_us * 1e-6))
            os.environ["QKD_IDS_FPS"] = str(round(fps, 3))
    if args.ganho is not None:
        os.environ["QKD_ZWO_GAIN" if zwo else "QKD_IDS_ANALOG_GAIN"] = str(args.ganho)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", choices=["ids", "zwo"], default="ids")
    parser.add_argument("--horas", type=float, default=10.0)
    parser.add_argument("--intervalo", type=float, default=0.2,
                        help="segundos entre linhas gravadas (padrao 0,2, ou 5 por segundo)")
    parser.add_argument("--exposicao-us", type=float, default=None)
    parser.add_argument("--ganho", type=float, default=None)
    parser.add_argument("--sem-camera", action="store_true")
    parser.add_argument("--sem-pm", action="store_true")
    parser.add_argument("--pm-recurso", default=None, help="nome VISA do PM100, se houver mais de um")
    parser.add_argument("--teste", action="store_true",
                        help="10 s imprimindo cada leitura, sem gravar, para conferir")
    args = parser.parse_args()

    camera = pm = None
    if not args.sem_camera:
        print(f"Camera: {aplicar_camera(args.camera)}")
        aplicar_sobreposicoes(args)
        from modulos.controle.cameras.backend import direct_camera  # noqa: PLC0415
        from modulos.visao import detector_ilhas as foco  # noqa: PLC0415
        camera = direct_camera()
        try:
            camera.connect()
        except Exception as exc:
            print(f"Nao consegui abrir a camera: {exc}")
            print("Ela esta plugada? O Cockpit ou o ASICap estao fechados?")
            print("Para registrar so o power meter, use --sem-camera.")
            return 1
        exposicao_s = foco.EXPOSURE_SECONDS
    if not args.sem_pm:
        from modulos.instrumentos.pm100 import PM100Reader  # noqa: PLC0415
        pm = PM100Reader(COMPRIMENTO_DE_ONDA_NM, args.pm_recurso)
        print(f"Power meter: {getattr(pm, 'idn', '?')} | {COMPRIMENTO_DE_ONDA_NM} nm")
    if camera is None and pm is None:
        print("Nada para registrar: tirou camera e power meter.")
        return 1

    duracao = 10.0 if args.teste else args.horas * 3600.0
    pasta = None
    if not args.teste:
        pasta = saidas.CBPF_OUTPUT_DIR / f"cbpf_{datetime.now():%Y-%m-%d_%H-%M-%S}"
        pasta.mkdir(parents=True, exist_ok=True)
        metadados = {
            "inicio_local": datetime.now().isoformat(timespec="seconds"),
            "inicio_t_unix": time.time(),
            "camera": None if camera is None else args.camera,
            "exposicao_s": None if camera is None else exposicao_s,
            "power_meter": None if pm is None else getattr(pm, "idn", "?"),
            "comprimento_de_onda_nm": COMPRIMENTO_DE_ONDA_NM,
            "relogio_no_inicio": estado_do_relogio(),
        }
        (pasta / "metadados.json").write_text(json.dumps(metadados, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        print(f"Gravando em {pasta}")
    print(f"{'Teste de 10 s' if args.teste else f'{args.horas:g} h'}; Ctrl+C encerra.\n")

    arquivo = escritor = None
    if pasta is not None:
        arquivo = (pasta / "registro.csv").open("w", newline="", encoding="utf-8")
        escritor = csv.writer(arquivo)
        escritor.writerow(COLUNAS)

    inicio = time.monotonic()
    proxima_linha = 0.0
    proxima_imagem = 0.0
    ultimo_flush = inicio
    linhas = 0
    try:
        while time.monotonic() - inicio < duracao:
            agora = time.monotonic() - inicio
            if agora < proxima_linha:
                time.sleep(min(0.01, proxima_linha - agora))
                continue
            proxima_linha = agora + args.intervalo
            t_unix = time.time()
            erros = []
            medida = None
            if camera is not None:
                try:
                    quadro = camera.capture(exposicao_s)
                    medida = medir_ponto(quadro)
                    if medida is None:
                        erros.append("sem_ponto")
                    if pasta is not None and agora >= proxima_imagem:
                        cv2.imwrite(str(pasta / f"quadro_{datetime.now():%H-%M-%S}.png"), quadro)
                        proxima_imagem = agora + IMAGEM_A_CADA_S
                except Exception as exc:
                    erros.append(f"camera:{type(exc).__name__}")
            potencia = None
            if pm is not None:
                try:
                    potencia = pm.read_power_w()
                except Exception as exc:
                    erros.append(f"pm:{type(exc).__name__}")
            m = medida or {}
            linha = [f"{t_unix:.3f}", datetime.now().isoformat(timespec="milliseconds"),
                     m.get("x_px", ""), m.get("y_px", ""), m.get("pico", ""), m.get("fundo", ""),
                     m.get("fluxo", ""), m.get("saturados", ""),
                     "" if potencia is None else f"{potencia:.6e}", ";".join(erros)]
            if args.teste:
                pos = "sem ponto" if not m else f"({m['x_px']:.1f}, {m['y_px']:.1f}) pico {m['pico']:.0f}"
                pot = "" if potencia is None else f" | {potencia * 1e6:.2f} uW"
                print(f"  {agora:5.1f} s  {pos}{pot}  {';'.join(erros)}")
            else:
                escritor.writerow(linha)
                linhas += 1
                if time.monotonic() - ultimo_flush >= 1.0:
                    arquivo.flush()
                    ultimo_flush = time.monotonic()
                if linhas % 3000 == 0:
                    print(f"[{datetime.now():%H:%M:%S}] {linhas} linhas")
    except KeyboardInterrupt:
        print("\nEncerrado pelo operador.")
    finally:
        if arquivo is not None:
            arquivo.close()
            (pasta / "relogio_no_fim.txt").write_text(estado_do_relogio(), encoding="utf-8")
            print(f"{linhas} linhas em {pasta / 'registro.csv'}")
        if camera is not None:
            try:
                camera.disconnect()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
