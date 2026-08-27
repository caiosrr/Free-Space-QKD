"""Alinhamento lento e continuo baseado na ilha luminosa selecionada.

Este programa fica entre a centralizacao unica e o tracker de alta frequencia:
mede varias imagens, usa a mediana do centro da mesma ilha e corrige somente a
deriva persistente. O modo de observacao e o padrao e nunca conecta o mount.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from modulos.artefatos import display_path, matrix_candidates
from modulos.controle.alvo_alinhamento import escolher_posicao_inicial_ou_centro
from modulos.controle.mapa_jacobianas import MapaJacobianas, SelecaoJacobiana
from modulos.controle.mount_control import (
    calc_error,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    move_axes_pid_2d,
    read_altaz,
    stop_axes_safely,
)


AMOSTRAS_POR_MEDICAO = 5
MIN_AMOSTRAS_VALIDAS = 3
INTERVALO_CORRECAO_PADRAO_S = 2.0
TEMPO_SEM_SINAL_LIMITE_S = 75.0
RAIO_ENTRADA_REPOUSO_PX = 2.0
RAIO_SAIDA_REPOUSO_PX = 3.5
MAX_SALTO_ILHA_PX = 180.0
MAX_OFFSET_PADRAO_DEG = 0.25
LIMITE_LOCAL_SEM_MAPA_DEG = 0.05


@dataclass(frozen=True)
class Medicao:
    x_px: float
    y_px: float
    intensidade: float
    amostras_validas: int
    amostras_tentadas: int
    toca_borda: bool


def _ler_float(prompt: str, padrao: float, minimo: float) -> float:
    texto = input(f"{prompt} [{padrao:g}]: ").strip()
    valor = padrao if not texto else float(texto.replace(",", "."))
    if valor < minimo:
        raise ValueError(f"O valor de '{prompt}' precisa ser >= {minimo:g}.")
    return valor


def _medir_ilha(foco, quantidade: int = AMOSTRAS_POR_MEDICAO) -> tuple[Medicao | None, np.ndarray]:
    medidas = []
    ultimo_frame = None
    for _ in range(max(1, int(quantidade))):
        ultimo_frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
        cm = foco.centro_massa(ultimo_frame)
        if cm is None:
            continue
        x_px, y_px, intensidade, toca_borda = cm
        if toca_borda:
            continue
        medidas.append((float(x_px), float(y_px), float(intensidade)))

    if ultimo_frame is None:
        raise RuntimeError("Nenhum frame foi capturado.")
    if len(medidas) < min(MIN_AMOSTRAS_VALIDAS, quantidade):
        return None, ultimo_frame

    valores = np.asarray(medidas, dtype=float)
    return (
        Medicao(
            x_px=float(np.median(valores[:, 0])),
            y_px=float(np.median(valores[:, 1])),
            intensidade=float(np.median(valores[:, 2])),
            amostras_validas=len(medidas),
            amostras_tentadas=quantidade,
            toca_borda=False,
        ),
        ultimo_frame,
    )


def _carregar_mapa(foco) -> MapaJacobianas | None:
    if foco.backend_name() == "ids":
        nomes = [f"{foco.IDS_MATRIX_PREFIX}_mapa_jacobianas.json"]
    else:
        nomes = ["foco_temp_mapa_jacobianas.json", "mapa_jacobianas.json"]
    for caminho in matrix_candidates(*nomes):
        if not caminho.exists():
            continue
        mapa = MapaJacobianas.carregar(caminho)
        print(f"Mapa de Jacobianas carregado: {display_path(caminho)} ({len(mapa.nos)} nos)")
        return mapa
    return None


def _offset_mount(inicial_az: float, inicial_alt: float, az: float, alt: float) -> tuple[float, float]:
    return -float(calc_error(0, inicial_az, az)), float(alt - inicial_alt)


def _selecionar_matriz(
    foco,
    mapa: MapaJacobianas | None,
    matrizes_legadas,
    az_deg: float,
    alt_deg: float,
    raio_px: float,
) -> tuple[str, np.ndarray, SelecaoJacobiana | None] | None:
    if mapa is not None:
        selecao = mapa.selecionar(az_deg, alt_deg)
        if selecao is None:
            return None
        return selecao.nome, selecao.A_inv, selecao
    nome, A_inv = foco._select_A_inv(matrizes_legadas, raio_px)
    return f"local:{nome}", A_inv, None


def _limitar_passo_a_cobertura(
    mapa: MapaJacobianas,
    az_deg: float,
    alt_deg: float,
    daz_deg: float,
    dalt_deg: float,
    amostras_trajeto: int = 20,
) -> tuple[float, float, float] | None:
    """Mantem todo o segmento do comando dentro da uniao das regioes validas."""
    ultima_fracao_valida = 0.0
    for fracao in np.linspace(0.05, 1.0, max(2, int(amostras_trajeto))):
        proximo_az = (float(az_deg) + fracao * float(daz_deg)) % 360.0
        proxima_alt = float(alt_deg) + fracao * float(dalt_deg)
        if mapa.selecionar(proximo_az, proxima_alt) is None:
            break
        ultima_fracao_valida = float(fracao)
    if ultima_fracao_valida <= 0.0:
        return None
    # Margem para erros de posicionamento e para a discretizacao acima.
    escala = min(1.0, 0.9 * ultima_fracao_valida)
    return float(daz_deg * escala), float(dalt_deg * escala), escala


def _limitar_passo_por_offset(
    daz_deg: float,
    dalt_deg: float,
    offset_az_deg: float,
    offset_alt_deg: float,
    limite_deg: float,
) -> tuple[float, float, float] | None:
    """Reduz o comando para que o destino continue dentro do limite por eixo."""
    escala = 1.0
    for comando, offset in ((daz_deg, offset_az_deg), (dalt_deg, offset_alt_deg)):
        destino = float(offset) + float(comando)
        if abs(destino) <= limite_deg:
            continue
        if abs(comando) <= 1e-12:
            return None
        fronteira = np.sign(destino) * limite_deg
        escala_eixo = (fronteira - float(offset)) / float(comando)
        escala = min(escala, float(escala_eixo))
    if escala <= 0.01:
        return None
    escala = min(1.0, max(0.0, 0.95 * escala))
    return float(daz_deg * escala), float(dalt_deg * escala), escala


class Telemetria:
    CAMPOS = [
        "data_hora", "tempo_s", "estado", "sinal", "x_cm_px", "y_cm_px",
        "alvo_x_px", "alvo_y_px", "erro_x_px", "erro_y_px", "distancia_px",
        "amostras_validas", "amostras_tentadas", "azimute_deg", "altitude_deg",
        "offset_az_deg", "offset_alt_deg", "matriz", "daz_comando_deg",
        "dalt_comando_deg", "movimento_habilitado", "evento",
    ]

    def __init__(self, raiz: Path, inicio: float, configuracao: dict):
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.pasta = Path(raiz) / "sessoes" / f"alinhamento_{stamp}"
        self.pasta.mkdir(parents=True, exist_ok=False)
        self.csv_path = self.pasta / "telemetria.csv"
        self.resumo_path = self.pasta / "resumo.json"
        self._arquivo = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._arquivo, fieldnames=self.CAMPOS)
        self._writer.writeheader()
        self._inicio = inicio
        self._configuracao = configuracao
        self._linhas = 0
        self._movimentos = 0

    def escrever(self, agora: float, **linha) -> None:
        registro = {campo: linha.get(campo, "") for campo in self.CAMPOS}
        registro["data_hora"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        registro["tempo_s"] = round(agora - self._inicio, 3)
        self._writer.writerow(registro)
        self._linhas += 1
        if (
            linha.get("daz_comando_deg", "") not in ("", 0, 0.0)
            or linha.get("dalt_comando_deg", "") not in ("", 0, 0.0)
        ):
            self._movimentos += 1
        self._arquivo.flush()

    def fechar(self, motivo: str) -> None:
        if self._arquivo.closed:
            return
        self._arquivo.flush()
        self._arquivo.close()
        resumo = {
            **self._configuracao,
            "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stopped_by": motivo,
            "duration_s": time.monotonic() - self._inicio,
            "telemetry_rows": self._linhas,
            "mount_movements": self._movimentos,
            "csv_path": display_path(self.csv_path),
        }
        self.resumo_path.write_text(json.dumps(resumo, indent=2), encoding="utf-8")


def executar(foco) -> None:
    """Executa observacao ou alinhamento; `foco` e o modulo centro_massa ativo."""
    foco.set_focus_mode("dual")
    print("\nCentro de massa continuo — detector por ilhas")
    print("  1 = observar e gravar CSV, sem conectar/mover o mount")
    print("  2 = alinhar continuamente com correcoes lentas")
    modo = input("Escolha [1]: ").strip() or "1"
    if modo not in {"1", "2"}:
        raise ValueError("Escolha 1 para observar ou 2 para mover o mount.")
    mover_mount = modo == "2"

    duracao_h = _ler_float("Duracao maxima em horas", 1.0, 1.0 / 60.0)
    intervalo_s = _ler_float(
        "Intervalo minimo entre medicoes/correcoes em segundos",
        INTERVALO_CORRECAO_PADRAO_S,
        0.0,
    )
    max_offset_deg = (
        _ler_float("Limite absoluto por eixo desde o inicio (graus)", MAX_OFFSET_PADRAO_DEG, 0.001)
        if mover_mount
        else 0.0
    )

    inicio = time.monotonic()
    limite_tempo = inicio + duracao_h * 3600.0
    telemetria = None
    motivo = "fim_normal"
    inicial_mount = None
    matrizes = None
    mapa = None
    ultimo_movimento = None
    em_repouso = False
    perda_iniciada = None

    foco.connect_camera()
    try:
        foco.set_gain(foco.CAMERA_GAIN)
        frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
        print("Selecione a ilha que devera permanecer travada durante toda a sessao.")
        selecao_manual = foco.escolher_ilha_manualmente(
            frame,
            max_jump_px=MAX_SALTO_ILHA_PX,
        )
        cm = foco.centro_massa(frame)
        if cm is None:
            raise RuntimeError("A ilha selecionada nao foi confirmada no frame inicial.")
        x_inicial, y_inicial, _, _ = cm
        alvo = escolher_posicao_inicial_ou_centro(
            frame,
            x_inicial,
            y_inicial,
            prompt="Referencia do alinhamento continuo",
            default_choice="3",
            focus_mode="dual",
            focus_signature=selecao_manual["signature"],
        )

        if mover_mount:
            ensure_connected()
            ensure_unparked()
            ensure_not_tracking()
            inicial_mount = read_altaz()
            mapa = _carregar_mapa(foco)
            matrizes = foco._load_calibration_matrices()
            if mapa is None and matrizes is None:
                raise RuntimeError("Nao ha mapa de Jacobianas nem matriz local para corrigir.")
            if mapa is None:
                print(
                    "AVISO: usando apenas a matriz fine/coarse local. O programa recusara "
                    f"afastar-se mais de {LIMITE_LOCAL_SEM_MAPA_DEG:.3f} deg da posicao inicial."
                )

        configuracao = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": "continuous_alignment" if mover_mount else "observe_only",
            "camera_backend": foco.backend_name(),
            "duration_limit_h": duracao_h,
            "measurement_interval_s": intervalo_s,
            "samples_per_measurement": AMOSTRAS_POR_MEDICAO,
            "target_x_px": alvo.x_px,
            "target_y_px": alvo.y_px,
            "max_offset_deg": max_offset_deg,
            "jacobian_map_loaded": mapa is not None,
        }
        telemetria = Telemetria(foco.FOCO_DIR / "alinhamento_continuo", inicio, configuracao)
        print(f"Telemetria: {display_path(telemetria.csv_path)}")

        while time.monotonic() < limite_tempo:
            ciclo_inicio = time.monotonic()
            medicao, frame = _medir_ilha(foco)
            agora = time.monotonic()
            if medicao is None:
                if perda_iniciada is None:
                    perda_iniciada = agora
                    evento = "sinal_perdido"
                    if mover_mount:
                        stop_axes_safely()
                else:
                    evento = ""
                tempo_perdido = agora - perda_iniciada
                telemetria.escrever(
                    agora,
                    estado="SEM_SINAL",
                    sinal=0,
                    alvo_x_px=round(alvo.x_px, 3),
                    alvo_y_px=round(alvo.y_px, 3),
                    amostras_validas=0,
                    amostras_tentadas=AMOSTRAS_POR_MEDICAO,
                    movimento_habilitado=int(mover_mount),
                    evento=evento,
                )
                print(f"Sem a ilha selecionada ha {tempo_perdido:.1f}s; mount parado.")
                if tempo_perdido >= TEMPO_SEM_SINAL_LIMITE_S:
                    motivo = "tempo_sem_sinal_excedido"
                    break
                time.sleep(max(0.0, intervalo_s - (time.monotonic() - ciclo_inicio)))
                continue

            evento = ""
            if perda_iniciada is not None:
                evento = "sinal_recuperado"
                perda_iniciada = None

            dx = medicao.x_px - alvo.x_px
            dy = medicao.y_px - alvo.y_px
            raio_px = float(np.hypot(dx, dy))
            if mover_mount and ultimo_movimento is not None:
                raio_anterior, daz_anterior, dalt_anterior = ultimo_movimento
                limite_piora = max(raio_anterior * foco.WORSE_ABORT_FACTOR,
                                   raio_anterior + foco.WORSE_ABORT_MARGIN_PX)
                if raio_px > limite_piora:
                    print(
                        "A ultima correcao piorou muito o erro "
                        f"({raio_anterior:.2f}px -> {raio_px:.2f}px); revertendo e encerrando."
                    )
                    move_axes_pid_2d(True, -daz_anterior, -dalt_anterior)
                    motivo = "correcao_piorou_rollback_executado"
                    evento = motivo
                ultimo_movimento = None
            if em_repouso:
                em_repouso = raio_px <= RAIO_SAIDA_REPOUSO_PX
            else:
                em_repouso = raio_px <= RAIO_ENTRADA_REPOUSO_PX

            az = alt = offset_az = offset_alt = ""
            matriz_nome = "observacao"
            daz = dalt = ""
            estado = "REPOUSO" if em_repouso else "DESVIO"

            if mover_mount and motivo == "fim_normal":
                az, alt = read_altaz()
                offset_az, offset_alt = _offset_mount(*inicial_mount, az, alt)
                if abs(offset_az) >= max_offset_deg or abs(offset_alt) >= max_offset_deg:
                    motivo = "limite_absoluto_do_mount"
                    evento = motivo
                elif mapa is None and max(abs(offset_az), abs(offset_alt)) >= LIMITE_LOCAL_SEM_MAPA_DEG:
                    motivo = "fora_da_faixa_da_matriz_local"
                    evento = motivo
                elif not em_repouso:
                    escolha = _selecionar_matriz(foco, mapa, matrizes, az, alt, raio_px)
                    if escolha is None:
                        motivo = "sem_jacobiana_validada_nesta_posicao"
                        evento = motivo
                    else:
                        matriz_nome, A_inv, _ = escolha
                        bruto = A_inv @ np.asarray([-dx, -dy], dtype=float)
                        daz, dalt, _ = foco._limit_correction(float(bruto[0]), float(bruto[1]))
                        limite_ativo = max_offset_deg
                        if mapa is None:
                            limite_ativo = min(limite_ativo, LIMITE_LOCAL_SEM_MAPA_DEG)
                        passo = _limitar_passo_por_offset(
                            daz, dalt, offset_az, offset_alt, limite_ativo
                        )
                        if passo is None:
                            motivo = "sem_passo_seguro_dentro_do_limite_absoluto"
                            evento = motivo
                        else:
                            daz, dalt, _ = passo
                            if mapa is not None:
                                passo_mapa = _limitar_passo_a_cobertura(
                                    mapa, az, alt, daz, dalt
                                )
                                if passo_mapa is None:
                                    motivo = "sem_passo_continuo_na_cobertura_do_mapa"
                                    evento = motivo
                                else:
                                    daz, dalt, _ = passo_mapa
                            if motivo == "fim_normal":
                                move_axes_pid_2d(True, daz, dalt)
                                ultimo_movimento = (raio_px, daz, dalt)
                                estado = "CORRIGINDO"

            telemetria.escrever(
                agora,
                estado=estado,
                sinal=1,
                x_cm_px=round(medicao.x_px, 3),
                y_cm_px=round(medicao.y_px, 3),
                alvo_x_px=round(alvo.x_px, 3),
                alvo_y_px=round(alvo.y_px, 3),
                erro_x_px=round(dx, 3),
                erro_y_px=round(dy, 3),
                distancia_px=round(raio_px, 3),
                amostras_validas=medicao.amostras_validas,
                amostras_tentadas=medicao.amostras_tentadas,
                azimute_deg="" if az == "" else round(az, 6),
                altitude_deg="" if alt == "" else round(alt, 6),
                offset_az_deg="" if offset_az == "" else round(offset_az, 6),
                offset_alt_deg="" if offset_alt == "" else round(offset_alt, 6),
                matriz=matriz_nome,
                daz_comando_deg="" if daz == "" else round(daz, 7),
                dalt_comando_deg="" if dalt == "" else round(dalt, 7),
                movimento_habilitado=int(mover_mount),
                evento=evento,
            )
            print(
                f"{estado:10s} | erro=({dx:+7.2f}, {dy:+7.2f}) px | "
                f"raio={raio_px:6.2f}px | matriz={matriz_nome}"
            )
            if motivo != "fim_normal":
                break
            time.sleep(max(0.0, intervalo_s - (time.monotonic() - ciclo_inicio)))
        else:
            motivo = "duracao_atingida"
    except KeyboardInterrupt:
        motivo = "interrompido_por_ctrl_c"
        print("\nInterrompido pelo usuario.")
    finally:
        try:
            if mover_mount:
                stop_axes_safely()
        finally:
            try:
                foco.disconnect_camera()
            finally:
                if telemetria is not None:
                    telemetria.fechar(motivo)
                    print(f"CSV salvo em: {display_path(telemetria.csv_path)}")
        print(f"Encerramento: {motivo}")
