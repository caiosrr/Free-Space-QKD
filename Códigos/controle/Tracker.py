"""Tracker continuo do feixe, organizado em um unico programa.

Resumo do fluxo
---------------
1. Conecta o mount e a camera selecionada (IDS ou ASI/ASCOM).
2. Carrega as matrizes produzidas pela calibracao.
3. Encontra o foco salvo e recorta uma ROI ao redor dele.
4. Mede ``dx`` e ``dy`` entre o foco e o alvo.
5. Converte pixels em erro angular e calcula a velocidade dos dois eixos.
6. Freia se perder o sinal, detectar salto ou observar divergencia.
7. Ao sair com Q, Esc ou Ctrl+C, envia velocidade zero aos dois eixos.

O codigo esta dividido em blocos numerados. Exposicao e ganho ficam nas fontes
unicas ``config_camera_asi.py`` (programas normais) e
``Link UFF/config_camera_ids.py`` (IDS).
"""

import csv
import itertools
import json
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

cv2.setUseOptimized(True)

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from artifact_paths import display_path, matrix_candidates
from config_camera_asi import ALPACA_ADDRESS, DEVICE_NUMBER
from config_camera_asi import EXPOSURE_SECONDS as ASI_EXPOSURE_SECONDS
from config_camera_asi import GAIN as ASI_GAIN
from config_tracker import (
    BORDER_CONFIRM_FRAMES,
    CSV_FLUSH_SECONDS,
    CSV_LOG_HZ,
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_CONFIRM_FRAMES,
    HOLD_EXIT_RADIUS_PX,
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    MAX_SESSION_HOURS,
    MAX_TRACKING_RATE_DEG_S,
    POSITION_WATCHDOG_HZ,
    RETURN_ATTEMPTS,
    RETURN_MAX_RATE_DEG_S,
    RETURN_TO_START_ON_LIMIT,
    RETURN_TOLERANCE_DEG,
    SIGNAL_LOSS_LIMIT_SECONDS,
    TRACKER_MAX_SPOT_JUMP_PX,
    VARIANCE_WINDOW_SECONDS,
    WATCHDOG_READ_FAILURES,
    roi_size_for_backend,
)
from controle.alvo_alinhamento import (
    AlvoAlinhamento,
    carregar_alvo_salvo,
    escolher_posicao_inicial_ou_centro,
    roi_incluindo_alvo,
)
from controle.mount_control import ensure_connected, ensure_not_tracking, ensure_unparked
from controle.mount_control import (
    VEL_MAX_LIMITE,
    VEL_MIN_LIMITE,
    calc_error,
    move_axes_pid_2d,
    move_axis,
    read_altaz,
    stop_axes_safely,
)
from controle.camera_backend import backend_name
from foco_multiplos import Center_of_Mass_foco_temp as foco_temp


# =============================================================================
# BLOCO 1 - CONFIGURACAO
# =============================================================================
# Edite este bloco para ajustar o controle. Ganho e exposicao da camera ficam
# nos arquivos de configuracao citados acima. A direcao dos movimentos NAO deve
# ser corrigida trocando sinais aqui: ela vem das matrizes da calibracao.

# Endereco usado somente pela camera ASI/ASCOM. A IDS ignora estes valores.
BASE_URL = f"http://{ALPACA_ADDRESS}/api/v1/camera/{DEVICE_NUMBER}"
CLIENT_ID = 1
IMAGE_READY_POLL_S = 0.001
IMAGE_READY_SPIN_POLLS = 3
_transaction_ids = itertools.count(1)
session = requests.Session()

# Imagem e frequencia de operacao.
WINDOW_SIZE = roi_size_for_backend(backend_name())
TARGET_H = 1080
DISPLAY_HZ = 6.0

# Tolerancia visual e zona morta do controle, ambas em pixels.
TOLERANCIA_PX = HOLD_ENTER_RADIUS_PX
EXPOSURE_SECONDS = (
    float(os.environ.get("QKD_IDS_EXPOSURE_US", "7276")) * 1e-6
    if backend_name() == "ids"
    else ASI_EXPOSURE_SECONDS
)
ROTATE_IMAGE_180 = os.environ.get("QKD_ROTATE_IMAGE_180", "1") != "0"
IDS_MATRIX_PREFIX = "ids_foco_temp" if ROTATE_IMAGE_180 else "ids_raw_foco_temp"
CONTROL_HZ = float(os.environ.get("QKD_IDS_FPS", "20")) if backend_name() == "ids" else 45.0
TRACKER_OUTPUT_DIR = Path(
    os.environ.get("QKD_TRACKER_OUTPUT_DIR", ROOT_DIR / "resultados" / "debug")
)
SIGNAL_TIMEOUT_S = 0.45

# Limite de seguranca da velocidade enviada ao mount, em graus por segundo.
VEL_MAX_TESTE = min(MAX_TRACKING_RATE_DEG_S, VEL_MAX_LIMITE)
SAFE_TEST_MAX_RATE_DEG_S = 0.003
SAFE_TEST_MAX_OFFSET_DEG = 0.010
SAFE_TEST_MAX_SECONDS = 60.0

# Usa a matriz fina perto do alvo e a grossa quando o erro e maior.
FINE_MATRIX_ENTER_RADIUS_PX = 8.0
FINE_MATRIX_EXIT_RADIUS_PX = 14.0

# Suavizacao das medicoes e envio dos comandos.
MEASUREMENT_ALPHA = 0.65
CMD_ACCEL_LIMIT = 2.00
CMD_KEEPALIVE_S = 0.15
MIN_CMD_DELTA_TO_SEND = 2e-4
CMD_ZERO_SNAP = 0.35 * VEL_MIN_LIMITE

# Ganhos da malha rapida PD.
KP_AZ = 1.5000
KP_ALT = 1.4400
KD_AZ = 0.1800
KD_ALT = 0.1800
DERIVATIVE_ALPHA = 0.70

# "Trim" lento para viés persistente perto do centro. Ele substitui o Ki classico:
# so entra quando o erro permanece com o mesmo sinal por algum tempo e a malha ja
# esta em regime fino, evitando contaminar a resposta rapida.
TRIM_GAIN_AZ = 1.2
TRIM_GAIN_ALT = 1.2
TRIM_LIMIT = 0.020
TRIM_LEAK = 0.985
TRIM_ERROR_MAX_DEG = 0.0006
TRIM_DERIVATIVE_MAX_DEG_S = 0.006
TRIM_SAME_SIGN_S = 0.80
TRIM_SIGN_EPS_DEG = 0.00010
TRIM_SIGN_FLIP_DAMP = 0.35
TRIM_ENTER_RADIUS_PX = 1.3
TRIM_EXIT_RADIUS_PX = 2.2

# Freio simples se o erro cresce em varios frames seguidos.
ENABLE_RUNAWAY_BRAKE = True
RUNAWAY_MARGIN_PX = 1.0
RUNAWAY_FRAMES = 4
RUNAWAY_HOLD_S = 0.40
RUNAWAY_LOG_COOLDOWN_S = 2.0
ENABLE_MANUAL_JUMP_BRAKE = True
MANUAL_JUMP_PX = 18.0
MANUAL_JUMP_HOLD_S = 0.25


# =============================================================================
# BLOCO 2 - CAMERA, ALVO E ROI
# =============================================================================
# Este bloco esconde as diferencas entre IDS e ASI/ASCOM. Ele abre a camera,
# localiza o alvo salvo e recorta uma ROI de WINDOW_SIZE ao redor desse alvo.

def call(method: str, command: str, timeout: float = 5.0, **extra_args):
    params = {
        "ClientID": CLIENT_ID,
        "ClientTransactionID": next(_transaction_ids),
    }
    params.update(extra_args.pop("params", {}))
    resp = session.request(
        method,
        f"{BASE_URL}/{command}",
        params=params,
        timeout=timeout,
        **extra_args,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("ErrorNumber", 0):
        raise RuntimeError(f"{command}: {payload.get('ErrorMessage')}")
    return payload.get("Value")


def _ids_camera():
    from controle.camera_ids_peak import camera

    return camera


def get_camera_size() -> tuple[int, int]:
    if backend_name() == "ids":
        return _ids_camera().get_sensor_size()
    max_x = int(call("GET", "cameraxsize"))
    max_y = int(call("GET", "cameraysize"))
    return max_x, max_y


def _roi_params_for_target(
    sensor_w: int,
    sensor_h: int,
    roi_w: int,
    roi_h: int,
    target_x: float,
    target_y: float,
    mode: str,
) -> tuple[int, int, float, float]:
    def roi_for(raw_x: float, raw_y: float) -> tuple[int, int, float, float]:
        start_x, start_y, local_x, local_y = roi_incluindo_alvo(
            sensor_w, sensor_h, roi_w, roi_h, raw_x, raw_y
        )
        if backend_name() == "ids":
            # Incrementos de Offset da U3-3680XCP-NIR.
            start_x = int(np.clip(round(start_x / 8) * 8, 0, sensor_w - roi_w))
            start_y = int(np.clip(round(start_y / 2) * 2, 0, sensor_h - roi_h))
            local_x = float(raw_x - start_x)
            local_y = float(raw_y - start_y)
        return start_x, start_y, local_x, local_y

    if mode == "rot180_ascom_axes":
        raw_target_x = (sensor_w - 1) - target_y
        raw_target_y = (sensor_h - 1) - target_x
        start_x, start_y, raw_local_x, raw_local_y = roi_for(raw_target_x, raw_target_y)
        return (
            start_x,
            start_y,
            float((roi_h - 1) - raw_local_y),
            float((roi_w - 1) - raw_local_x),
        )

    if mode == "rot180":
        target_x = float(np.clip(target_x, 0, sensor_w - 1))
        target_y = float(np.clip(target_y, 0, sensor_h - 1))
        raw_target_x = (sensor_w - 1) - target_x
        raw_target_y = (sensor_h - 1) - target_y
        start_x, start_y, local_x, local_y = roi_for(raw_target_x, raw_target_y)
        return start_x, start_y, float((roi_w - 1) - local_x), float((roi_h - 1) - local_y)

    target_x = float(np.clip(target_x, 0, sensor_w - 1))
    target_y = float(np.clip(target_y, 0, sensor_h - 1))
    start_x, start_y, local_x, local_y = roi_for(target_x, target_y)
    return start_x, start_y, local_x, local_y


def _target_local_in_actual_roi(
    sensor_w: int,
    sensor_h: int,
    target_x: float,
    target_y: float,
    actual_roi: tuple[int, int, int, int],
    mode: str,
) -> tuple[float, float]:
    """Recalcula o alvo quando o hardware arredonda tamanho/offset da ROI."""
    actual_w, actual_h, actual_x, actual_y = actual_roi
    if mode == "rot180_ascom_axes":
        raw_x = (sensor_w - 1) - target_y
        raw_y = (sensor_h - 1) - target_x
        return (
            float((actual_h - 1) - (raw_y - actual_y)),
            float((actual_w - 1) - (raw_x - actual_x)),
        )
    if mode == "rot180":
        raw_x = (sensor_w - 1) - target_x
        raw_y = (sensor_h - 1) - target_y
        return (
            float((actual_w - 1) - (raw_x - actual_x)),
            float((actual_h - 1) - (raw_y - actual_y)),
        )
    return float(target_x - actual_x), float(target_y - actual_y)


def _apply_camera_roi(
    w: int,
    h: int,
    start_x: int,
    start_y: int,
) -> tuple[int, int, int, int]:
    if backend_name() == "ids":
        actual = _ids_camera().set_roi(w, h, start_x, start_y)
        expected = (w, h, start_x, start_y)
        if actual != expected:
            print(
                f"Aviso: a IDS alinhou a ROI de {expected} para {actual}; "
                "o alvo local sera recalculado automaticamente."
            )
        return actual
    call("PUT", "numx", data={"NumX": w})
    call("PUT", "numy", data={"NumY": h})
    call("PUT", "startx", data={"StartX": start_x})
    call("PUT", "starty", data={"StartY": start_y})
    return w, h, start_x, start_y


def set_camera_roi(w: int, h: int, target_x: float | None = None, target_y: float | None = None) -> tuple[int, int, float, float]:
    try:
        max_x, max_y = get_camera_size()
        if target_x is None:
            target_x = (max_x - 1) / 2
        if target_y is None:
            target_y = (max_y - 1) / 2

        target_x = float(np.clip(target_x, 0, max_x - 1))
        target_y = float(np.clip(target_y, 0, max_y - 1))
        start_x, start_y, target_x_local, target_y_local = _roi_params_for_target(
            max_x,
            max_y,
            w,
            h,
            target_x,
            target_y,
            mode="rot180" if ROTATE_IMAGE_180 else "direct",
        )
        print(
            f"Cortando o sensor na fonte: ROI {w}x{h} px em "
            f"Start=({start_x}, {start_y}); alvo local=({target_x_local:.1f}, {target_y_local:.1f})"
        )
        actual_roi = _apply_camera_roi(w, h, start_x, start_y)
        target_x_local, target_y_local = _target_local_in_actual_roi(
            max_x,
            max_y,
            target_x,
            target_y,
            actual_roi,
            mode="rot180" if ROTATE_IMAGE_180 else "direct",
        )
        return actual_roi[2], actual_roi[3], target_x_local, target_y_local
    except Exception as exc:
        print(f"Erro ao setar ROI via hardware: {exc}")
        return 0, 0, w / 2, h / 2


def set_camera_roi_validated(
    w: int,
    h: int,
    target_x: float,
    target_y: float,
    focus_mode: str,
    focus_signature: dict | None = None,
) -> tuple[int, int, float, float]:
    max_x, max_y = get_camera_size()
    if backend_name() == "ids":
        display_w = max_x
        display_h = max_y
    else:
        display_w = max_y
        display_h = max_x
    target_x = float(np.clip(target_x, 0, display_w - 1))
    target_y = float(np.clip(target_y, 0, display_h - 1))

    if backend_name() == "ids":
        candidates = (
            [("rot180", "IDS rotacionada 180 graus")]
            if ROTATE_IMAGE_180
            else [("direct", "IDS sem rotacao")]
        )
    else:
        candidates = [
            ("rot180_ascom_axes", "frame rotacionado 180 com eixos ASCOM"),
            ("rot180", "coordenada corrigida pela rotacao 180 antiga"),
            ("direct", "coordenada direta do sensor antiga"),
        ]
    best = None
    print(
        f"Sensor {backend_name()}: {max_x}x{max_y}; frame esperado apos captura: "
        f"{display_w}x{display_h}; alvo global=({target_x:.1f}, {target_y:.1f})"
    )

    for mode, description in candidates:
        start_x, start_y, target_x_local, target_y_local = _roi_params_for_target(
            max_x,
            max_y,
            w,
            h,
            target_x,
            target_y,
            mode=mode,
        )
        print(
            f"Testando ROI ({description}): Start=({start_x}, {start_y}), "
            f"alvo local=({target_x_local:.1f}, {target_y_local:.1f})"
        )
        actual_roi = _apply_camera_roi(w, h, start_x, start_y)
        actual_w, actual_h, start_x, start_y = actual_roi
        target_x_local, target_y_local = _target_local_in_actual_roi(
            max_x,
            max_y,
            target_x,
            target_y,
            actual_roi,
            mode,
        )
        if not (0 <= target_x_local < actual_w and 0 <= target_y_local < actual_h):
            print("  -> o ajuste do hardware deixou o alvo fora dessa ROI.")
            continue
        if _normalize_focus_mode(focus_mode) == "dual":
            foco_temp.initialize_focus_lock(
                focus_signature,
                target_x_local,
                target_y_local,
                max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
            )
        frame_test = capture_frame(EXPOSURE_SECONDS)
        cm = medir_laser(frame_test, focus_mode)
        if cm is None:
            print("  -> sem sinal nessa ROI.")
            continue

        x_cm, y_cm = cm
        dist = float(np.hypot(x_cm - target_x_local, y_cm - target_y_local))
        print(f"  -> sinal encontrado em ({x_cm:.1f}, {y_cm:.1f}), distancia ao alvo={dist:.1f}px")
        if best is None or dist < best[0]:
            best = (dist, start_x, start_y, target_x_local, target_y_local, mode, frame_test)

    if best is None:
        print("Aviso: nenhuma ROI de teste encontrou o laser; usando a ROI calculada para o alvo.")
        fallback = set_camera_roi(w, h, target_x, target_y)
        if _normalize_focus_mode(focus_mode) == "dual":
            foco_temp.initialize_focus_lock(
                focus_signature,
                fallback[2],
                fallback[3],
                max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
            )
        return fallback

    _, start_x, start_y, target_x_local, target_y_local, mode, frame_test = best
    actual_roi = _apply_camera_roi(w, h, start_x, start_y)
    actual_w, actual_h, start_x, start_y = actual_roi
    target_x_local, target_y_local = _target_local_in_actual_roi(
        max_x,
        max_y,
        target_x,
        target_y,
        actual_roi,
        mode,
    )
    if _normalize_focus_mode(focus_mode) == "dual":
        foco_temp.initialize_focus_lock(
            focus_signature,
            target_x_local,
            target_y_local,
            max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
        )
    debug_path = TRACKER_OUTPUT_DIR / "tracker_roi_teste.png"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame_test)
    if ok:
        debug_path.write_bytes(encoded.tobytes())
    print(
        f"ROI escolhida: {mode} | Start=({start_x}, {start_y}) | "
        f"alvo local=({target_x_local:.1f}, {target_y_local:.1f})"
    )
    print(f"Frame de teste da ROI salvo em: {display_path(debug_path)}")
    return start_x, start_y, target_x_local, target_y_local


def reset_camera_roi() -> None:
    try:
        if backend_name() == "ids":
            _ids_camera().reset_roi()
            return
        max_x = call("GET", "cameraxsize")
        max_y = call("GET", "cameraysize")
        call("PUT", "startx", data={"StartX": 0})
        call("PUT", "starty", data={"StartY": 0})
        call("PUT", "numx", data={"NumX": max_x})
        call("PUT", "numy", data={"NumY": max_y})
    except Exception:
        pass


def escolher_referencia_tracker(sensor_w: int, sensor_h: int, focus_mode: str) -> AlvoAlinhamento:
    reset_camera_roi()
    saved_target = carregar_alvo_salvo()

    if _normalize_focus_mode(focus_mode) == "dual":
        target_choice = (
            input(
                "Alvo desta sessao (1=selecionar a luz agora, "
                "2=usar alvo salvo pela calibracao) [1]: "
            ).strip()
            or "1"
        )
        if target_choice not in {"1", "2"}:
            raise ValueError("Escolha 1 para selecionar agora ou 2 para usar o alvo salvo.")
        if target_choice == "1":
            foco_temp.reset_focus_lock()
            frame = capture_frame(EXPOSURE_SECONDS)
            selection = foco_temp.escolher_ilha_manualmente(
                frame,
                max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
            )
            print(
                "Alvo temporario desta sessao: "
                f"({selection['x_px']:.2f}, {selection['y_px']:.2f}) px. "
                "As matrizes e o alvo salvo da calibracao nao foram alterados."
            )
            return AlvoAlinhamento(
                x_px=float(selection["x_px"]),
                y_px=float(selection["y_px"]),
                source="manual_tracker_session",
                path=None,
                focus_mode="dual",
                focus_signature=selection["signature"],
            )

    saved_signature = None
    if (
        _normalize_focus_mode(focus_mode) == "dual"
        and saved_target is not None
        and saved_target.focus_signature is not None
        and saved_target.focus_mode in {None, "dual"}
    ):
        saved_signature = saved_target.focus_signature
        if foco_temp.initialize_focus_lock(
            saved_signature,
            saved_target.x_px,
            saved_target.y_px,
            max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
        ):
            print("Assinatura do foco salva carregada antes da busca no frame completo.")
    elif _normalize_focus_mode(focus_mode) == "dual":
        foco_temp.reset_focus_lock()

    frame = capture_frame(EXPOSURE_SECONDS)
    cm = medir_laser(frame, focus_mode)
    if cm is None:
        if saved_target is not None:
            print("Nao identifiquei o foco no frame completo; mantendo o alvo salvo para testar a ROI.")
            return saved_target
        cx = (sensor_w - 1) / 2
        cy = (sensor_h - 1) / 2
        print("Nao encontrei o laser no frame inicial; usando centro da camera.")
        return AlvoAlinhamento(x_px=float(cx), y_px=float(cy), source="camera_center")

    x_cm, y_cm = cm
    current_signature = (
        foco_temp.get_focus_signature()
        if _normalize_focus_mode(focus_mode) == "dual"
        else None
    )
    return escolher_posicao_inicial_ou_centro(
        frame,
        float(x_cm),
        float(y_cm),
        prompt="Referencia do tracker",
        focus_mode=focus_mode,
        focus_signature=current_signature,
    )


def connect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().connect()
        return
    print("Conectando à câmera...")
    call("PUT", "connected", data={"Connected": True})
    call("PUT", "gain", data={"Gain": int(ASI_GAIN)})
    print(f"Camera ASI configurada: ganho={ASI_GAIN}, exposicao={EXPOSURE_SECONDS * 1e6:.1f} us")


def disconnect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().disconnect()
        return
    print("Desconectando da câmera...")
    call("PUT", "connected", data={"Connected": False})


def start_exposure(duration_seconds: float, light: bool = True) -> None:
    call("PUT", "startexposure", data={"Duration": duration_seconds, "Light": light})


def wait_until_image_ready(
    poll_interval: float = IMAGE_READY_POLL_S,
    timeout: float = 5.0,
) -> None:
    deadline = time.time() + timeout
    spin_polls = IMAGE_READY_SPIN_POLLS
    while time.time() < deadline:
        ready = bool(call("GET", "imageready"))
        if ready:
            return
        if spin_polls > 0:
            spin_polls -= 1
            continue
        time.sleep(poll_interval)
    raise TimeoutError("Tempo limite esperando ImageReady = True")


def fetch_image_array() -> np.ndarray:
    from controle.camera_asi_fast import fetch_image_array as fetch_image_array_fast

    return fetch_image_array_fast()


def capture_frame(exposure_seconds: float) -> np.ndarray:
    if backend_name() == "ids":
        frame = _ids_camera().capture(exposure_seconds).astype(np.float32)
    else:
        from controle.camera_asi_fast import record_capture_time

        capture_started = time.perf_counter()
        start_exposure(exposure_seconds, light=True)
        wait_until_image_ready()
        frame = fetch_image_array().astype(np.float32)
        record_capture_time(time.perf_counter() - capture_started)

    pedestal = np.median(frame) + (0.5 * np.std(frame))
    max_val = frame.max()
    if max_val <= pedestal:
        return np.zeros_like(frame, dtype=np.uint8)

    norm = np.clip((frame - pedestal) / (max_val - pedestal + 1e-6), 0, 1)
    norm = (norm * 255).astype(np.uint8)
    return np.rot90(norm, 2) if ROTATE_IMAGE_180 else norm


# =============================================================================
# BLOCO 3 - MEDICAO DO FOCO
# =============================================================================
# Retorna o centro do foco em pixels. No modo dual, reutiliza a assinatura
# salva pelo centro de massa para evitar trocar para uma luz concorrente.

def calcular_cm_corrigido(frame_window: np.ndarray, threshold_percent: float = 0.5):
    if frame_window.ndim == 3:
        frame_gray = frame_window.mean(axis=2)
    else:
        frame_gray = frame_window

    max_val = float(frame_gray.max())
    if max_val < 100:
        return None

    dynamic_threshold = max_val * threshold_percent
    _, weights = cv2.threshold(
        frame_gray.astype(np.float32, copy=False),
        dynamic_threshold,
        0,
        cv2.THRESH_TOZERO,
    )
    moments = cv2.moments(weights, binaryImage=False)
    total_intensidade = moments["m00"]
    if total_intensidade <= 0:
        return None

    x_cm = moments["m10"] / total_intensidade
    y_cm = moments["m01"] / total_intensidade
    return x_cm, y_cm


def medir_laser(frame_window: np.ndarray, focus_mode: str):
    if _normalize_focus_mode(focus_mode) == "dual":
        cm = foco_temp.centro_massa(frame_window)
        if cm is None:
            return None
        return float(cm[0]), float(cm[1])

    return calcular_cm_corrigido(frame_window)


# =============================================================================
# BLOCO 4 - CONTROLADOR E ESTADO COMPARTILHADO
# =============================================================================
# O PD reage rapidamente ao erro. O trim corrige apenas um vies pequeno e
# persistente perto do centro. SharedState liga a captura ao controle do mount.

class MeasurementPDTrim:
    """PD rapido com trim lento para erro persistente perto do centro."""

    def __init__(
        self,
        kp,
        kd,
        trim_gain,
        output_limits,
        derivative_alpha=0.70,
        trim_limit=0.18,
        trim_leak=0.995,
        trim_error_max=0.0025,
        trim_derivative_max=0.03,
        trim_same_sign_s=0.25,
        trim_sign_eps=0.00015,
        trim_sign_flip_damp=0.35,
    ):
        self.kp = kp
        self.kd = kd
        self.trim_gain = trim_gain
        self.min_output, self.max_output = output_limits
        self.derivative_alpha = derivative_alpha
        self.trim_limit = abs(float(trim_limit))
        self.trim_leak = float(trim_leak)
        self.trim_error_max = abs(float(trim_error_max))
        self.trim_derivative_max = abs(float(trim_derivative_max))
        self.trim_same_sign_s = float(trim_same_sign_s)
        self.trim_sign_eps = abs(float(trim_sign_eps))
        self.trim_sign_flip_damp = float(trim_sign_flip_damp)
        self.reset()

    def reset(self):
        self._last_error = None
        self._last_t = None
        self._d_filt = 0.0
        self.clear_trim()

    def clear_trim(self):
        self._trim_bias = 0.0
        self._held_sign = 0
        self._same_sign_elapsed = 0.0

    def _clip(self, value):
        if self.min_output is not None and value < self.min_output:
            return self.min_output
        if self.max_output is not None and value > self.max_output:
            return self.max_output
        return value

    def update(self, error, timestamp, trim_allowed):
        if self._last_t is None or timestamp <= self._last_t:
            self._last_error = error
            self._last_t = timestamp
            self._d_filt = 0.0
            self.clear_trim()
            return self._clip(self.kp * error), 0.0

        dt = max(timestamp - self._last_t, 1e-3)
        deriv = (error - self._last_error) / dt
        self._d_filt = (self.derivative_alpha * self._d_filt) + ((1.0 - self.derivative_alpha) * deriv)

        if abs(error) < self.trim_sign_eps:
            sign = 0
        else:
            sign = 1 if error > 0.0 else -1

        if sign == 0:
            self._held_sign = 0
            self._same_sign_elapsed = 0.0
            self._trim_bias *= self.trim_leak
        else:
            if sign == self._held_sign:
                self._same_sign_elapsed += dt
            else:
                if self._held_sign != 0:
                    self._trim_bias *= self.trim_sign_flip_damp
                self._held_sign = sign
                self._same_sign_elapsed = 0.0

            trim_ready = (
                trim_allowed
                and self._same_sign_elapsed >= self.trim_same_sign_s
                and abs(error) <= self.trim_error_max
                and abs(self._d_filt) <= self.trim_derivative_max
            )
            if trim_ready:
                self._trim_bias += self.trim_gain * error * dt
                self._trim_bias = float(
                    np.clip(
                        self._trim_bias,
                        -self.trim_limit,
                        self.trim_limit,
                    )
                )
            else:
                self._trim_bias *= self.trim_leak

        output_raw = (self.kp * error) + (self.kd * self._d_filt) + self._trim_bias
        output = self._clip(output_raw)
        self._last_error = error
        self._last_t = timestamp
        return output, float(self._trim_bias)


@dataclass
class SharedState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: bool = False
    has_signal: bool = False
    measurement_seq: int = 0
    measurement_ts: float = 0.0
    dx_filt_px: float = 0.0
    dy_filt_px: float = 0.0
    err_az_deg: float = 0.0
    err_alt_deg: float = 0.0
    cmd_az_deg_s: float = 0.0
    cmd_alt_deg_s: float = 0.0
    brake_active: bool = False
    active_matrix_name: str = "coarse"
    trim_mode_active: bool = False
    hold_active: bool = False
    measurement_hz: float = 0.0
    control_loop_hz: float = 0.0
    spot_touches_border: bool = False
    mount_az_deg: float | None = None
    mount_alt_deg: float | None = None
    offset_az_deg: float = 0.0
    offset_alt_deg: float = 0.0
    safety_stop_reason: str | None = None
    watchdog_error: str | None = None


class TrackerCsvLogger:
    """Grava telemetria compacta e calcula variancia numa janela movel."""

    FIELDNAMES = [
        "data_hora",
        "tempo_decorrido_s",
        "estado",
        "sinal_encontrado",
        "x_cm_px",
        "y_cm_px",
        "alvo_x_px",
        "alvo_y_px",
        "erro_x_px",
        "erro_y_px",
        "distancia_px",
        "erro_x_filtrado_px",
        "erro_y_filtrado_px",
        "variancia_x_px2",
        "variancia_y_px2",
        "desvio_padrao_2d_px",
        "erro_az_deg",
        "erro_alt_deg",
        "velocidade_az_deg_s",
        "velocidade_alt_deg_s",
        "azimute_absoluto_deg",
        "altitude_absoluta_deg",
        "deslocamento_az_desde_inicio_deg",
        "deslocamento_alt_desde_inicio_deg",
        "loop_medicao_hz",
        "loop_controle_hz",
        "matriz_ativa",
        "zona_parada_ativa",
        "correcao_lenta_ativa",
        "freio_ativo",
        "ilha_tocando_borda",
        "evento_seguranca",
    ]

    def __init__(
        self,
        output_dir: Path,
        session_started_monotonic: float,
        initial_az_deg: float,
        initial_alt_deg: float,
        max_session_hours: float,
    ):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir = Path(output_dir) / "sessoes" / f"tracker_{timestamp}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.csv_path = self.session_dir / "telemetria.csv"
        self.summary_path = self.session_dir / "resumo.json"
        self._fp = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fp, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self._session_started = session_started_monotonic
        self._last_write_t = 0.0
        self._last_flush_t = session_started_monotonic
        self._samples = deque()
        self._summary = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "initial_azimuth_deg": initial_az_deg,
            "initial_altitude_deg": initial_alt_deg,
            "max_session_hours": max_session_hours,
            "max_offset_az_deg": MAX_OFFSET_AZ_DEG,
            "max_offset_alt_deg": MAX_OFFSET_ALT_DEG,
            "roi_size_px": WINDOW_SIZE,
            "hold_enter_radius_px": HOLD_ENTER_RADIUS_PX,
            "hold_exit_radius_px": HOLD_EXIT_RADIUS_PX,
            "csv_path": display_path(self.csv_path),
        }

    def write(
        self,
        now: float,
        *,
        state_values: dict,
        status: str,
        x_cm: float | None,
        y_cm: float | None,
        target_x: float,
        target_y: float,
        dx: float,
        dy: float,
        event: str = "",
    ) -> None:
        has_signal = bool(state_values["has_signal"])
        if has_signal:
            self._samples.append((now, float(dx), float(dy)))
        cutoff = now - VARIANCE_WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

        force = bool(event)
        if not force and self._last_write_t > 0.0:
            if (now - self._last_write_t) < (1.0 / CSV_LOG_HZ):
                return

        if self._samples:
            sample_array = np.asarray([(v[1], v[2]) for v in self._samples], dtype=float)
            variance_x = float(np.var(sample_array[:, 0]))
            variance_y = float(np.var(sample_array[:, 1]))
            std_2d = float(np.sqrt(variance_x + variance_y))
        else:
            variance_x = variance_y = std_2d = 0.0

        def number(value, digits=6):
            return "" if value is None else round(float(value), digits)

        row = {
            "data_hora": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "tempo_decorrido_s": round(now - self._session_started, 3),
            "estado": status,
            "sinal_encontrado": int(has_signal),
            "x_cm_px": number(x_cm, 3),
            "y_cm_px": number(y_cm, 3),
            "alvo_x_px": number(target_x, 3),
            "alvo_y_px": number(target_y, 3),
            "erro_x_px": number(dx, 3),
            "erro_y_px": number(dy, 3),
            "distancia_px": number(np.hypot(dx, dy), 3),
            "erro_x_filtrado_px": number(state_values["dx_filt_px"], 3),
            "erro_y_filtrado_px": number(state_values["dy_filt_px"], 3),
            "variancia_x_px2": round(variance_x, 4),
            "variancia_y_px2": round(variance_y, 4),
            "desvio_padrao_2d_px": round(std_2d, 4),
            "erro_az_deg": number(state_values["err_az_deg"]),
            "erro_alt_deg": number(state_values["err_alt_deg"]),
            "velocidade_az_deg_s": number(state_values["cmd_az_deg_s"]),
            "velocidade_alt_deg_s": number(state_values["cmd_alt_deg_s"]),
            "azimute_absoluto_deg": number(state_values["mount_az_deg"]),
            "altitude_absoluta_deg": number(state_values["mount_alt_deg"]),
            "deslocamento_az_desde_inicio_deg": number(state_values["offset_az_deg"]),
            "deslocamento_alt_desde_inicio_deg": number(state_values["offset_alt_deg"]),
            "loop_medicao_hz": number(state_values["measurement_hz"], 2),
            "loop_controle_hz": number(state_values["control_loop_hz"], 2),
            "matriz_ativa": state_values["active_matrix_name"],
            "zona_parada_ativa": int(bool(state_values["hold_active"])),
            "correcao_lenta_ativa": int(bool(state_values["trim_mode_active"])),
            "freio_ativo": int(bool(state_values["brake_active"])),
            "ilha_tocando_borda": int(bool(state_values["spot_touches_border"])),
            "evento_seguranca": event,
        }
        self._writer.writerow(row)
        self._last_write_t = now
        if force or (now - self._last_flush_t) >= CSV_FLUSH_SECONDS:
            self._fp.flush()
            self._last_flush_t = now

    def save_event_frame(self, frame: np.ndarray | None, event: str) -> Path | None:
        if frame is None:
            return None
        safe_event = "".join(c if c.isalnum() else "_" for c in event).strip("_")
        path = self.session_dir / f"evento_{safe_event or 'seguranca'}.png"
        cv2.imwrite(str(path), frame)
        return path

    def close(self, *, reason: str, return_result: dict | None = None) -> None:
        self._summary.update(
            {
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "finish_reason": reason,
                "return_to_start": return_result,
            }
        )
        self.summary_path.write_text(
            json.dumps(self._summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if not self._fp.closed:
            self._fp.flush()
            self._fp.close()


def _state_snapshot(state: SharedState) -> dict:
    with state.lock:
        return {
            "stop": state.stop,
            "has_signal": state.has_signal,
            "dx_filt_px": state.dx_filt_px,
            "dy_filt_px": state.dy_filt_px,
            "err_az_deg": state.err_az_deg,
            "err_alt_deg": state.err_alt_deg,
            "cmd_az_deg_s": state.cmd_az_deg_s,
            "cmd_alt_deg_s": state.cmd_alt_deg_s,
            "brake_active": state.brake_active,
            "active_matrix_name": state.active_matrix_name,
            "trim_mode_active": state.trim_mode_active,
            "hold_active": state.hold_active,
            "measurement_hz": state.measurement_hz,
            "control_loop_hz": state.control_loop_hz,
            "spot_touches_border": state.spot_touches_border,
            "mount_az_deg": state.mount_az_deg,
            "mount_alt_deg": state.mount_alt_deg,
            "offset_az_deg": state.offset_az_deg,
            "offset_alt_deg": state.offset_alt_deg,
            "safety_stop_reason": state.safety_stop_reason,
            "watchdog_error": state.watchdog_error,
        }


def _request_safety_stop(state: SharedState, reason: str) -> bool:
    with state.lock:
        if state.safety_stop_reason is not None:
            return False
        state.safety_stop_reason = str(reason)
        state.stop = True
    stop_axes_safely()
    return True


def _mount_offsets_from_start(
    initial_az_deg: float,
    initial_alt_deg: float,
    current_az_deg: float,
    current_alt_deg: float,
) -> tuple[float, float]:
    """Retorna deslocamentos assinados, incluindo a passagem Az 359/0 graus."""
    offset_az = -float(calc_error(0, initial_az_deg, current_az_deg))
    offset_alt = float(current_alt_deg - initial_alt_deg)
    return offset_az, offset_alt


def _position_watchdog(
    state: SharedState,
    initial_az_deg: float,
    initial_alt_deg: float,
    session_started: float,
    max_session_seconds: float,
) -> None:
    interval = 1.0 / POSITION_WATCHDOG_HZ
    consecutive_failures = 0

    while True:
        loop_started = time.perf_counter()
        with state.lock:
            if state.stop:
                return

        if (loop_started - session_started) >= max_session_seconds:
            _request_safety_stop(state, "tempo_maximo_da_sessao")
            return

        try:
            az_deg, alt_deg = read_altaz()
            offset_az, offset_alt = _mount_offsets_from_start(
                initial_az_deg,
                initial_alt_deg,
                az_deg,
                alt_deg,
            )
            consecutive_failures = 0
            with state.lock:
                state.mount_az_deg = az_deg
                state.mount_alt_deg = alt_deg
                state.offset_az_deg = offset_az
                state.offset_alt_deg = offset_alt
                state.watchdog_error = None

            if abs(offset_az) >= MAX_OFFSET_AZ_DEG:
                _request_safety_stop(
                    state,
                    f"limite_absoluto_az_{offset_az:+.4f}_deg",
                )
                return
            if abs(offset_alt) >= MAX_OFFSET_ALT_DEG:
                _request_safety_stop(
                    state,
                    f"limite_absoluto_alt_{offset_alt:+.4f}_deg",
                )
                return
        except Exception as exc:
            consecutive_failures += 1
            with state.lock:
                state.watchdog_error = str(exc)
            if consecutive_failures >= WATCHDOG_READ_FAILURES:
                _request_safety_stop(state, "watchdog_mount_sem_resposta")
                return

        elapsed = time.perf_counter() - loop_started
        if elapsed < interval:
            time.sleep(interval - elapsed)


def _return_to_initial_position(initial_az_deg: float, initial_alt_deg: float) -> dict:
    result = {
        "attempted": True,
        "success": False,
        "attempts": 0,
        "final_azimuth_deg": None,
        "final_altitude_deg": None,
        "error": None,
    }
    try:
        stop_axes_safely()
        for attempt in range(1, RETURN_ATTEMPTS + 1):
            current_az, current_alt = read_altaz()
            delta_az = float(calc_error(0, initial_az_deg, current_az))
            delta_alt = float(initial_alt_deg - current_alt)
            result["attempts"] = attempt
            if max(abs(delta_az), abs(delta_alt)) <= RETURN_TOLERANCE_DEG:
                result["success"] = True
                break

            # Uma leitura muito alem da trava configurada pode ser invalida.
            # Nesse caso e mais seguro nao iniciar um retorno longo automatico.
            if (
                abs(delta_az) > (MAX_OFFSET_AZ_DEG + 0.5)
                or abs(delta_alt) > (MAX_OFFSET_ALT_DEG + 0.5)
            ):
                raise RuntimeError(
                    "distancia de retorno excedeu o limite mais a margem de 0.5 deg"
                )

            print(
                f"Retorno seguro {attempt}/{RETURN_ATTEMPTS}: "
                f"dAz={delta_az:+.5f} deg, dAlt={delta_alt:+.5f} deg"
            )
            move_axes_pid_2d(
                True,
                delta_az,
                delta_alt,
                max_velocity_deg_s=RETURN_MAX_RATE_DEG_S,
            )

        final_az, final_alt = read_altaz()
        final_error_az = float(calc_error(0, initial_az_deg, final_az))
        final_error_alt = float(initial_alt_deg - final_alt)
        result.update(
            {
                "final_azimuth_deg": final_az,
                "final_altitude_deg": final_alt,
                "final_error_azimuth_deg": final_error_az,
                "final_error_altitude_deg": final_error_alt,
            }
        )
        result["success"] = (
            max(abs(final_error_az), abs(final_error_alt)) <= RETURN_TOLERANCE_DEG
        )
    except KeyboardInterrupt:
        result["error"] = "retorno interrompido pelo usuario"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        stop_axes_safely()
    return result


def _apply_min_velocity(cmd):
    if abs(cmd) < CMD_ZERO_SNAP:
        return 0.0
    if 0.0 < abs(cmd) < VEL_MIN_LIMITE:
        return float(VEL_MIN_LIMITE * np.sign(cmd))
    return float(cmd)


def _update_hold_state(
    hold_active: bool,
    exit_count: int,
    radius_px: float,
) -> tuple[bool, int]:
    """Aplica a histerese 4/6 px sem oscilar entre parar e corrigir."""
    if hold_active:
        if radius_px >= HOLD_EXIT_RADIUS_PX:
            exit_count += 1
            if exit_count >= HOLD_EXIT_CONFIRM_FRAMES:
                return False, 0
        else:
            exit_count = 0
        return True, exit_count
    if radius_px <= HOLD_ENTER_RADIUS_PX:
        return True, 0
    return False, 0


def _slew_limit(current, target, max_delta):
    delta = target - current
    if abs(delta) <= max_delta:
        return float(target)
    return float(current + (np.sign(delta) * max_delta))


def _pixel_error_to_mount_error(dx_px, dy_px, A_inv):
    """Converte o deslocamento da imagem no erro angular que deve ser corrigido."""

    # O sinal negativo pede ao mount um movimento que anule o erro observado.
    vec_px = np.array([-dx_px, -dy_px], dtype=float)
    err_vec = A_inv @ vec_px
    return float(err_vec[0]), float(err_vec[1])


# =============================================================================
# BLOCO 5 - MATRIZES DA CALIBRACAO
# =============================================================================
# Carrega somente matrizes compatveis com a camera/modo atual. Para a IDS,
# recusa matrizes antigas da ASI para evitar uma correcao com eixos errados.

def _normalize_focus_mode(mode: str) -> str:
    normalized = str(mode).strip().lower()
    if normalized in {"2", "dual", "duplo", "dois", "two"}:
        return "dual"
    return "single"


def _load_tracking_calibration_matrices(focus_mode: str):
    focus_mode = _normalize_focus_mode(focus_mode)
    if backend_name() == "ids":
        fine_candidates = matrix_candidates(f"{IDS_MATRIX_PREFIX}_A_inv_fine.npy")
        coarse_candidates = matrix_candidates(f"{IDS_MATRIX_PREFIX}_A_inv_coarse.npy")
    elif focus_mode == "dual":
        fine_candidates = matrix_candidates("foco_temp_A_inv_fine.npy")
        coarse_candidates = matrix_candidates("foco_temp_A_inv_coarse.npy")
    else:
        fine_candidates = matrix_candidates(
            "A_inv_fine.npy",
            "calibracao_dual_v3_fine_A_inv.npy",
            "calibracao_A_inv.npy",
        )
        coarse_candidates = matrix_candidates(
            "A_inv_coarse.npy",
            "calibracao_dual_v3_coarse_A_inv.npy",
            "calibracao_A_inv.npy",
        )

    def _load_first_existing(candidates, label):
        for path in candidates:
            try:
                matrix = np.load(path)
            except FileNotFoundError:
                continue
            if matrix.shape != (2, 2):
                raise ValueError(f"Matriz {path} para {label} precisa ser 2x2")
            return matrix, path
        raise FileNotFoundError(
            f"Nao encontrei matriz {label}. Testei: {', '.join(str(path) for path in candidates)}"
        )

    fine_matrix, fine_path = _load_first_existing(fine_candidates, "fine")
    coarse_matrix, coarse_path = _load_first_existing(coarse_candidates, "coarse")
    return {
        "fine": fine_matrix,
        "coarse": coarse_matrix,
        "fine_path": display_path(fine_path),
        "coarse_path": display_path(coarse_path),
    }


# =============================================================================
# BLOCO 6 - LOOP DE CONTROLE DO MOUNT
# =============================================================================
# Roda em uma thread separada. A cada medida: escolhe a matriz, converte o erro,
# calcula a velocidade, limita aceleracao e envia os dois eixos. Se o sinal
# sumir ou o erro crescer repetidamente, comanda velocidade zero.

def control_loop_continuo(
    state: SharedState,
    A_inv_fine: np.ndarray,
    A_inv_coarse: np.ndarray,
    usar_mount: bool,
):
    ctrl_az = MeasurementPDTrim(
        kp=KP_AZ,
        kd=KD_AZ,
        trim_gain=TRIM_GAIN_AZ,
        output_limits=(-VEL_MAX_TESTE, VEL_MAX_TESTE),
        derivative_alpha=DERIVATIVE_ALPHA,
        trim_limit=TRIM_LIMIT,
        trim_leak=TRIM_LEAK,
        trim_error_max=TRIM_ERROR_MAX_DEG,
        trim_derivative_max=TRIM_DERIVATIVE_MAX_DEG_S,
        trim_same_sign_s=TRIM_SAME_SIGN_S,
        trim_sign_eps=TRIM_SIGN_EPS_DEG,
        trim_sign_flip_damp=TRIM_SIGN_FLIP_DAMP,
    )
    ctrl_alt = MeasurementPDTrim(
        kp=KP_ALT,
        kd=KD_ALT,
        trim_gain=TRIM_GAIN_ALT,
        output_limits=(-VEL_MAX_TESTE, VEL_MAX_TESTE),
        derivative_alpha=DERIVATIVE_ALPHA,
        trim_limit=TRIM_LIMIT,
        trim_leak=TRIM_LEAK,
        trim_error_max=TRIM_ERROR_MAX_DEG,
        trim_derivative_max=TRIM_DERIVATIVE_MAX_DEG_S,
        trim_same_sign_s=TRIM_SAME_SIGN_S,
        trim_sign_eps=TRIM_SIGN_EPS_DEG,
        trim_sign_flip_damp=TRIM_SIGN_FLIP_DAMP,
    )

    dt_target = 1.0 / CONTROL_HZ
    last_loop_t = time.perf_counter()
    last_seq = -1

    target_cmd_az = 0.0
    target_cmd_alt = 0.0
    cmd_az = 0.0
    cmd_alt = 0.0
    last_sent_az = None
    last_sent_alt = None
    last_sent_az_t = 0.0
    last_sent_alt_t = 0.0

    err_az = 0.0
    err_alt = 0.0
    prev_radius_px = None
    prev_dx_filt_px = None
    prev_dy_filt_px = None
    runaway_count = 0
    brake_until = 0.0
    last_runaway_log_t = 0.0
    active_matrix_name = "coarse"
    active_matrix = A_inv_coarse
    trim_mode_active = False
    hold_active = False
    hold_exit_count = 0
    control_loop_hz = 0.0

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            while True:
                loop_t0 = time.perf_counter()
                dt_loop = max(loop_t0 - last_loop_t, 1e-4)
                last_loop_t = loop_t0
                if dt_loop >= 1e-3:
                    instant_control_hz = 1.0 / dt_loop
                    control_loop_hz = (
                        instant_control_hz
                        if control_loop_hz <= 0.0
                        else (0.15 * instant_control_hz) + (0.85 * control_loop_hz)
                    )

                with state.lock:
                    stop = state.stop
                    has_signal = state.has_signal
                    seq = state.measurement_seq
                    measurement_ts = state.measurement_ts
                    dx_filt = state.dx_filt_px
                    dy_filt = state.dy_filt_px

                if stop:
                    break

                measurement_age = (loop_t0 - measurement_ts) if measurement_ts else 1e9
                signal_ok = has_signal and (measurement_age <= SIGNAL_TIMEOUT_S)

                if not signal_ok:
                    err_az = 0.0
                    err_alt = 0.0
                    target_cmd_az = 0.0
                    target_cmd_alt = 0.0
                    ctrl_az.reset()
                    ctrl_alt.reset()
                    prev_radius_px = None
                    prev_dx_filt_px = None
                    prev_dy_filt_px = None
                    runaway_count = 0
                    trim_mode_active = False
                    hold_active = False
                    hold_exit_count = 0
                elif seq != last_seq:
                    last_seq = seq
                    radius_px = float(np.hypot(dx_filt, dy_filt))
                    previous_hold_active = hold_active
                    hold_active, hold_exit_count = _update_hold_state(
                        hold_active,
                        hold_exit_count,
                        radius_px,
                    )
                    if hold_active != previous_hold_active:
                        ctrl_az.reset()
                        ctrl_alt.reset()

                    manual_jump = False
                    if (
                        ENABLE_MANUAL_JUMP_BRAKE
                        and prev_dx_filt_px is not None
                        and prev_dy_filt_px is not None
                    ):
                        jump_px = float(
                            np.hypot(
                                dx_filt - prev_dx_filt_px,
                                dy_filt - prev_dy_filt_px,
                            )
                        )
                        manual_jump = (
                            jump_px >= MANUAL_JUMP_PX
                            and radius_px > (2.0 * TOLERANCIA_PX)
                        )

                    prev_dx_filt_px = dx_filt
                    prev_dy_filt_px = dy_filt

                    if manual_jump:
                        target_cmd_az = 0.0
                        target_cmd_alt = 0.0
                        cmd_az = 0.0
                        cmd_alt = 0.0
                        err_az = 0.0
                        err_alt = 0.0
                        ctrl_az.reset()
                        ctrl_alt.reset()
                        trim_mode_active = False
                        hold_active = False
                        hold_exit_count = 0
                        prev_radius_px = radius_px
                        runaway_count = 0
                        brake_until = loop_t0 + MANUAL_JUMP_HOLD_S
                        if (loop_t0 - last_runaway_log_t) >= RUNAWAY_LOG_COOLDOWN_S:
                            print(
                                "\nMovimento manual brusco detectado. "
                                "Zerando o controle por um instante antes de recentralizar."
                            )
                            last_runaway_log_t = loop_t0
                    else:
                        previous_matrix_name = active_matrix_name

                        if active_matrix_name == "coarse" and radius_px <= FINE_MATRIX_ENTER_RADIUS_PX:
                            active_matrix_name = "fine"
                            active_matrix = A_inv_fine
                        elif active_matrix_name == "fine" and radius_px >= FINE_MATRIX_EXIT_RADIUS_PX:
                            active_matrix_name = "coarse"
                            active_matrix = A_inv_coarse

                        if active_matrix_name != previous_matrix_name:
                            ctrl_az.reset()
                            ctrl_alt.reset()
                            trim_mode_active = False

                        if hold_active:
                            trim_mode_active = False
                            ctrl_az.clear_trim()
                            ctrl_alt.clear_trim()
                        elif active_matrix_name == "fine" and radius_px <= TRIM_ENTER_RADIUS_PX:
                            trim_mode_active = True
                        elif (
                            active_matrix_name != "fine"
                            or radius_px >= TRIM_EXIT_RADIUS_PX
                        ):
                            if trim_mode_active:
                                ctrl_az.clear_trim()
                                ctrl_alt.clear_trim()
                            trim_mode_active = False

                        if hold_active:
                            err_az = 0.0
                            err_alt = 0.0
                            target_cmd_az = 0.0
                            target_cmd_alt = 0.0
                        else:
                            err_az, err_alt = _pixel_error_to_mount_error(dx_filt, dy_filt, active_matrix)
                            trim_allowed = trim_mode_active and (loop_t0 >= brake_until)
                            target_cmd_az, _ = ctrl_az.update(err_az, measurement_ts, trim_allowed)
                            target_cmd_alt, _ = ctrl_alt.update(err_alt, measurement_ts, trim_allowed)

                        if ENABLE_RUNAWAY_BRAKE:
                            cmd_norm = float(np.hypot(cmd_az, cmd_alt))
                            if (
                                prev_radius_px is not None
                                and cmd_norm >= VEL_MIN_LIMITE
                                and radius_px > (prev_radius_px + RUNAWAY_MARGIN_PX)
                                and radius_px > (2.0 * TOLERANCIA_PX)
                            ):
                                runaway_count += 1
                            else:
                                runaway_count = 0

                            prev_radius_px = radius_px

                            if runaway_count >= RUNAWAY_FRAMES:
                                if (loop_t0 - last_runaway_log_t) >= RUNAWAY_LOG_COOLDOWN_S:
                                    print(
                                        "\n⚠️ Erro aumentou em varios frames seguidos. "
                                        "Freando o mount. Isso pode acontecer se o spot/camera "
                                        "for movido manualmente ou se houver sinal/eixo invertido."
                                    )
                                    last_runaway_log_t = loop_t0
                                target_cmd_az = 0.0
                                target_cmd_alt = 0.0
                                cmd_az = 0.0
                                cmd_alt = 0.0
                                ctrl_az.reset()
                                ctrl_alt.reset()
                                trim_mode_active = False
                                hold_active = False
                                hold_exit_count = 0
                                brake_until = loop_t0 + RUNAWAY_HOLD_S
                                runaway_count = 0

                if loop_t0 < brake_until:
                    target_cmd_az = 0.0
                    target_cmd_alt = 0.0

                target_cmd_az = float(np.clip(target_cmd_az, -VEL_MAX_TESTE, VEL_MAX_TESTE))
                target_cmd_alt = float(np.clip(target_cmd_alt, -VEL_MAX_TESTE, VEL_MAX_TESTE))

                max_step = CMD_ACCEL_LIMIT * dt_loop
                cmd_az = _slew_limit(cmd_az, target_cmd_az, max_step)
                cmd_alt = _slew_limit(cmd_alt, target_cmd_alt, max_step)

                if abs(target_cmd_az) < 1e-12 and abs(cmd_az) < VEL_MIN_LIMITE:
                    cmd_az = 0.0
                if abs(target_cmd_alt) < 1e-12 and abs(cmd_alt) < VEL_MIN_LIMITE:
                    cmd_alt = 0.0

                cmd_az = _apply_min_velocity(float(np.clip(cmd_az, -VEL_MAX_TESTE, VEL_MAX_TESTE)))
                cmd_alt = _apply_min_velocity(float(np.clip(cmd_alt, -VEL_MAX_TESTE, VEL_MAX_TESTE)))

                send_az = (
                    last_sent_az is None
                    or abs(cmd_az - last_sent_az) >= MIN_CMD_DELTA_TO_SEND
                    or (loop_t0 - last_sent_az_t) >= CMD_KEEPALIVE_S
                )
                send_alt = (
                    last_sent_alt is None
                    or abs(cmd_alt - last_sent_alt) >= MIN_CMD_DELTA_TO_SEND
                    or (loop_t0 - last_sent_alt_t) >= CMD_KEEPALIVE_S
                )

                future_az = None
                future_alt = None
                if usar_mount and send_az:
                    future_az = executor.submit(move_axis, 0, cmd_az, usar_mount)
                if usar_mount and send_alt:
                    future_alt = executor.submit(move_axis, 1, cmd_alt, usar_mount)

                if future_az is not None:
                    future_az.result()
                    last_sent_az = cmd_az
                    last_sent_az_t = loop_t0
                if future_alt is not None:
                    future_alt.result()
                    last_sent_alt = cmd_alt
                    last_sent_alt_t = loop_t0

                with state.lock:
                    state.err_az_deg = err_az
                    state.err_alt_deg = err_alt
                    state.cmd_az_deg_s = cmd_az
                    state.cmd_alt_deg_s = cmd_alt
                    state.brake_active = loop_t0 < brake_until
                    state.active_matrix_name = active_matrix_name
                    state.trim_mode_active = trim_mode_active
                    state.hold_active = hold_active
                    state.control_loop_hz = control_loop_hz

                elapsed = time.perf_counter() - loop_t0
                if elapsed < dt_target:
                    time.sleep(dt_target - elapsed)

        finally:
            stop_axes_safely()


# =============================================================================
# BLOCO 7 - EXECUCAO, TELA E ENCERRAMENTO
# =============================================================================
# Coordena os blocos anteriores. O finally e proposital: ele tenta parar o
# mount mesmo quando ocorre erro, Ctrl+C ou fechamento da janela.

def main():
    global VEL_MAX_TESTE, MAX_OFFSET_AZ_DEG, MAX_OFFSET_ALT_DEG

    logger = None
    return_result = None
    finish_reason = "encerramento_normal"
    last_frame = None
    initial_position = None
    usar_mount = False
    safe_mount_test = False
    observation_only = True
    operation_label = "OBSERVACAO - MOUNT SEM MOVIMENTO"
    try:
        ensure_connected()
        ensure_unparked()
        ensure_not_tracking()
        connect_camera()

        saved_target_for_mode = carregar_alvo_salvo()
        default_focus_input = (
            "2"
            if saved_target_for_mode is not None
            and _normalize_focus_mode(saved_target_for_mode.focus_mode or "single") == "dual"
            else "1"
        )
        focus_input = (
            input(
                "Modo do laser (1=foco unico, 2=dupla reflexao/ilha travada) "
                f"[{default_focus_input}]: "
            ).strip()
            or default_focus_input
        )
        focus_mode = _normalize_focus_mode(focus_input)
        foco_temp.set_focus_mode(focus_mode)
        operation_input = (
            input(
                "Operacao (1=observar sem mover, 2=teste limitado do mount, "
                "3=tracking normal) [1]: "
            ).strip()
            or "1"
        )
        if operation_input not in {"1", "2", "3"}:
            raise ValueError("Escolha 1, 2 ou 3 para o modo de operacao.")
        usar_mount = operation_input in {"2", "3"}
        safe_mount_test = operation_input == "2"
        observation_only = operation_input == "1"
        if safe_mount_test:
            VEL_MAX_TESTE = min(SAFE_TEST_MAX_RATE_DEG_S, VEL_MAX_LIMITE)
            MAX_OFFSET_AZ_DEG = SAFE_TEST_MAX_OFFSET_DEG
            MAX_OFFSET_ALT_DEG = SAFE_TEST_MAX_OFFSET_DEG
            default_session_hours = SAFE_TEST_MAX_SECONDS / 3600.0
            operation_label = "TESTE LIMITADO DO MOUNT"
        elif observation_only:
            default_session_hours = 30.0 / 3600.0
            operation_label = "OBSERVACAO - MOUNT SEM MOVIMENTO"
        else:
            default_session_hours = MAX_SESSION_HOURS
            operation_label = "TRACKING NORMAL"
        session_hours_input = input(
            f"Tempo maximo da sessao em horas [{default_session_hours:g}]: "
        ).strip()
        session_hours = (
            float(session_hours_input.replace(",", "."))
            if session_hours_input
            else default_session_hours
        )
        if session_hours <= 0.0:
            raise ValueError("O tempo maximo da sessao precisa ser positivo.")
        if safe_mount_test:
            session_hours = min(session_hours, SAFE_TEST_MAX_SECONDS / 3600.0)

        matrices = _load_tracking_calibration_matrices(focus_mode)
        sensor_w, sensor_h = get_camera_size()
        alvo = escolher_referencia_tracker(sensor_w, sensor_h, focus_mode)

        state = SharedState()
        initial_az_deg, initial_alt_deg = read_altaz()
        initial_position = (initial_az_deg, initial_alt_deg)
        session_started = time.perf_counter()
        max_session_seconds = session_hours * 3600.0
        with state.lock:
            state.mount_az_deg = initial_az_deg
            state.mount_alt_deg = initial_alt_deg
        print(
            "Posicao absoluta inicial da sessao: "
            f"Az={initial_az_deg:.6f} deg, Alt={initial_alt_deg:.6f} deg"
        )

        logger = TrackerCsvLogger(
            TRACKER_OUTPUT_DIR,
            session_started,
            initial_az_deg,
            initial_alt_deg,
            session_hours,
        )

        win_name = "Tracker 4QD Continuo - V2"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        print("\nTracker continuo V2 iniciado.")
        print(f"MODO: {operation_label}")
        if observation_only:
            print(
                "GARANTIA: este modo calcula e registra correcoes, mas nao envia "
                "velocidades de movimento ao mount (somente a parada em zero de seguranca)."
            )
        elif safe_mount_test:
            print(
                f"LIMITES DO TESTE: velocidade=+/-{VEL_MAX_TESTE:.4f} deg/s | "
                f"deslocamento=+/-{SAFE_TEST_MAX_OFFSET_DEG:.3f} deg | "
                f"tempo<={SAFE_TEST_MAX_SECONDS:.0f}s."
            )
        print("ROI nativa da camera, zona de repouso e watchdog absoluto.")
        print(f"Imagem IDS rotacionada 180 graus: {'sim' if ROTATE_IMAGE_180 else 'nao'}")
        print(
            f"Modo do laser: {focus_mode} | "
            f"movimento do mount: {'habilitado' if usar_mount else 'bloqueado'}."
        )
        print(
            f"Matrizes carregadas | fine: {matrices['fine_path']} | "
            f"coarse: {matrices['coarse_path']}"
        )
        print(f"Alvo do tracker: {alvo.source} | x={alvo.x_px:.2f}px y={alvo.y_px:.2f}px")
        if alvo.path is not None:
            print(f"Arquivo do alvo: {alvo.path}")
        print(
            f"Sessao: {session_hours:g} h | ROI={WINDOW_SIZE}x{WINDOW_SIZE} px | "
            f"limites Az/Alt=+/-{MAX_OFFSET_AZ_DEG:g}/+/-{MAX_OFFSET_ALT_DEG:g} deg"
        )
        print(f"Telemetria CSV: {display_path(logger.csv_path)}")
        print("Pressione q para encerrar.\n")

        _, _, target_x_local, target_y_local = set_camera_roi_validated(
            WINDOW_SIZE,
            WINDOW_SIZE,
            alvo.x_px,
            alvo.y_px,
            focus_mode,
            alvo.focus_signature,
        )

        ctrl_thread = threading.Thread(
            target=control_loop_continuo,
            args=(state, matrices["fine"], matrices["coarse"], usar_mount),
            daemon=True,
        )
        watchdog_thread = threading.Thread(
            target=_position_watchdog,
            args=(
                state,
                initial_az_deg,
                initial_alt_deg,
                session_started,
                max_session_seconds,
            ),
            daemon=True,
        )
        ctrl_thread.start()
        watchdog_thread.start()

        actual_roi_w = WINDOW_SIZE
        actual_roi_h = WINDOW_SIZE
        if backend_name() == "ids" and _ids_camera().current_roi is not None:
            actual_roi_w, actual_roi_h = _ids_camera().current_roi[:2]
        scale_y = TARGET_H / actual_roi_h
        target_w = max(1, int(round(actual_roi_w * scale_y)))
        scale_x = target_w / actual_roi_w
        cx_L = int(target_x_local * scale_x)
        cy_L = int(target_y_local * scale_y)
        display_interval_s = 1.0 / DISPLAY_HZ
        last_display_t = 0.0
        last_measurement_t = 0.0
        measurement_hz = 0.0
        border_frames = 0
        signal_lost_since = None

        while True:
            frame_window = capture_frame(EXPOSURE_SECONDS)
            last_frame = frame_window
            t_now = time.perf_counter()
            if last_measurement_t > 0.0:
                measurement_dt = max(t_now - last_measurement_t, 1e-4)
                instant_measurement_hz = 1.0 / measurement_dt
                measurement_hz = (
                    instant_measurement_hz
                    if measurement_hz <= 0.0
                    else (0.15 * instant_measurement_hz) + (0.85 * measurement_hz)
                )
            last_measurement_t = t_now
            cm = medir_laser(frame_window, focus_mode)
            focus_debug = foco_temp.get_focus_debug() if focus_mode == "dual" else {}
            selected_debug = focus_debug.get("selected") or {}
            spot_touches_border = bool(selected_debug.get("toca_borda", False))

            if spot_touches_border:
                border_frames += 1
            else:
                border_frames = 0

            if cm is None:
                if signal_lost_since is None:
                    signal_lost_since = t_now
                dx = 0.0
                dy = 0.0
                x_cm_local = target_x_local
                y_cm_local = target_y_local
                cor_laser = (0, 0, 255)
            else:
                x_cm_local, y_cm_local = cm
                dx = float(x_cm_local - target_x_local)
                dy = float(y_cm_local - target_y_local)
                if not spot_touches_border:
                    signal_lost_since = None
                cor_laser = (0, 165, 255) if spot_touches_border else (0, 255, 255)

            measurement_valid = cm is not None and not spot_touches_border
            if not measurement_valid:
                with state.lock:
                    state.has_signal = False
                    state.spot_touches_border = spot_touches_border
                    state.measurement_seq += 1
                    state.measurement_ts = t_now
                    state.measurement_hz = measurement_hz
            else:
                with state.lock:
                    if state.measurement_seq == 0 or not state.has_signal:
                        dx_filt = dx
                        dy_filt = dy
                    else:
                        dx_filt = (MEASUREMENT_ALPHA * dx) + ((1.0 - MEASUREMENT_ALPHA) * state.dx_filt_px)
                        dy_filt = (MEASUREMENT_ALPHA * dy) + ((1.0 - MEASUREMENT_ALPHA) * state.dy_filt_px)

                    state.dx_filt_px = float(dx_filt)
                    state.dy_filt_px = float(dy_filt)
                    state.has_signal = True
                    state.spot_touches_border = False
                    state.measurement_seq += 1
                    state.measurement_ts = t_now
                    state.measurement_hz = measurement_hz

            if border_frames >= BORDER_CONFIRM_FRAMES:
                _request_safety_stop(state, "ilha_tocou_a_borda_da_roi")
            if (
                cm is None
                and signal_lost_since is not None
                and (t_now - signal_lost_since) >= SIGNAL_LOSS_LIMIT_SECONDS
            ):
                _request_safety_stop(state, "sinal_perdido_por_tempo_excessivo")

            state_values = _state_snapshot(state)
            brake_active = state_values["brake_active"]
            active_matrix_name = state_values["active_matrix_name"]
            trim_mode_active = state_values["trim_mode_active"]
            hold_active = state_values["hold_active"]
            err_az_deg = state_values["err_az_deg"]
            err_alt_deg = state_values["err_alt_deg"]
            cmd_az_deg_s = state_values["cmd_az_deg_s"]
            cmd_alt_deg_s = state_values["cmd_alt_deg_s"]
            measurement_hz_display = state_values["measurement_hz"]
            control_loop_hz_display = state_values["control_loop_hz"]

            dist_px = float(np.hypot(dx, dy))
            if state_values["safety_stop_reason"]:
                status_text = "PARADA DE SEGURANCA"
                status_color = (0, 0, 255)
            elif spot_touches_border:
                status_text = "ILHA NA BORDA"
                status_color = (0, 0, 255)
            elif cm is None:
                status_text = "SEM SINAL"
                status_color = (0, 0, 255)
            elif brake_active:
                status_text = "FREIO DE SEGURANCA"
                status_color = (0, 0, 255)
            elif hold_active:
                status_text = "CENTRALIZADO - REPOUSO"
                status_color = (0, 255, 0)
            elif observation_only:
                status_text = "OBSERVANDO - SEM MOVER"
                status_color = (0, 255, 255)
            elif safe_mount_test:
                status_text = "TESTE LIMITADO"
                status_color = (0, 255, 255)
            else:
                status_text = "RASTREANDO"
                status_color = (0, 255, 255)

            logger.write(
                t_now,
                state_values=state_values,
                status=status_text,
                x_cm=None if cm is None else x_cm_local,
                y_cm=None if cm is None else y_cm_local,
                target_x=target_x_local,
                target_y=target_y_local,
                dx=dx,
                dy=dy,
                event=state_values["safety_stop_reason"] or "",
            )

            if state_values["safety_stop_reason"]:
                finish_reason = state_values["safety_stop_reason"]
                event_frame = logger.save_event_frame(last_frame, finish_reason)
                if event_frame is not None:
                    print(f"Frame do evento salvo em: {display_path(event_frame)}")
                print(f"\nPARADA DE SEGURANCA: {finish_reason}")
                break

            if last_display_t == 0.0 or (t_now - last_display_t) >= display_interval_s:
                frame_display = cv2.cvtColor(frame_window, cv2.COLOR_GRAY2BGR)
                frame_display_large = cv2.resize(
                    frame_display,
                    (target_w, TARGET_H),
                    interpolation=cv2.INTER_NEAREST,
                )

                x_cm_L = int(x_cm_local * scale_x)
                y_cm_L = int(y_cm_local * scale_y)

                cv2.line(frame_display_large, (cx_L, 0), (cx_L, TARGET_H), (255, 0, 0), 2)
                cv2.line(frame_display_large, (0, cy_L), (target_w, cy_L), (0, 0, 255), 2)
                cv2.circle(frame_display_large, (x_cm_L, y_cm_L), 8, cor_laser, -1)

                cv2.putText(
                    frame_display_large,
                    f"Erro na imagem: {dist_px:.1f} px | X={dx:+.1f} px | Y={dy:+.1f} px",
                    (40, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.78,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                focus_label = "ilha travada" if focus_mode == "dual" else "foco unico"
                matrix_label = "fina" if active_matrix_name == "fine" else "ampla"
                hold_label = "ATIVA" if hold_active else "inativa"
                cv2.putText(
                    frame_display_large,
                    (
                        f"Alvo: {focus_label} | Calibracao: {matrix_label} | "
                        f"Zona parada: {hold_label} ({HOLD_ENTER_RADIUS_PX:.0f}/{HOLD_EXIT_RADIUS_PX:.0f} px)"
                    ),
                    (40, 110),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.78,
                    (0, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                elapsed_hours = (t_now - session_started) / 3600.0
                cv2.putText(
                    frame_display_large,
                    (
                        f"Sessao: {elapsed_hours:.2f}/{session_hours:.2f} h | "
                        f"Posicao desde inicio: Az={state_values['offset_az_deg']:+.3f} deg | "
                        f"Alt={state_values['offset_alt_deg']:+.3f} deg"
                    ),
                    (40, 310),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.74,
                    (200, 200, 200),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame_display_large,
                    (
                        f"Erro do mount: Az={err_az_deg:+.5f} deg | "
                        f"Alt={err_alt_deg:+.5f} deg"
                    ),
                    (40, 160),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.82,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame_display_large,
                    (
                        f"Velocidade {'calculada' if observation_only else 'enviada'}: "
                        f"Az={cmd_az_deg_s:+.4f} deg/s | "
                        f"Alt={cmd_alt_deg_s:+.4f} deg/s"
                    ),
                    (40, 210),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.82,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame_display_large,
                    (
                        f"Loops: medicao={measurement_hz_display:.1f} Hz | "
                        f"controle={control_loop_hz_display:.1f} Hz | tela={DISPLAY_HZ:.1f} Hz"
                    ),
                    (40, 260),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.82,
                    (255, 200, 0),
                    2,
                    cv2.LINE_AA,
                )
                status_x = max(40, target_w - max(360, 22 * len(status_text)))
                cv2.putText(
                    frame_display_large,
                    status_text,
                    (status_x, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    status_color,
                    3,
                    cv2.LINE_AA,
                )

                cv2.imshow(win_name, frame_display_large)
                last_display_t = t_now

            key = cv2.waitKeyEx(1)
            if key in (ord("q"), ord("Q"), 27):
                finish_reason = "encerrado_pelo_teclado"
                break
            if cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
                finish_reason = "janela_fechada"
                break

    except KeyboardInterrupt:
        finish_reason = "interrompido_por_ctrl_c"
        print("\nInterrompido pelo usuario.")
    except Exception as exc:
        finish_reason = f"erro_{type(exc).__name__}"
        print(f"\nErro no tracker continuo V2: {exc}")
    finally:
        safety_reason = None
        if "state" in locals():
            with state.lock:
                state.stop = True
                safety_reason = state.safety_stop_reason
            if "ctrl_thread" in locals() and ctrl_thread.is_alive():
                ctrl_thread.join(timeout=2.0)
            if "watchdog_thread" in locals() and watchdog_thread.is_alive():
                watchdog_thread.join(timeout=2.0)

        try:
            if "usar_mount" in locals():
                stop_axes_safely()
        except Exception:
            pass

        if (
            (safety_reason or safe_mount_test)
            and initial_position is not None
            and usar_mount
            and RETURN_TO_START_ON_LIMIT
        ):
            print(
                "Retornando a posicao absoluta inicial antes de encerrar "
                f"(motivo: {safety_reason or 'fim_do_teste_limitado'})."
            )
            return_result = _return_to_initial_position(*initial_position)
            if return_result.get("success"):
                print("Posicao absoluta inicial restaurada.")
            else:
                print(f"ALERTA: retorno inicial nao confirmado: {return_result}")

        if logger is not None:
            try:
                logger.close(reason=finish_reason, return_result=return_result)
                print(f"Telemetria salva em: {display_path(logger.csv_path)}")
            except Exception as exc:
                print(f"ALERTA: nao consegui finalizar o CSV: {exc}")

        reset_camera_roi()
        disconnect_camera()
        cv2.destroyAllWindows()
        print("Controle encerrado com parada segura.")


if __name__ == "__main__":
    main()
