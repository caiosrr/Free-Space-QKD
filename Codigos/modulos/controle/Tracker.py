"""Orquestrador do tracker continuo.

Este arquivo mostra apenas o roteiro da sessao. Aquisicao, controle do mount,
seguranca, interface e telemetria ficam nos modulos ``tracker_*`` desta pasta.
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modulos.artefatos import display_path, matrix_candidates
from modulos.configuracoes.tracker import (
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    MAX_SESSION_HOURS,
    RETURN_TO_START_ON_LIMIT,
    SIGNAL_LOSS_LIMIT_SECONDS,
    TEMPORAL_WINDOW_SECONDS,
    roi_size_for_backend,
)
from modulos.controle.cameras.backend import backend_name
from modulos.controle.mount_control import (
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.tracker_aquisicao import executar_aquisicao, medir_laser
from modulos.controle.tracker_camera import (
    EXPOSURE_SECONDS,
    IDS_MATRIX_PREFIX,
    TRACKER_OUTPUT_DIR,
    connect_camera,
    current_roi_size,
    disconnect_camera,
    escolher_referencia_tracker,
    reset_camera_roi,
    set_camera_roi_validated,
)
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_interface import TrackerDisplay
from modulos.controle.tracker_loop import executar_loop_controle
from modulos.controle.tracker_seguranca import (
    monitorar_posicao,
    motivo_permite_retorno,
    retornar_posicao_inicial,
)
from modulos.controle.tracker_telemetria import TrackerCsvLogger
from modulos.visao import detector_ilhas as foco


WINDOW_SIZE = roi_size_for_backend(backend_name())
DISPLAY_HEIGHT_PX = 1080


def carregar_matriz_calibracao() -> tuple[np.ndarray, str]:
    """Carrega a matriz inversa 2x2 da camera selecionada."""
    filename = (
        f"{IDS_MATRIX_PREFIX}_A_inv_fine.npy"
        if backend_name() == "ids"
        else "foco_temp_A_inv_fine.npy"
    )
    candidates = matrix_candidates(filename)
    for path in candidates:
        try:
            matrix = np.load(path)
        except FileNotFoundError:
            continue
        if matrix.shape != (2, 2):
            raise ValueError(f"Matriz {path} precisa ser 2x2.")
        return matrix, display_path(path)
    raise FileNotFoundError(
        "Matriz da calibracao continua nao encontrada. "
        f"Execute a calibracao para {backend_name()}. Testei: "
        + ", ".join(str(path) for path in candidates)
    )


def _ler_tempo_sessao() -> float:
    text = input(
        f"Tempo maximo da sessao em horas [{MAX_SESSION_HOURS:g}]: "
    ).strip()
    hours = float(text.replace(",", ".")) if text else MAX_SESSION_HOURS
    if hours <= 0.0:
        raise ValueError("O tempo maximo da sessao precisa ser positivo.")
    return hours


def main() -> None:
    """Seleciona uma ilha e a mantem no alvo ate o fim da sessao."""
    logger = None
    return_result = None
    finish_reason = "encerramento_normal"
    initial_position = None
    state = None

    try:
        # 1. Prepara mount, camera, calibracao e alvo.
        ensure_connected()
        ensure_unparked()
        ensure_not_tracking()
        connect_camera()
        foco.set_focus_mode("dual")

        session_hours = _ler_tempo_sessao()
        A_inv, matrix_path = carregar_matriz_calibracao()
        target = escolher_referencia_tracker()
        if target.focus_signature is None:
            raise RuntimeError("A selecao manual nao produziu uma assinatura da ilha.")

        state = TrackerState(exposure_us=EXPOSURE_SECONDS * 1e6)
        initial_az, initial_alt = read_altaz()
        initial_position = (initial_az, initial_alt)
        session_started = time.perf_counter()
        with state.lock:
            state.mount_az_deg = initial_az
            state.mount_alt_deg = initial_alt

        logger = TrackerCsvLogger(
            TRACKER_OUTPUT_DIR,
            session_started,
            initial_az,
            initial_alt,
            session_hours,
        )
        print(
            f"\nTracker iniciado | ilha travada | media={TEMPORAL_WINDOW_SECONDS:.1f}s"
        )
        print(f"Calibracao continua: {matrix_path}")
        print(f"Posicao inicial: Az={initial_az:.6f} deg | Alt={initial_alt:.6f} deg")
        print(
            f"Seguranca: limites Az/Alt=+/-{MAX_OFFSET_AZ_DEG:g}/"
            f"+/-{MAX_OFFSET_ALT_DEG:g} deg | perda de sinal="
            f"{SIGNAL_LOSS_LIMIT_SECONDS:.0f}s"
        )
        print(f"Telemetria: {display_path(logger.csv_path)}")
        print("Q, Esc ou Ctrl+C encerra com velocidade zero.\n")

        # 2. Centraliza a ROI e inicia controle e watchdog independentes.
        _, _, target_x, target_y = set_camera_roi_validated(
            WINDOW_SIZE,
            WINDOW_SIZE,
            target.x_px,
            target.y_px,
            target.focus_signature,
        )
        roi_w, roi_h = current_roi_size(WINDOW_SIZE)
        display = TrackerDisplay(
            roi_w,
            roi_h,
            target_x,
            target_y,
            DISPLAY_HEIGHT_PX,
        )

        control_thread = threading.Thread(
            target=executar_loop_controle,
            args=(state, A_inv),
            daemon=True,
        )
        watchdog_thread = threading.Thread(
            target=monitorar_posicao,
            args=(
                state,
                initial_az,
                initial_alt,
                session_started,
                session_hours * 3600.0,
            ),
            daemon=True,
        )
        control_thread.start()
        watchdog_thread.start()

        # 3. Adquire frames e publica uma medida temporal para o controle.
        result = executar_aquisicao(
            state,
            logger,
            display,
            target_x=target_x,
            target_y=target_y,
            session_started=session_started,
            session_hours=session_hours,
        )
        finish_reason = result.motivo

    except KeyboardInterrupt:
        finish_reason = "interrompido_por_ctrl_c"
        print("\nInterrompido pelo usuario.")
    except Exception as exc:
        finish_reason = f"erro_{type(exc).__name__}"
        print(f"\nErro no tracker: {exc}")
    finally:
        # 4. Para threads/mount antes de retornar, salvar e desconectar.
        safety_reason = None
        if state is not None:
            state.request_stop()
            safety_reason = state.snapshot()["safety_stop_reason"]
            if "control_thread" in locals() and control_thread.is_alive():
                control_thread.join(timeout=2.0)
            if "watchdog_thread" in locals() and watchdog_thread.is_alive():
                watchdog_thread.join(timeout=2.0)

        stop_axes_safely()

        if (
            safety_reason
            and initial_position is not None
            and RETURN_TO_START_ON_LIMIT
            and motivo_permite_retorno(safety_reason)
        ):
            print(f"Retornando a posicao inicial (motivo: {safety_reason}).")
            return_result = retornar_posicao_inicial(*initial_position)
            print(
                "Posicao inicial restaurada."
                if return_result.get("success")
                else f"ALERTA: retorno nao confirmado: {return_result}"
            )
        elif safety_reason == "sinal_perdido_por_tempo_excessivo":
            print("Sinal ausente: mount parado, sem busca ou retorno cego.")

        if logger is not None:
            try:
                logger.close(reason=finish_reason, return_result=return_result)
                print(f"Telemetria salva em: {display_path(logger.csv_path)}")
            except Exception as exc:
                print(f"ALERTA: nao consegui finalizar o CSV: {exc}")

        reset_camera_roi()
        try:
            disconnect_camera()
        except Exception:
            pass
        TrackerDisplay.close()
        print("Tracker encerrado com o mount parado.")


if __name__ == "__main__":
    main()
