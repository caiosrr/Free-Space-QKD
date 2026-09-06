"""Camera, selecao da ilha e ROI usados pelo tracker.

Objetivo: aplicar a ROI ao redor da ilha travada e entregar frames ja
normalizados ao laco de aquisicao.
Entradas/saidas: pixels da ROI; nao move o mount.
Hardware: IDS peak ou ASI/ASCOM, conforme o backend selecionado.

A captura, a normalizacao e o cliente ASCOM sao os mesmos usados pela
calibracao: este modulo nao mantem uma segunda copia deles.
"""

import os
from pathlib import Path

import cv2
import numpy as np

cv2.setUseOptimized(True)

from modulos.configuracoes.camera_asi import GAIN as ASI_GAIN
from modulos.configuracoes.tracker import TRACKER_MAX_SPOT_JUMP_PX
from modulos.controle.alvo_alinhamento import AlvoAlinhamento, roi_incluindo_alvo
from modulos.controle.cameras.alpaca import call
from modulos.controle.cameras.backend import backend_name
from modulos.visao import detector_ilhas as foco_temp

ROOT_DIR = Path(__file__).resolve().parents[2]

# Uma unica fonte para exposicao e orientacao, compartilhada com a calibracao.
EXPOSURE_SECONDS = foco_temp.EXPOSURE_SECONDS
ROTATE_IMAGE_180 = foco_temp.ROTATE_IMAGE_180
IDS_MATRIX_PREFIX = foco_temp.IDS_MATRIX_PREFIX
TRACKER_OUTPUT_DIR = Path(
    os.environ.get("QKD_TRACKER_OUTPUT_DIR", ROOT_DIR / "resultados" / "debug")
)


def _measure_locked_island(frame):
    cm = foco_temp.centro_massa(frame)
    if cm is None:
        return None
    return float(cm[0]), float(cm[1])


def _ids_camera():
    from modulos.controle.cameras.ids_peak import camera

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
    focus_signature: dict,
) -> tuple[int, int, float, float]:
    """Aplica a ROI e confirma que a ilha selecionada continua dentro dela."""
    max_x, max_y = get_camera_size()
    display_w, display_h = ((max_x, max_y) if backend_name() == "ids" else (max_y, max_x))
    target_x = float(np.clip(target_x, 0, display_w - 1))
    target_y = float(np.clip(target_y, 0, display_h - 1))

    if backend_name() == "ids":
        candidates = [
            ("rot180", "IDS rotacionada 180 graus")
            if ROTATE_IMAGE_180
            else ("direct", "IDS sem rotacao")
        ]
    else:
        candidates = [
            ("rot180_ascom_axes", "eixos ASCOM"),
            ("rot180", "coordenadas antigas rotacionadas"),
            ("direct", "coordenadas diretas"),
        ]

    best = None
    for mode, description in candidates:
        start_x, start_y, _, _ = _roi_params_for_target(
            max_x, max_y, w, h, target_x, target_y, mode=mode
        )
        actual_roi = _apply_camera_roi(w, h, start_x, start_y)
        actual_w, actual_h, start_x, start_y = actual_roi
        local_x, local_y = _target_local_in_actual_roi(
            max_x, max_y, target_x, target_y, actual_roi, mode
        )
        if not (0 <= local_x < actual_w and 0 <= local_y < actual_h):
            continue

        foco_temp.initialize_focus_lock(
            focus_signature,
            local_x,
            local_y,
            max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
        )
        frame = capture_frame(EXPOSURE_SECONDS)
        cm = _measure_locked_island(frame)
        if cm is None:
            print(f"ROI {description}: ilha nao confirmada.")
            continue

        distance = float(np.hypot(cm[0] - local_x, cm[1] - local_y))
        if best is None or distance < best[0]:
            best = (distance, actual_roi, local_x, local_y, mode, frame)

    if best is None:
        raise RuntimeError(
            "A ilha selecionada nao foi encontrada na ROI. "
            "Selecione novamente ou aumente a ROI na configuracao."
        )

    _, actual_roi, local_x, local_y, mode, frame = best
    actual_roi = _apply_camera_roi(*actual_roi)
    actual_w, actual_h, start_x, start_y = actual_roi
    local_x, local_y = _target_local_in_actual_roi(
        max_x, max_y, target_x, target_y, actual_roi, mode
    )
    foco_temp.initialize_focus_lock(
        focus_signature,
        local_x,
        local_y,
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    )

    debug_path = TRACKER_OUTPUT_DIR / "tracker_roi_teste.png"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(debug_path), frame)
    print(
        f"ROI confirmada: {actual_w}x{actual_h} em ({start_x}, {start_y}) | "
        f"alvo local=({local_x:.1f}, {local_y:.1f})"
    )
    return start_x, start_y, local_x, local_y



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


def escolher_referencia_tracker() -> AlvoAlinhamento:
    """Mostra o sensor inteiro e trava a ilha escolhida para esta sessao."""
    reset_camera_roi()
    foco_temp.set_focus_mode("dual")
    foco_temp.reset_focus_lock()
    selection = foco_temp.escolher_ilha_manualmente(
        capture_frame(EXPOSURE_SECONDS),
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    )
    print(
        "Ilha selecionada: "
        f"x={selection['x_px']:.2f}px, y={selection['y_px']:.2f}px."
    )
    return AlvoAlinhamento(
        x_px=float(selection["x_px"]),
        y_px=float(selection["y_px"]),
        source="selecao_manual_tracker",
        path=None,
        focus_mode="dual",
        focus_signature=selection["signature"],
    )



def connect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().connect()
        return
    print("Conectando a camera...")
    call("PUT", "connected", data={"Connected": True})
    call("PUT", "gain", data={"Gain": int(ASI_GAIN)})
    print(
        f"Camera ASI configurada: ganho={ASI_GAIN}, "
        f"exposicao={EXPOSURE_SECONDS * 1e6:.1f} us"
    )


def disconnect_camera() -> None:
    if backend_name() == "ids":
        _ids_camera().disconnect()
        return
    print("Desconectando da camera...")
    call("PUT", "connected", data={"Connected": False})


def capture_frame(exposure_seconds: float) -> np.ndarray:
    """Captura e normaliza pelo mesmo caminho usado na calibracao.

    Herda dai as tentativas de captura em caso de falha. O piso absoluto de
    contagens fica DESLIGADO aqui, como sempre esteve neste modulo: com a
    autoexposicao em ~1150 us o pico bruto do beacon fica perto de 15
    contagens sobre um fundo de 2, e o piso de 20 do detector zeraria 94% dos
    frames de uma sessao real. Quem rejeita ruido no tracker e a trava de
    identidade da ilha, nao um limiar absoluto.
    """
    return foco_temp.capture_frame(exposure_seconds, light=True, min_raw_signal=0.0)


def latest_raw_frame() -> np.ndarray | None:
    """Frame bruto sincronizado com a ultima imagem normalizada."""
    return foco_temp.LAST_RAW_FRAME


def current_roi_size(default_size: int) -> tuple[int, int]:
    """Retorna o tamanho realmente aplicado, inclusive alinhamento da IDS."""
    if backend_name() == "ids" and _ids_camera().current_roi is not None:
        return tuple(_ids_camera().current_roi[:2])
    return int(default_size), int(default_size)
