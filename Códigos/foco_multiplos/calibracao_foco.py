import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent
if not (ROOT_DIR / "artifact_paths.py").exists():
    ROOT_DIR = ROOT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from artifact_paths import (
    display_path,
    json_candidates,
    json_output_path,
    matrix_candidates,
    matrix_output_path,
)
from foco_multiplos.Center_of_Mass_foco_temp import (
    backend_name,
    capture_frame,
    centro_massa,
    centro_massa_em_roi,
    connect_camera,
    disconnect_camera,
    escolher_ilha_manualmente,
    get_focus_debug,
    initialize_focus_lock,
    get_focus_mode,
    set_gain,
    set_focus_mode,
)
from controle.alvo_alinhamento import roi_incluindo_alvo, salvar_alvo
from config_camera_asi import EXPOSURE_SECONDS as ASI_EXPOSURE_SECONDS
from config_camera_asi import GAIN as ASI_GAIN
from config_tracker import FINE_CALIBRATION_RADII_DEG, roi_size_for_backend
from controle.mount_control import (
    TOLERANCIA_GRAUS,
    calc_error,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    move_axes_pid_2d,
    read_altaz,
    stop_axes_safely,
)

FOCO_DIR = Path(
    os.environ.get(
        "QKD_CALIBRATION_OUTPUT_DIR",
        os.environ.get("QKD_CAMERA_OUTPUT_DIR", ROOT_DIR / "foco_multiplos"),
    )
)

CAMERA_GAIN = (
    float(os.environ.get("QKD_IDS_ANALOG_GAIN", "1"))
    if backend_name() == "ids"
    else ASI_GAIN
)
EXPOSURE_SECONDS = (
    float(os.environ.get("QKD_IDS_EXPOSURE_US", "7276")) * 1e-6
    if backend_name() == "ids"
    else ASI_EXPOSURE_SECONDS
)
ROTATE_IMAGE_180 = os.environ.get("QKD_ROTATE_IMAGE_180", "1") != "0"
IDS_MATRIX_PREFIX = "ids_foco_temp" if ROTATE_IMAGE_180 else "ids_raw_foco_temp"
SETTLE_S = 1.50
CAPTURES_PER_CENTER = 2
CAPTURES_PER_POINT = 2
MAX_SAMPLE_ATTEMPTS = 3
POST_FIT_REMEASURE_PASSES = 2
CENTER_DRIFT_WEIGHT = 0.50
DISCONNECT_CAMERA_ON_EXIT = True
# A calibracao manual usa varios raios; o ajuste robusto deve combinar todos.
FINE_FIT_USE_SMALLEST_RADIUS = False
DRIFT_LIMITS_PX = {
    "coarse": {"accept": 10.0, "warn": 15.0, "reject": 20.0},
    "fine": {"accept": 6.0, "warn": 10.0, "reject": 10.0},
}
RESIDUAL_RETRY_LIMITS_PX = {
    "coarse": 15.0,
    "fine": 7.0,
}
MAX_COND = 1.0e4
MIN_SPREAD_DEG = 0.008
ROBUST_ITERS = 8
HUBER_K = 1.5
RETURN_POSITION_TOLERANCE_DEG = TOLERANCIA_GRAUS
MAX_AUTO_RETURN_DELTA_DEG = 0.25
MAX_AUTO_RETURN_ATTEMPTS = 2

COARSE_RADII_DEG = [0.04]
FINE_RADII_DEG = [0.010]

QUALITY_LIMITS = {
    "coarse": {"warn_rms_px": 10.0, "max_rms_px": 18.0},
    "fine": {"warn_rms_px": 5.0, "max_rms_px": 10.0},
}
CALIBRATION_PROFILE = "laboratorio"
MIN_FIT_RECORDS = 8
JITTER_LIMITS_PX = {
    "coarse": {"accept": None, "warn": None, "reject": None},
    "fine": {"accept": None, "warn": None, "reject": None},
}

OUTPUT_PREFIX = (
    "ids_calibracao_foco_temp"
    if backend_name() == "ids" and ROTATE_IMAGE_180
    else "ids_raw_calibracao_foco_temp"
    if backend_name() == "ids"
    else "calibracao_dual_v3_foco_temp"
)
COARSE_A_PATH = f"{IDS_MATRIX_PREFIX}_A_coarse.npy" if backend_name() == "ids" else "foco_temp_A_coarse.npy"
COARSE_A_INV_PATH = f"{IDS_MATRIX_PREFIX}_A_inv_coarse.npy" if backend_name() == "ids" else "foco_temp_A_inv_coarse.npy"
FINE_A_PATH = f"{IDS_MATRIX_PREFIX}_A_fine.npy" if backend_name() == "ids" else "foco_temp_A_fine.npy"
FINE_A_INV_PATH = f"{IDS_MATRIX_PREFIX}_A_inv_fine.npy" if backend_name() == "ids" else "foco_temp_A_inv_fine.npy"

AUDIT_DIR: Path | None = None
AUDIT_LOG = []
CALIBRATION_ROI: dict | None = None
CALIBRATION_MEASUREMENT_MODE = "sensor_completo"
MANUAL_TARGET: dict | None = None

DIRECTIONS = [
    ("az+", +1.0, 0.0),
    ("az-", -1.0, 0.0),
    ("alt+", 0.0, +1.0),
    ("alt-", 0.0, -1.0),
    ("diag++", +1.0, +1.0),
    ("diag+-", +1.0, -1.0),
    ("diag-+", -1.0, +1.0),
    ("diag--", -1.0, -1.0),
]


@dataclass
class MedicaoCM:
    x_px: float
    y_px: float
    std_x_px: float
    std_y_px: float
    samples: int
    toca_borda: bool
    timestamp_s: float


@dataclass
class RegistroDual:
    regime: str
    label: str
    radius_deg: float
    target_az_deg: float
    target_alt_deg: float
    center_before_x_px: float
    center_before_y_px: float
    center_before_std_x_px: float
    center_before_std_y_px: float
    center_after_x_px: float
    center_after_y_px: float
    center_after_std_x_px: float
    center_after_std_y_px: float
    target_x_px: float
    target_y_px: float
    target_std_x_px: float
    target_std_y_px: float
    corrected_x_px: float
    corrected_y_px: float
    center_drift_px: float
    jitter_px: float
    drift_interpolation_alpha: float


def _configure_calibration_profile(long_link: bool) -> None:
    """Seleciona limites sem enfraquecer silenciosamente o perfil de laboratorio."""
    global CALIBRATION_PROFILE
    global CAPTURES_PER_CENTER, CAPTURES_PER_POINT, MAX_SAMPLE_ATTEMPTS
    global DRIFT_LIMITS_PX, RESIDUAL_RETRY_LIMITS_PX, JITTER_LIMITS_PX
    global MIN_FIT_RECORDS, FINE_RADII_DEG

    if not long_link:
        CALIBRATION_PROFILE = "laboratorio"
        CAPTURES_PER_CENTER = 2
        CAPTURES_PER_POINT = 2
        MAX_SAMPLE_ATTEMPTS = 3
        MIN_FIT_RECORDS = 8
        FINE_RADII_DEG = [0.010]
        DRIFT_LIMITS_PX = {
            "coarse": {"accept": 10.0, "warn": 15.0, "reject": 20.0},
            "fine": {"accept": 6.0, "warn": 10.0, "reject": 10.0},
        }
        RESIDUAL_RETRY_LIMITS_PX = {"coarse": 15.0, "fine": 7.0}
        JITTER_LIMITS_PX = {
            "coarse": {"accept": None, "warn": None, "reject": None},
            "fine": {"accept": None, "warn": None, "reject": None},
        }
        return

    # Link atmosferico de varios quilometros: a mediana de cinco frames reduz
    # seeing e fontes espurias. O drift permitido cresce, mas jitter extremo
    # continua provocando repeticao/rejeicao em vez de ser aceito cegamente.
    CALIBRATION_PROFILE = "link_longo_7km"
    CAPTURES_PER_CENTER = 5
    CAPTURES_PER_POINT = 5
    MAX_SAMPLE_ATTEMPTS = 4
    MIN_FIT_RECORDS = 6
    FINE_RADII_DEG = [0.015]
    DRIFT_LIMITS_PX = {
        "coarse": {"accept": 60.0, "warn": 90.0, "reject": 140.0},
        "fine": {"accept": 45.0, "warn": 70.0, "reject": 110.0},
    }
    JITTER_LIMITS_PX = {
        "coarse": {"accept": 80.0, "warn": 120.0, "reject": 200.0},
        "fine": {"accept": 60.0, "warn": 100.0, "reject": 160.0},
    }
    RESIDUAL_RETRY_LIMITS_PX = {"coarse": 25.0, "fine": 15.0}


def _prepare_manual_tracker_roi(focus_mode: str) -> dict:
    """Seleciona uma ilha no sensor completo e prepara a ROI local do tracker."""
    global CALIBRATION_ROI, CALIBRATION_MEASUREMENT_MODE, MANUAL_TARGET
    global CALIBRATION_PROFILE, FINE_RADII_DEG
    global CAPTURES_PER_CENTER, CAPTURES_PER_POINT, MAX_SAMPLE_ATTEMPTS
    global MIN_FIT_RECORDS, DRIFT_LIMITS_PX, JITTER_LIMITS_PX
    global RESIDUAL_RETRY_LIMITS_PX

    roi_size = roi_size_for_backend(backend_name())
    max_jump_px = float((roi_size / 2) - 10)
    full_frame = capture_frame(EXPOSURE_SECONDS, light=True)
    selection = escolher_ilha_manualmente(
        full_frame,
        max_jump_px=max_jump_px,
    )
    if AUDIT_DIR is not None:
        _save_audit_frame(
            full_frame,
            AUDIT_DIR / "ilha_manual_selecionada_frame_completo.png",
            selection["x_px"],
            selection["y_px"],
            {"candidates": selection.get("candidates", [])},
        )

    sensor_h, sensor_w = full_frame.shape[:2]
    start_x, start_y, local_x, local_y = roi_incluindo_alvo(
        sensor_w,
        sensor_h,
        roi_size,
        roi_size,
        selection["x_px"],
        selection["y_px"],
    )
    signature = selection["signature"]
    if not initialize_focus_lock(
        signature,
        local_x,
        local_y,
        freeze_reference=True,
        max_jump_px=max_jump_px,
    ):
        raise RuntimeError("Nao consegui inicializar o lock da ilha escolhida.")

    target_path = salvar_alvo(
        selection["x_px"],
        selection["y_px"],
        source="manual_calibration_tracker_roi",
        frame_shape=full_frame.shape,
        samples=1,
        std_x_px=0.0,
        std_y_px=0.0,
        focus_mode=focus_mode,
        focus_signature=signature,
    )

    CALIBRATION_ROI = {
        "start_x": int(start_x),
        "start_y": int(start_y),
        "width": int(roi_size),
        "height": int(roi_size),
        "target_x_local": float(local_x),
        "target_y_local": float(local_y),
        "target_x_full": float(selection["x_px"]),
        "target_y_full": float(selection["y_px"]),
        "max_jump_px": max_jump_px,
        "saved_target_path": display_path(target_path),
    }
    MANUAL_TARGET = selection
    CALIBRATION_MEASUREMENT_MODE = "ilha_manual_roi_tracker"
    CALIBRATION_PROFILE = f"{CALIBRATION_PROFILE}_roi_manual"

    # Mede varias escalas dentro da mesma ROI. O ajuste robusto combina os
    # deslocamentos e reduz o peso de um raio contaminado por jitter ou drift.
    FINE_RADII_DEG = list(FINE_CALIBRATION_RADII_DEG)
    CAPTURES_PER_CENTER = max(CAPTURES_PER_CENTER, 5)
    CAPTURES_PER_POINT = max(CAPTURES_PER_POINT, 5)
    MAX_SAMPLE_ATTEMPTS = max(MAX_SAMPLE_ATTEMPTS, 4)
    # Com 24 trajetorias planejadas, exigir ao menos metade evita aprovar uma
    # matriz baseada em poucas direcoes depois de descartes por borda/jitter.
    MIN_FIT_RECORDS = max(MIN_FIT_RECORDS, 12)
    DRIFT_LIMITS_PX["fine"] = {"accept": 25.0, "warn": 40.0, "reject": 65.0}
    JITTER_LIMITS_PX["fine"] = {"accept": 25.0, "warn": 40.0, "reject": 70.0}
    RESIDUAL_RETRY_LIMITS_PX["fine"] = 10.0

    print(
        "Ilha manual travada: "
        f"full=({selection['x_px']:.2f}, {selection['y_px']:.2f}) px | "
        f"ROI={roi_size}x{roi_size} em ({start_x}, {start_y}) | "
        f"alvo local=({local_x:.2f}, {local_y:.2f}) px."
    )
    print(f"Alvo e assinatura congelada salvos em: {display_path(target_path)}")

    preflight = _capture_cm_estavel(
        EXPOSURE_SECONDS,
        CAPTURES_PER_CENTER,
        audit_tag="roi_preflight_sem_movimento",
    )
    if preflight is None:
        raise RuntimeError("A ilha escolhida foi perdida no teste da ROI, antes de mover o mount.")
    preflight_jitter = float(np.hypot(preflight.std_x_px, preflight.std_y_px))
    if preflight_jitter > float(JITTER_LIMITS_PX["fine"]["warn"]):
        raise RuntimeError(
            "A ilha escolhida nao ficou estavel na ROI sem movimento: "
            f"jitter={preflight_jitter:.2f}px. Escolha uma ilha mais isolada."
        )
    print(
        "Preflight da ROI aprovado: "
        f"CM=({preflight.x_px:.2f}, {preflight.y_px:.2f}) px, "
        f"jitter={preflight_jitter:.2f}px."
    )
    return CALIBRATION_ROI


def _safe_tag(text: str) -> str:
    safe = []
    for char in text:
        if char.isalnum():
            safe.append(char)
        elif char == "+":
            safe.append("p")
        elif char == "-":
            safe.append("m")
        else:
            safe.append("_")
    return "".join(safe).strip("_")


def _save_audit_frame(frame: np.ndarray, path: Path, x_cm: float | None, y_cm: float | None, debug: dict):
    marked = frame.copy()
    if marked.ndim == 2:
        marked = cv2.cvtColor(marked, cv2.COLOR_GRAY2BGR)

    h, w = frame.shape[:2]
    cv2.circle(marked, (int(round((w - 1) / 2)), int(round((h - 1) / 2))), 8, (255, 0, 0), -1)

    for candidate in debug.get("candidates", []):
        cx = int(round(candidate["x_cm"]))
        cy = int(round(candidate["y_cm"]))
        cv2.circle(marked, (cx, cy), 10, (0, 255, 255), 2)

    if x_cm is not None and y_cm is not None:
        cv2.circle(marked, (int(round(x_cm)), int(round(y_cm))), 8, (0, 255, 0), -1)

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", marked)
    if not ok:
        raise RuntimeError(f"OpenCV nao conseguiu codificar a auditoria: {path}")
    path.write_bytes(encoded.tobytes())


def _audit_capture(tag: str, repeat_idx: int, frame: np.ndarray, cm, debug: dict):
    if AUDIT_DIR is None:
        return

    safe_tag = _safe_tag(tag)
    filename = f"{safe_tag}_rep{repeat_idx + 1:02d}.png"
    path = AUDIT_DIR / filename
    if cm is None:
        x_cm = None
        y_cm = None
    else:
        x_cm = float(cm[0])
        y_cm = float(cm[1])

    _save_audit_frame(frame, path, x_cm, y_cm, debug)
    AUDIT_LOG.append(
        {
            "tag": tag,
            "repeat": repeat_idx + 1,
            "frame": str(path.relative_to(ROOT_DIR)),
            "ok": cm is not None,
            "x_px": x_cm,
            "y_px": y_cm,
            "focus_debug": debug,
        }
    )


def _capture_cm_estavel(exposure: float, repeats: int, audit_tag: str) -> MedicaoCM | None:
    xs = []
    ys = []
    timestamps = []
    toca_borda = False

    for repeat_idx in range(repeats):
        try:
            full_frame = capture_frame(exposure, light=True)
        except Exception as exc:
            print(
                f"  -> captura falhou em {audit_tag} "
                f"({repeat_idx + 1}/{repeats}): {exc}"
            )
            return None
        if CALIBRATION_ROI is None:
            frame = full_frame
            cm = centro_massa(frame)
        else:
            frame, cm = centro_massa_em_roi(
                full_frame,
                CALIBRATION_ROI["start_x"],
                CALIBRATION_ROI["start_y"],
                CALIBRATION_ROI["width"],
                CALIBRATION_ROI["height"],
            )
        debug = get_focus_debug()
        _audit_capture(audit_tag, repeat_idx, frame, cm, debug)
        if cm is None:
            return None
        x_cm, y_cm, _, cm_toca_borda = cm
        xs.append(float(x_cm))
        ys.append(float(y_cm))
        timestamps.append(time.perf_counter())
        toca_borda = toca_borda or bool(cm_toca_borda)

    xs_arr = np.array(xs, dtype=float)
    ys_arr = np.array(ys, dtype=float)
    if repeats >= 5:
        spread_x = 1.4826 * float(np.median(np.abs(xs_arr - np.median(xs_arr))))
        spread_y = 1.4826 * float(np.median(np.abs(ys_arr - np.median(ys_arr))))
    else:
        spread_x = float(np.std(xs_arr))
        spread_y = float(np.std(ys_arr))
    return MedicaoCM(
        x_px=float(np.median(xs_arr)),
        y_px=float(np.median(ys_arr)),
        std_x_px=spread_x,
        std_y_px=spread_y,
        samples=repeats,
        toca_borda=toca_borda,
        timestamp_s=float(np.median(np.asarray(timestamps, dtype=float))),
    )


def _move_and_settle(mount: bool, delta_az: float, delta_alt: float):
    if abs(delta_az) <= 1e-6 and abs(delta_alt) <= 1e-6:
        return
    move_axes_pid_2d(mount, float(delta_az), float(delta_alt))
    time.sleep(SETTLE_S)


def _collect_bracketed_sample_once(
    regime: str,
    label: str,
    radius_deg: float,
    target_az_deg: float,
    target_alt_deg: float,
    exposure: float,
    mount: bool,
    attempt_idx: int,
    audit_label: str | None = None,
) -> RegistroDual | None:
    audit_label = label if audit_label is None else audit_label
    tag_base = f"{regime}_{audit_label}_try{attempt_idx + 1:02d}"

    center_before = _capture_cm_estavel(
        exposure,
        CAPTURES_PER_CENTER,
        audit_tag=f"{tag_base}_center_before",
    )
    if center_before is None:
        print(f"  -> centro antes falhou em {label}.")
        return None
    if center_before.toca_borda:
        print(f"  -> centro antes tocou borda em {label}; descartando.")
        return None

    target_cm = None
    returned_to_center = False
    try:
        _move_and_settle(mount, target_az_deg, target_alt_deg)
        target_cm = _capture_cm_estavel(
            exposure,
            CAPTURES_PER_POINT,
            audit_tag=f"{tag_base}_target",
        )
    except Exception as exc:
        print(f"  -> erro durante movimento/captura do ponto {label}: {exc}")
    finally:
        try:
            _move_and_settle(mount, -target_az_deg, -target_alt_deg)
            returned_to_center = True
        except Exception as exc:
            print(f"  -> erro voltando ao centro apos {label}: {exc}")

    if not returned_to_center:
        return None

    center_after = _capture_cm_estavel(
        exposure,
        CAPTURES_PER_CENTER,
        audit_tag=f"{tag_base}_center_after",
    )

    if target_cm is None:
        print(f"  -> ponto {label} sem sinal; descartando.")
        return None
    if target_cm.toca_borda:
        print(f"  -> ponto {label} tocou borda; descartando.")
        return None
    if center_after is None:
        print(f"  -> centro depois falhou em {label}.")
        return None
    if center_after.toca_borda:
        print(f"  -> centro depois tocou borda em {label}; descartando.")
        return None

    center_drift_px = float(
        np.hypot(
            center_after.x_px - center_before.x_px,
            center_after.y_px - center_before.y_px,
        )
    )
    bracket_duration = center_after.timestamp_s - center_before.timestamp_s
    if bracket_duration > 1e-9:
        drift_alpha = float(
            np.clip(
                (target_cm.timestamp_s - center_before.timestamp_s) / bracket_duration,
                0.0,
                1.0,
            )
        )
    else:
        drift_alpha = 0.5
    x_ref = (1.0 - drift_alpha) * center_before.x_px + drift_alpha * center_after.x_px
    y_ref = (1.0 - drift_alpha) * center_before.y_px + drift_alpha * center_after.y_px
    corrected_x = target_cm.x_px - x_ref
    corrected_y = target_cm.y_px - y_ref

    jitter_px = float(
        np.hypot(target_cm.std_x_px, target_cm.std_y_px)
        + 0.5 * np.hypot(center_before.std_x_px, center_before.std_y_px)
        + 0.5 * np.hypot(center_after.std_x_px, center_after.std_y_px)
        + CENTER_DRIFT_WEIGHT * center_drift_px
    )

    return RegistroDual(
        regime=regime,
        label=label,
        radius_deg=radius_deg,
        target_az_deg=target_az_deg,
        target_alt_deg=target_alt_deg,
        center_before_x_px=center_before.x_px,
        center_before_y_px=center_before.y_px,
        center_before_std_x_px=center_before.std_x_px,
        center_before_std_y_px=center_before.std_y_px,
        center_after_x_px=center_after.x_px,
        center_after_y_px=center_after.y_px,
        center_after_std_x_px=center_after.std_x_px,
        center_after_std_y_px=center_after.std_y_px,
        target_x_px=target_cm.x_px,
        target_y_px=target_cm.y_px,
        target_std_x_px=target_cm.std_x_px,
        target_std_y_px=target_cm.std_y_px,
        corrected_x_px=float(corrected_x),
        corrected_y_px=float(corrected_y),
        center_drift_px=center_drift_px,
        jitter_px=float(jitter_px),
        drift_interpolation_alpha=drift_alpha,
    )


def _collect_bracketed_sample(
    regime: str,
    label: str,
    radius_deg: float,
    target_az_deg: float,
    target_alt_deg: float,
    exposure: float,
    mount: bool,
    audit_label: str | None = None,
) -> RegistroDual | None:
    limits = DRIFT_LIMITS_PX[regime]
    jitter_limits = JITTER_LIMITS_PX[regime]
    best_record = None
    best_score = float("inf")

    for attempt_idx in range(MAX_SAMPLE_ATTEMPTS):
        if attempt_idx > 0:
            print(f"  -> repetindo {label}: drift/jitter alto na tentativa anterior.")
        registro = _collect_bracketed_sample_once(
            regime=regime,
            label=label,
            radius_deg=radius_deg,
            target_az_deg=target_az_deg,
            target_alt_deg=target_alt_deg,
            exposure=exposure,
            mount=mount,
            attempt_idx=attempt_idx,
            audit_label=audit_label,
        )
        if registro is None:
            continue
        score = registro.center_drift_px / max(float(limits["accept"]), 1e-6)
        if jitter_limits["accept"] is not None:
            score += registro.jitter_px / max(float(jitter_limits["accept"]), 1e-6)
        if best_record is None or score < best_score:
            best_record = registro
            best_score = score

        drift_ok = registro.center_drift_px <= limits["accept"]
        jitter_ok = (
            jitter_limits["accept"] is None
            or registro.jitter_px <= jitter_limits["accept"]
        )
        if drift_ok and jitter_ok:
            return registro
        print(
            f"  -> medida instavel: drift={registro.center_drift_px:.2f}px "
            f"(aceite {limits['accept']:.1f}), jitter={registro.jitter_px:.2f}px"
            + (
                f" (aceite {jitter_limits['accept']:.1f})."
                if jitter_limits["accept"] is not None
                else "."
            )
        )

    if best_record is None:
        return None

    if best_record.center_drift_px >= limits["warn"]:
        print(
            f"  -> aviso: usando {label} com drift centro alto "
            f"({best_record.center_drift_px:.2f}px); peso reduzido no ajuste."
        )
    if (
        jitter_limits["warn"] is not None
        and best_record.jitter_px >= jitter_limits["warn"]
    ):
        print(
            f"  -> aviso: usando {label} com jitter alto "
            f"({best_record.jitter_px:.2f}px); peso reduzido no ajuste."
        )
    return best_record


def _build_star_sequence(radii_deg: list[float]):
    sequence = []
    for radius_deg in radii_deg:
        for direction_label, sign_az, sign_alt in DIRECTIONS:
            label = f"{direction_label}@{radius_deg:.4f}"
            sequence.append(
                (
                    label,
                    radius_deg,
                    float(radius_deg * sign_az),
                    float(radius_deg * sign_alt),
                )
            )
    return sequence


def _replace_record(registros: list[RegistroDual], novo: RegistroDual) -> None:
    for idx, registro in enumerate(registros):
        if registro.label == novo.label:
            registros[idx] = novo
            return
    registros.append(novo)


def _collect_regime(
    regime: str,
    radii_deg: list[float],
    exposure: float,
    mount: bool,
) -> list[RegistroDual]:
    print(f"\n{'=' * 72}")
    print(f"Coleta {regime.upper()} | raios {', '.join(f'{r:.4f}' for r in radii_deg)} deg")
    print(f"{'=' * 72}")

    registros: list[RegistroDual] = []
    for idx, (label, radius_deg, target_az_deg, target_alt_deg) in enumerate(_build_star_sequence(radii_deg), start=1):
        print(
            f"[{idx:02d}] {label} | "
            f"dAz={target_az_deg:+.4f} deg dAlt={target_alt_deg:+.4f} deg"
        )
        registro = _collect_bracketed_sample(
            regime=regime,
            label=label,
            radius_deg=radius_deg,
            target_az_deg=target_az_deg,
            target_alt_deg=target_alt_deg,
            exposure=exposure,
            mount=mount,
        )
        if registro is None:
            continue
        registros.append(registro)
        print(
            f"  -> corrigido: x={registro.corrected_x_px:+.2f}px "
            f"y={registro.corrected_y_px:+.2f}px | "
            f"drift={registro.center_drift_px:.2f}px | jitter={registro.jitter_px:.2f}px"
        )

    print(f"Registros validos {regime}: {len(registros)}")
    return registros


def _residuals_for_records(A: np.ndarray, registros: list[RegistroDual]) -> list[dict]:
    residuals = []
    for registro in registros:
        offset = np.array([registro.target_az_deg, registro.target_alt_deg], dtype=float)
        pred = A @ offset
        residual_px = float(
            np.hypot(
                pred[0] - registro.corrected_x_px,
                pred[1] - registro.corrected_y_px,
            )
        )
        residuals.append(
            {
                "label": registro.label,
                "residual_px": residual_px,
                "pred_x_px": float(pred[0]),
                "pred_y_px": float(pred[1]),
                "measured_x_px": registro.corrected_x_px,
                "measured_y_px": registro.corrected_y_px,
                "center_drift_px": registro.center_drift_px,
                "jitter_px": registro.jitter_px,
            }
        )
    return residuals


def _records_fit_ready(registros: list[RegistroDual]) -> bool:
    if len(registros) < MIN_FIT_RECORDS:
        return False
    offsets = np.array(
        [[r.target_az_deg, r.target_alt_deg] for r in registros],
        dtype=float,
    )
    spread_az = float(np.ptp(offsets[:, 0]))
    spread_alt = float(np.ptp(offsets[:, 1]))
    return (
        spread_az >= MIN_SPREAD_DEG
        and spread_alt >= MIN_SPREAD_DEG
        and np.linalg.matrix_rank(offsets) >= 2
    )


def _filter_records_for_fit(registros: list[RegistroDual], regime: str) -> tuple[list[RegistroDual], list[dict]]:
    reject_limit = DRIFT_LIMITS_PX[regime].get("reject")
    jitter_reject_limit = JITTER_LIMITS_PX[regime].get("reject")
    if reject_limit is None and jitter_reject_limit is None:
        return registros, []

    kept = []
    rejected = []
    for registro in registros:
        drift_rejected = (
            reject_limit is not None and registro.center_drift_px > reject_limit
        )
        jitter_rejected = (
            jitter_reject_limit is not None and registro.jitter_px > jitter_reject_limit
        )
        if drift_rejected or jitter_rejected:
            rejected.append(
                {
                    "label": registro.label,
                    "center_drift_px": registro.center_drift_px,
                    "reject_limit_px": reject_limit,
                    "jitter_px": registro.jitter_px,
                    "jitter_reject_limit_px": jitter_reject_limit,
                    "reason": ",".join(
                        reason
                        for reason, active in (
                            ("drift", drift_rejected),
                            ("jitter", jitter_rejected),
                        )
                        if active
                    ),
                }
            )
        else:
            kept.append(registro)

    if not rejected:
        return registros, []

    if not _records_fit_ready(kept):
        print(
            f"Aviso: {regime} teve {len(rejected)} ponto(s) com drift alto, "
            "mas eles foram mantidos para preservar cobertura minima do ajuste."
        )
        return registros, []

    print(
        f"{regime}: descartando {len(rejected)} ponto(s) acima "
        "dos limites de estabilidade antes do fit."
    )
    for item in rejected:
        print(
            f"  -> {item['label']}: motivo={item['reason']}, "
            f"drift={item['center_drift_px']:.2f}px, jitter={item['jitter_px']:.2f}px"
        )
    return kept, rejected


def _select_records_for_fit_scale(registros: list[RegistroDual], regime: str) -> tuple[list[RegistroDual], list[dict]]:
    if regime != "fine" or not FINE_FIT_USE_SMALLEST_RADIUS or not registros:
        return registros, []

    smallest_radius = min(r.radius_deg for r in registros)
    selected = [
        r for r in registros
        if abs(r.radius_deg - smallest_radius) <= 1e-9
    ]
    if not _records_fit_ready(selected):
        print(
            "Aviso: pontos fine do menor raio nao cobrem o ajuste; "
            "mantendo todos os raios no fit."
        )
        return registros, []

    excluded = [
        {
            "label": r.label,
            "radius_deg": r.radius_deg,
            "selected_radius_deg": smallest_radius,
        }
        for r in registros
        if abs(r.radius_deg - smallest_radius) > 1e-9
    ]
    if excluded:
        print(
            f"fine: usando apenas raio {smallest_radius:.4f} deg no fit "
            f"({len(selected)}/{len(registros)} pontos)."
        )
    return selected, excluded


def _evaluate_matrix(A: np.ndarray, registros: list[RegistroDual]):
    offsets = np.array(
        [[r.target_az_deg, r.target_alt_deg] for r in registros],
        dtype=float,
    )
    x = np.array([r.corrected_x_px for r in registros], dtype=float)
    y = np.array([r.corrected_y_px for r in registros], dtype=float)

    pred_x = offsets @ A[0]
    pred_y = offsets @ A[1]
    residuo = np.sqrt((pred_x - x) ** 2 + (pred_y - y) ** 2)
    return {
        "rms_px": float(np.sqrt(np.mean(residuo ** 2))),
        "max_px": float(np.max(residuo)),
        "mean_px": float(np.mean(residuo)),
    }


def _fit_robusto_sem_intercepto(registros: list[RegistroDual], regime: str):
    if len(registros) < MIN_FIT_RECORDS:
        raise RuntimeError(
            f"Poucos pontos em {regime}: {len(registros)} "
            f"(minimo {MIN_FIT_RECORDS})."
        )

    offsets = np.array(
        [[r.target_az_deg, r.target_alt_deg] for r in registros],
        dtype=float,
    )
    x = np.array([r.corrected_x_px for r in registros], dtype=float)
    y = np.array([r.corrected_y_px for r in registros], dtype=float)
    jitter = np.array([max(r.jitter_px, 0.5) for r in registros], dtype=float)

    spread_az = float(np.ptp(offsets[:, 0]))
    spread_alt = float(np.ptp(offsets[:, 1]))
    if spread_az < MIN_SPREAD_DEG or spread_alt < MIN_SPREAD_DEG:
        raise RuntimeError(
            f"{regime}: pouca excitacao dos eixos (spread_az={spread_az:.4f}, "
            f"spread_alt={spread_alt:.4f})."
        )
    if np.linalg.matrix_rank(offsets) < 2:
        raise RuntimeError(f"{regime}: offsets degenerados.")

    base_weights = 1.0 / np.square(jitter)
    base_weights /= np.max(base_weights)
    robust_weights = np.ones(len(registros), dtype=float)

    coef_x = None
    coef_y = None
    for _ in range(ROBUST_ITERS):
        weights = np.clip(base_weights * robust_weights, 1e-4, 1.0)
        sqrt_w = np.sqrt(weights)
        M_w = offsets * sqrt_w[:, None]
        x_w = x * sqrt_w
        y_w = y * sqrt_w

        coef_x = np.linalg.lstsq(M_w, x_w, rcond=None)[0]
        coef_y = np.linalg.lstsq(M_w, y_w, rcond=None)[0]

        pred_x = offsets @ coef_x
        pred_y = offsets @ coef_y
        residuo = np.sqrt((pred_x - x) ** 2 + (pred_y - y) ** 2)
        mad = float(np.median(np.abs(residuo - np.median(residuo))))
        scale = max(1e-6, 1.4826 * mad)
        cutoff = HUBER_K * scale

        new_weights = np.ones_like(robust_weights)
        mask = residuo > cutoff
        new_weights[mask] = cutoff / np.maximum(residuo[mask], 1e-9)

        if np.allclose(new_weights, robust_weights, atol=1e-3, rtol=1e-2):
            robust_weights = new_weights
            break
        robust_weights = new_weights

    A = np.array(
        [
            [coef_x[0], coef_x[1]],
            [coef_y[0], coef_y[1]],
        ],
        dtype=float,
    )
    cond = float(np.linalg.cond(A))
    if not np.isfinite(cond) or cond > MAX_COND:
        raise RuntimeError(f"{regime}: matriz mal condicionada (cond={cond:.2e}).")

    A_inv = np.linalg.inv(A)
    fit_metrics = _evaluate_matrix(A, registros)
    downweighted = int(np.count_nonzero(robust_weights < 0.99))
    effective_weights = np.clip(base_weights * robust_weights, 1e-4, 1.0)

    limits = QUALITY_LIMITS[regime]
    quality_ok = fit_metrics["rms_px"] <= limits["max_rms_px"]
    quality_warning = None
    if not quality_ok:
        quality_warning = (
            f"{regime}: RMS alto ({fit_metrics['rms_px']:.2f}px) para o alvo local "
            f"de {limits['max_rms_px']:.2f}px."
        )
    elif fit_metrics["rms_px"] > limits["warn_rms_px"]:
        quality_warning = (
            f"{regime}: RMS moderado ({fit_metrics['rms_px']:.2f}px), ainda aceitavel."
        )

    return {
        "A": A,
        "A_inv": A_inv,
        "spread_az_deg": spread_az,
        "spread_alt_deg": spread_alt,
        "condition_number": cond,
        "rms_residual_px": fit_metrics["rms_px"],
        "max_residual_px": fit_metrics["max_px"],
        "mean_residual_px": fit_metrics["mean_px"],
        "num_points": len(registros),
        "downweighted_points": downweighted,
        "quality_ok": quality_ok,
        "quality_warning": quality_warning,
        "weights": effective_weights.tolist(),
    }


def _prepare_fit_records(registros: list[RegistroDual], regime: str):
    fit_records, rejected = _filter_records_for_fit(registros, regime)
    fit_records, scale_excluded = _select_records_for_fit_scale(fit_records, regime)
    return fit_records, rejected, scale_excluded


def _fit_regime_with_outlier_remeasure(
    regime: str,
    radii_deg: list[float],
    exposure: float,
    mount: bool,
) -> tuple[dict, list[RegistroDual]]:
    sequence = _build_star_sequence(radii_deg)
    sequence_by_label = {label: item for item in sequence for label in [item[0]]}
    registros = _collect_regime(
        regime=regime,
        radii_deg=radii_deg,
        exposure=exposure,
        mount=mount,
    )
    remeasure_log = []
    residual_limit = RESIDUAL_RETRY_LIMITS_PX[regime]

    for pass_idx in range(POST_FIT_REMEASURE_PASSES + 1):
        fit_records, rejected, scale_excluded = _prepare_fit_records(registros, regime)
        result = _fit_robusto_sem_intercepto(fit_records, regime=regime)
        residuals = _residuals_for_records(result["A"], fit_records)
        bad_residuals = [
            item for item in residuals
            if item["residual_px"] > residual_limit
        ]

        if not bad_residuals or pass_idx >= POST_FIT_REMEASURE_PASSES:
            result["fit_rejected_records"] = rejected
            result["fit_excluded_scale_records"] = scale_excluded
            result["num_collected_points"] = len(registros)
            result["post_fit_remeasure_log"] = remeasure_log
            result["post_fit_residuals"] = residuals
            return result, registros

        print(
            f"{regime}: {len(bad_residuals)} ponto(s) com residuo acima de "
            f"{residual_limit:.1f}px; recolhendo antes de refazer o fit."
        )
        for item in sorted(bad_residuals, key=lambda value: value["residual_px"], reverse=True):
            label = item["label"]
            if label not in sequence_by_label:
                continue
            _, radius_deg, target_az_deg, target_alt_deg = sequence_by_label[label]
            print(f"  -> refazendo {label}: residuo={item['residual_px']:.2f}px")
            novo = _collect_bracketed_sample(
                regime=regime,
                label=label,
                radius_deg=radius_deg,
                target_az_deg=target_az_deg,
                target_alt_deg=target_alt_deg,
                exposure=exposure,
                mount=mount,
                audit_label=f"{label}_refit{pass_idx + 1:02d}",
            )
            if novo is None:
                remeasure_log.append(
                    {
                        **item,
                        "refit_pass": pass_idx + 1,
                        "remeasured": False,
                    }
                )
                continue
            _replace_record(registros, novo)
            remeasure_log.append(
                {
                    **item,
                    "refit_pass": pass_idx + 1,
                    "remeasured": True,
                    "new_center_drift_px": novo.center_drift_px,
                    "new_jitter_px": novo.jitter_px,
                }
            )

    raise RuntimeError(f"{regime}: falha inesperada no refit com remedicao.")


def _load_existing_matrix():
    for meta_path in json_candidates("calibracao_meta.json"):
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return {
                "source": display_path(meta_path),
                "A": np.array(meta["A"], dtype=float),
                "meta": meta,
            }
    for matrix_path in matrix_candidates("calibracao_A.npy"):
        if matrix_path.exists():
            return {
                "source": display_path(matrix_path),
                "A": np.load(matrix_path),
                "meta": None,
            }
    return None


def _compare_with_existing(existing, coarse_result, fine_result, coarse_records, fine_records):
    if existing is None:
        return None

    A_old = existing["A"]
    comparison = {
        "source": existing["source"],
        "old_on_coarse": _evaluate_matrix(A_old, coarse_records),
        "old_on_fine": _evaluate_matrix(A_old, fine_records),
        "new_coarse_on_coarse": _evaluate_matrix(coarse_result["A"], coarse_records),
        "new_fine_on_fine": _evaluate_matrix(fine_result["A"], fine_records),
        "new_coarse_on_fine": _evaluate_matrix(coarse_result["A"], fine_records),
        "new_fine_on_coarse": _evaluate_matrix(fine_result["A"], coarse_records),
    }
    if existing["meta"] is not None:
        comparison["old_saved_rms_px"] = existing["meta"].get("rms_residual_px")
        comparison["old_saved_selection_mode"] = existing["meta"].get("selection_mode")
    return comparison


def _calibration_config_payload() -> dict:
    return {
        "calibration_profile": CALIBRATION_PROFILE,
        "measurement_mode": CALIBRATION_MEASUREMENT_MODE,
        "calibration_roi": CALIBRATION_ROI,
        "exposure_seconds": EXPOSURE_SECONDS,
        "camera_gain": CAMERA_GAIN,
        "camera_backend": backend_name(),
        "rotate_image_180": ROTATE_IMAGE_180,
        "matrix_prefix": IDS_MATRIX_PREFIX if backend_name() == "ids" else "foco_temp",
        "settle_s": SETTLE_S,
        "captures_per_center": CAPTURES_PER_CENTER,
        "captures_per_point": CAPTURES_PER_POINT,
        "max_sample_attempts": MAX_SAMPLE_ATTEMPTS,
        "min_fit_records": MIN_FIT_RECORDS,
        "post_fit_remeasure_passes": POST_FIT_REMEASURE_PASSES,
        "center_drift_weight": CENTER_DRIFT_WEIGHT,
        "fine_fit_use_smallest_radius": FINE_FIT_USE_SMALLEST_RADIUS,
        "drift_limits_px": DRIFT_LIMITS_PX,
        "jitter_limits_px": JITTER_LIMITS_PX,
        "residual_retry_limits_px": RESIDUAL_RETRY_LIMITS_PX,
        "coarse_radii_deg": COARSE_RADII_DEG,
        "fine_radii_deg": FINE_RADII_DEG,
        "directions": DIRECTIONS,
        "robust_iters": ROBUST_ITERS,
        "huber_k": HUBER_K,
        "focus_mode": get_focus_mode(),
        "focus_method": "temporary locked-focus center of mass",
        "focus_audit_dir": None if AUDIT_DIR is None else str(AUDIT_DIR.relative_to(ROOT_DIR)),
    }


def _matrix_paths_for_regime(regime: str, output_dir=None) -> tuple[Path, Path]:
    if regime == "coarse":
        return (
            matrix_output_path(COARSE_A_PATH, output_dir),
            matrix_output_path(COARSE_A_INV_PATH, output_dir),
        )
    if regime == "fine":
        return (
            matrix_output_path(FINE_A_PATH, output_dir),
            matrix_output_path(FINE_A_INV_PATH, output_dir),
        )
    raise ValueError(f"Regime invalido: {regime}")


def _save_regime_results(regime: str, result, records, output_dir=None) -> None:
    a_path, a_inv_path = _matrix_paths_for_regime(regime, output_dir)
    np.save(a_path, result["A"])
    np.save(a_inv_path, result["A_inv"])

    payload = {
        "timestamp_epoch": time.time(),
        "config": _calibration_config_payload(),
        "focus_audit": AUDIT_LOG,
        regime: {
            **{k: v for k, v in result.items() if k not in {"A", "A_inv", "weights"}},
            "A": result["A"].tolist(),
            "A_inv": result["A_inv"].tolist(),
            "records": [asdict(r) for r in records],
        },
    }
    meta_output_path = json_output_path(f"{OUTPUT_PREFIX}_{regime}_meta.json", output_dir)
    with meta_output_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


def _promote_roi_fine_to_tracker_coarse(result, output_dir=None) -> None:
    """Evita que o tracker misture a fine nova com uma coarse antiga na mesma ROI."""
    coarse_a_path, coarse_a_inv_path = _matrix_paths_for_regime("coarse", output_dir)
    np.save(coarse_a_path, result["A"])
    np.save(coarse_a_inv_path, result["A_inv"])
    payload = {
        "timestamp_epoch": time.time(),
        "reason": "fine ROI promovida para coarse dentro da janela do tracker",
        "config": _calibration_config_payload(),
        "A": result["A"].tolist(),
        "A_inv": result["A_inv"].tolist(),
    }
    path = json_output_path(f"{OUTPUT_PREFIX}_roi_tracker_meta.json", output_dir)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
    print(
        "A mesma matriz ROI foi salva como fine e coarse para o tracker nao "
        "misturar esta calibracao local com uma coarse antiga."
    )


def _save_dual_results(coarse_result, fine_result, coarse_records, fine_records, comparison, output_dir=None):
    coarse_a_path = matrix_output_path(COARSE_A_PATH, output_dir)
    coarse_a_inv_path = matrix_output_path(COARSE_A_INV_PATH, output_dir)
    fine_a_path = matrix_output_path(FINE_A_PATH, output_dir)
    fine_a_inv_path = matrix_output_path(FINE_A_INV_PATH, output_dir)

    np.save(coarse_a_path, coarse_result["A"])
    np.save(coarse_a_inv_path, coarse_result["A_inv"])
    np.save(fine_a_path, fine_result["A"])
    np.save(fine_a_inv_path, fine_result["A_inv"])

    payload = {
        "timestamp_epoch": time.time(),
        "config": _calibration_config_payload(),
        "focus_audit": AUDIT_LOG,
        "coarse": {
            **{k: v for k, v in coarse_result.items() if k not in {"A", "A_inv", "weights"}},
            "A": coarse_result["A"].tolist(),
            "A_inv": coarse_result["A_inv"].tolist(),
            "records": [asdict(r) for r in coarse_records],
        },
        "fine": {
            **{k: v for k, v in fine_result.items() if k not in {"A", "A_inv", "weights"}},
            "A": fine_result["A"].tolist(),
            "A_inv": fine_result["A_inv"].tolist(),
            "records": [asdict(r) for r in fine_records],
        },
        "comparison_with_existing": comparison,
    }

    meta_output_path = json_output_path(f"{OUTPUT_PREFIX}_meta.json", output_dir)
    with meta_output_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


def _print_summary(name: str, result):
    quality = "OK" if result["quality_ok"] else "RESSALVAS"
    collected = result.get("num_collected_points", result["num_points"])
    print(
        f"{name}: pontos_fit={result['num_points']}/{collected} | "
        f"RMS={result['rms_residual_px']:.2f}px | "
        f"max={result['max_residual_px']:.2f}px | "
        f"cond={result['condition_number']:.2e} | "
        f"pesos_rebaixados={result['downweighted_points']} | "
        f"{quality}"
    )
    rejected = result.get("fit_rejected_records", [])
    if rejected:
        labels = ", ".join(item["label"] for item in rejected)
        print(f"  -> descartados por drift alto no fit: {labels}")
    scale_excluded = result.get("fit_excluded_scale_records", [])
    if scale_excluded:
        selected_radius = scale_excluded[0]["selected_radius_deg"]
        print(
            f"  -> fit priorizou raio {selected_radius:.4f} deg; "
            f"{len(scale_excluded)} ponto(s) de outro raio ficaram fora do fit."
        )
    if result["quality_warning"]:
        print(f"  -> {result['quality_warning']}")


def _novo_diretorio_auditoria() -> Path:
    foco_dir = FOCO_DIR.resolve()
    base_dir = (foco_dir / "auditoria_foco_temp").resolve()
    if base_dir.parent != foco_dir or base_dir.name != "auditoria_foco_temp":
        raise RuntimeError(f"Diretorio de auditoria inseguro: {base_dir}")

    base_dir.mkdir(parents=True, exist_ok=True)
    for old_entry in base_dir.iterdir():
        if old_entry.is_symlink() or old_entry.is_file():
            old_entry.unlink()
        elif old_entry.is_dir():
            shutil.rmtree(old_entry)
        else:
            raise RuntimeError(f"Entrada desconhecida na auditoria: {old_entry}")

    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    audit_dir = base_dir / f"calibracao_{timestamp}"
    audit_dir.mkdir(exist_ok=False)
    return audit_dir


def _write_mount_position_record(record: dict) -> Path:
    if AUDIT_DIR is None:
        raise RuntimeError("Diretorio de auditoria ainda nao foi criado.")
    path = AUDIT_DIR / "posicao_inicial_mount.json"
    with path.open("w", encoding="utf-8") as fp:
        json.dump(record, fp, indent=2)
    return path


def _position_error(
    target_az_deg: float,
    target_alt_deg: float,
    current_az_deg: float,
    current_alt_deg: float,
) -> tuple[float, float]:
    return (
        float(calc_error(0, target_az_deg, current_az_deg)),
        float(target_alt_deg - current_alt_deg),
    )


def _return_to_initial_position(
    mount: bool,
    initial_az_deg: float,
    initial_alt_deg: float,
) -> dict:
    result = {
        "attempted_epoch": time.time(),
        "success": False,
        "status": "not_started",
        "attempts": 0,
        "final_azimuth_deg": None,
        "final_altitude_deg": None,
        "final_error_azimuth_deg": None,
        "final_error_altitude_deg": None,
    }

    print(
        "\nRetornando o mount para a posicao absoluta inicial: "
        f"Az={initial_az_deg:.6f} deg, Alt={initial_alt_deg:.6f} deg"
    )

    try:
        if not stop_axes_safely():
            raise RuntimeError("nao foi possivel confirmar a parada antes do retorno")

        for attempt in range(1, MAX_AUTO_RETURN_ATTEMPTS + 1):
            current_az, current_alt = read_altaz()
            delta_az, delta_alt = _position_error(
                initial_az_deg,
                initial_alt_deg,
                current_az,
                current_alt,
            )
            result["attempts"] = attempt - 1

            if max(abs(delta_az), abs(delta_alt)) <= RETURN_POSITION_TOLERANCE_DEG:
                result["success"] = True
                result["status"] = "restored"
                break

            if max(abs(delta_az), abs(delta_alt)) > MAX_AUTO_RETURN_DELTA_DEG:
                result["status"] = "unsafe_delta_refused"
                print(
                    "ALERTA: retorno automatico recusado porque a distancia ate a "
                    f"origem ficou grande demais (dAz={delta_az:+.6f} deg, "
                    f"dAlt={delta_alt:+.6f} deg; limite="
                    f"{MAX_AUTO_RETURN_DELTA_DEG:.3f} deg)."
                )
                break

            print(
                f"Tentativa de retorno {attempt}/{MAX_AUTO_RETURN_ATTEMPTS}: "
                f"dAz={delta_az:+.6f} deg, dAlt={delta_alt:+.6f} deg"
            )
            result["attempts"] = attempt
            move_axes_pid_2d(mount, delta_az, delta_alt)

        final_az, final_alt = read_altaz()
        final_error_az, final_error_alt = _position_error(
            initial_az_deg,
            initial_alt_deg,
            final_az,
            final_alt,
        )
        result.update(
            {
                "final_azimuth_deg": final_az,
                "final_altitude_deg": final_alt,
                "final_error_azimuth_deg": final_error_az,
                "final_error_altitude_deg": final_error_alt,
            }
        )

        if max(abs(final_error_az), abs(final_error_alt)) <= RETURN_POSITION_TOLERANCE_DEG:
            result["success"] = True
            result["status"] = "restored"
            print(
                "Posicao inicial restaurada: "
                f"Az={final_az:.6f} deg, Alt={final_alt:.6f} deg."
            )
        elif result["status"] != "unsafe_delta_refused":
            result["status"] = "tolerance_not_reached"
            print(
                "ALERTA: o mount parou, mas nao confirmou retorno dentro da tolerancia "
                f"(erro Az={final_error_az:+.6f} deg, "
                f"Alt={final_error_alt:+.6f} deg)."
            )
    except KeyboardInterrupt:
        result["status"] = "return_interrupted_by_user"
        print("\nRetorno a posicao inicial interrompido; parando os eixos.")
    except Exception as exc:
        result["status"] = "return_failed"
        result["error"] = str(exc)
        print(f"ALERTA: nao consegui retornar a posicao absoluta inicial: {exc}")
    finally:
        stop_axes_safely()

    return result


def main():
    global AUDIT_DIR, AUDIT_LOG
    global CALIBRATION_ROI, CALIBRATION_MEASUREMENT_MODE, MANUAL_TARGET

    CALIBRATION_ROI = None
    CALIBRATION_MEASUREMENT_MODE = "sensor_completo"
    MANUAL_TARGET = None

    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()

    focus_input = input("Modo do laser (1=foco unico, 2=dupla reflexao) [2]: ").strip() or "2"
    focus_mode = set_focus_mode(focus_input)
    target_input = (
        input("O que calibrar? (coarse/fine/ambos) [ambos]: ").strip().lower()
        or "ambos"
    )
    if target_input in {"both", "ambas", "todos", "tudo"}:
        target_input = "ambos"
    if target_input not in {"coarse", "fine", "ambos"}:
        raise ValueError("Escolha coarse, fine ou ambos.")

    long_link_input = input(
        "Calibracao no link longo UFF-CBPF (~7 km)? (s/N): "
    ).strip().lower()
    if long_link_input not in {"", "s", "sim", "n", "nao", "não"}:
        raise ValueError("Responda s ou n para o perfil de link longo.")
    _configure_calibration_profile(long_link_input in {"s", "sim"})

    measurement_input = (
        input(
            "Medicao da calibracao (1=escolher ilha + ROI do tracker, "
            "2=sensor completo) [1]: "
        ).strip()
        or "1"
    )
    if measurement_input not in {"1", "2"}:
        raise ValueError("Escolha 1 para ROI manual ou 2 para sensor completo.")
    use_manual_roi = measurement_input == "1"
    if use_manual_roi and focus_mode != "dual":
        print(
            "A selecao manual de ilha usa o detector de componentes isolados; "
            "o modo de foco foi alterado para dupla reflexao/ilhas."
        )
        focus_mode = set_focus_mode("dual")
    if use_manual_roi and target_input != "fine":
        print(
            "O modo ROI do tracker calibra somente a matriz fine; "
            f"a escolha '{target_input}' foi alterada para 'fine'."
        )
        target_input = "fine"

    mount = True
    AUDIT_LOG = []
    AUDIT_DIR = _novo_diretorio_auditoria()
    calibration_started_epoch = time.time()
    calibration_started = time.perf_counter()
    get_asi_performance = None
    print_asi_performance = None
    if backend_name() == "alpaca":
        from controle.camera_asi_fast import (
            get_performance_stats,
            print_performance_summary,
            reset_performance_stats,
        )

        reset_performance_stats()
        get_asi_performance = get_performance_stats
        print_asi_performance = print_performance_summary
    initial_position = None
    position_record = None

    print(f"Modo de foco temporario: {focus_mode}")
    print(f"Regime de calibracao: {target_input}")
    print(f"Medicao: {'ilha manual + ROI do tracker' if use_manual_roi else 'sensor completo'}")
    if use_manual_roi:
        print(f"Perfil base: {CALIBRATION_PROFILE}; parametros finais apos escolher a ilha.")
    else:
        print(
            f"Perfil: {CALIBRATION_PROFILE} | "
            f"frames por medicao={CAPTURES_PER_POINT} | "
            f"tentativas={MAX_SAMPLE_ATTEMPTS} | "
            f"raios fine={FINE_RADII_DEG} deg"
        )
    if CALIBRATION_PROFILE == "link_longo_7km":
        print(
            "Limites link longo (coarse/fine): "
            f"drift aceitavel={DRIFT_LIMITS_PX['coarse']['accept']:.0f}/"
            f"{DRIFT_LIMITS_PX['fine']['accept']:.0f}px, "
            f"jitter aceitavel={JITTER_LIMITS_PX['coarse']['accept']:.0f}/"
            f"{JITTER_LIMITS_PX['fine']['accept']:.0f}px."
        )
    print(
        f"Camera {backend_name()}: ganho={CAMERA_GAIN}, "
        f"exposicao={EXPOSURE_SECONDS * 1e6:.1f} us"
    )
    if backend_name() == "ids":
        print(f"Imagem IDS rotacionada 180 graus: {'sim' if ROTATE_IMAGE_180 else 'nao'}")
    print("Usando montagem real. Esta versao temporaria nao pergunta por simulador.")
    print(f"Auditoria visual do foco em: {AUDIT_DIR}")

    try:
        initial_az, initial_alt = read_altaz()
        initial_position = (initial_az, initial_alt)
        position_record = {
            "captured_epoch": time.time(),
            "initial_azimuth_deg": initial_az,
            "initial_altitude_deg": initial_alt,
            "return_tolerance_deg": RETURN_POSITION_TOLERANCE_DEG,
            "max_auto_return_delta_deg": MAX_AUTO_RETURN_DELTA_DEG,
            "return": {"status": "pending"},
        }
        position_path = _write_mount_position_record(position_record)
        print(
            "Posicao absoluta inicial salva: "
            f"Az={initial_az:.6f} deg, Alt={initial_alt:.6f} deg "
            f"em {position_path}"
        )

        connect_camera()
        set_gain(CAMERA_GAIN)

        if use_manual_roi:
            _prepare_manual_tracker_roi(focus_mode)
            print(
                f"Calibracao ROI pronta: perfil={CALIBRATION_PROFILE}, "
                f"frames={CAPTURES_PER_POINT}, tentativas={MAX_SAMPLE_ATTEMPTS}, "
                f"raios fine={FINE_RADII_DEG} deg."
            )

        coarse_result = None
        coarse_records = None
        fine_result = None
        fine_records = None

        if target_input in {"coarse", "ambos"}:
            coarse_result, coarse_records = _fit_regime_with_outlier_remeasure(
                regime="coarse",
                radii_deg=COARSE_RADII_DEG,
                exposure=EXPOSURE_SECONDS,
                mount=mount,
            )
        if target_input in {"fine", "ambos"}:
            fine_result, fine_records = _fit_regime_with_outlier_remeasure(
                regime="fine",
                radii_deg=FINE_RADII_DEG,
                exposure=EXPOSURE_SECONDS,
                mount=mount,
            )

        if target_input == "coarse":
            _save_regime_results("coarse", coarse_result, coarse_records)
            print("\n=== Resumo Nova Calibracao COARSE ===")
            _print_summary("COARSE", coarse_result)
            print(
                f"\nArquivos salvos: "
                f"{display_path(matrix_output_path(COARSE_A_PATH))}, "
                f"{display_path(matrix_output_path(COARSE_A_INV_PATH))}, "
                f"{display_path(json_output_path(f'{OUTPUT_PREFIX}_coarse_meta.json'))}"
            )
            return

        if target_input == "fine":
            _save_regime_results("fine", fine_result, fine_records)
            if CALIBRATION_ROI is not None:
                _promote_roi_fine_to_tracker_coarse(fine_result)
            print("\n=== Resumo Nova Calibracao FINE ===")
            _print_summary("FINE", fine_result)
            print(
                f"\nArquivos salvos: "
                f"{display_path(matrix_output_path(FINE_A_PATH))}, "
                f"{display_path(matrix_output_path(FINE_A_INV_PATH))}, "
                f"{display_path(json_output_path(f'{OUTPUT_PREFIX}_fine_meta.json'))}"
            )
            if CALIBRATION_ROI is not None:
                print(
                    "Tracker pronto para usar esta ROI: "
                    f"{display_path(matrix_output_path(COARSE_A_PATH))}, "
                    f"{display_path(matrix_output_path(COARSE_A_INV_PATH))}"
                )
            return

        existing = _load_existing_matrix()
        coarse_fit_records, _, _ = _prepare_fit_records(coarse_records, "coarse")
        fine_fit_records, _, _ = _prepare_fit_records(fine_records, "fine")
        comparison = _compare_with_existing(
            existing,
            coarse_result,
            fine_result,
            coarse_fit_records,
            fine_fit_records,
        )
        _save_dual_results(
            coarse_result,
            fine_result,
            coarse_records,
            fine_records,
            comparison,
        )

        print("\n=== Resumo Nova Calibracao Dual V3 ===")
        _print_summary("COARSE", coarse_result)
        _print_summary("FINE", fine_result)
        if comparison is not None:
            print("\n=== Comparacao com calibracao atual ===")
            print(
                f"Atual no dataset COARSE: RMS={comparison['old_on_coarse']['rms_px']:.2f}px | "
                f"max={comparison['old_on_coarse']['max_px']:.2f}px"
            )
            print(
                f"Atual no dataset FINE: RMS={comparison['old_on_fine']['rms_px']:.2f}px | "
                f"max={comparison['old_on_fine']['max_px']:.2f}px"
            )
            print(
                f"Nova COARSE no dataset COARSE: RMS={comparison['new_coarse_on_coarse']['rms_px']:.2f}px | "
                f"max={comparison['new_coarse_on_coarse']['max_px']:.2f}px"
            )
            print(
                f"Nova FINE no dataset FINE: RMS={comparison['new_fine_on_fine']['rms_px']:.2f}px | "
                f"max={comparison['new_fine_on_fine']['max_px']:.2f}px"
            )

        print(
            f"\nArquivos salvos: "
            f"{display_path(matrix_output_path(COARSE_A_PATH))}, "
            f"{display_path(matrix_output_path(COARSE_A_INV_PATH))}, "
            f"{display_path(matrix_output_path(FINE_A_PATH))}, "
            f"{display_path(matrix_output_path(FINE_A_INV_PATH))}, "
            f"{display_path(json_output_path(f'{OUTPUT_PREFIX}_meta.json'))}"
        )

    except KeyboardInterrupt:
        print("\nCalibracao dual interrompida pelo usuario.")
    except Exception as exc:
        print(f"\nErro na calibracao dual V3: {exc}")
    finally:
        if initial_position is None:
            stop_axes_safely()
        else:
            return_result = _return_to_initial_position(
                mount,
                initial_position[0],
                initial_position[1],
            )
            if position_record is not None:
                position_record["return"] = return_result
                try:
                    _write_mount_position_record(position_record)
                except Exception as exc:
                    print(f"Aviso: nao consegui atualizar o registro da posicao: {exc}")
        if DISCONNECT_CAMERA_ON_EXIT:
            try:
                disconnect_camera()
            except Exception as exc:
                print(f"Aviso: nao consegui desconectar a camera pelo Alpaca: {exc}")
        else:
            print("Camera mantida conectada ao final da calibracao.")
        if print_asi_performance is not None:
            print_asi_performance()
        total_seconds = time.perf_counter() - calibration_started
        print(f"Tempo total da calibracao: {total_seconds:.1f} s.")
        try:
            execution_summary = {
                "started_epoch": calibration_started_epoch,
                "finished_epoch": time.time(),
                "total_seconds": total_seconds,
                "camera_backend": backend_name(),
                "calibration_profile": CALIBRATION_PROFILE,
                "measurement_mode": CALIBRATION_MEASUREMENT_MODE,
                "calibration_roi": CALIBRATION_ROI,
                "gain": CAMERA_GAIN,
                "exposure_seconds": EXPOSURE_SECONDS,
                "asi_performance": (
                    get_asi_performance() if get_asi_performance is not None else None
                ),
            }
            with (AUDIT_DIR / "resumo_execucao.json").open("w", encoding="utf-8") as fp:
                json.dump(execution_summary, fp, indent=2)
        except Exception as exc:
            print(f"Aviso: nao consegui salvar o resumo de desempenho: {exc}")


if __name__ == "__main__":
    main()
