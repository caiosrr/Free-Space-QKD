"""Calibracao angular-pixel por varredura continua e validacao independente."""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from modulos.artefatos import display_path
from modulos.configuracoes.tracker import TRACKER_MAX_SPOT_JUMP_PX, roi_size_for_backend
from modulos.controle.alvo_alinhamento import TARGET_FILENAME, roi_incluindo_alvo, salvar_alvo
from modulos.controle.cameras.backend import backend_name, connect_camera, direct_camera, disconnect_camera, set_gain
from modulos.controle.mount_control import (
    TOLERANCIA_GRAUS, calc_error, ensure_connected, ensure_not_tracking,
    ensure_unparked, move_axes_pid_2d, move_axis, read_altaz, stop_axes_safely,
)
from modulos.controle.mapa_jacobianas import registrar_no
from modulos.visao import detector_ilhas as foco


LOCAL_HALF_RANGE_DEG = 0.008
WIDE_HALF_RANGE_DEG = 0.014
SWEEP_RATE_DEG_S = 0.002
SWEEP_HALF_RANGE_DEG = LOCAL_HALF_RANGE_DEG
OTHER_AXIS_LIMIT_DEG = 0.004
RETURN_MAX_RATE_DEG_S = 0.02
RETURN_ATTEMPTS = 2
BASELINE_VALID_FRAMES = 10
SIGNAL_LOSS_TIMEOUT_S = 1.5
MIN_VALID_SWEEP_SAMPLES = 20
HUBER_K = 1.5
ROBUST_ITERS = 10


@dataclass(frozen=True)
class SweepSpec:
    name: str
    axis: int
    command_sign: int
    half_range_deg: float
    role: str


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    description: str
    specs: tuple[SweepSpec, ...]
    requires_holdout: bool


@dataclass
class SweepSample:
    run: str
    axis: int
    command_sign: int
    elapsed_s: float
    az_deg: float
    alt_deg: float
    delta_az_deg: float
    delta_alt_deg: float
    x_px: float
    y_px: float
    role: str = "fit"
    half_range_deg: float = LOCAL_HALF_RANGE_DEG
    capture_duration_s: float = 0.0
    capture_mid_epoch: float = 0.0


def _four_sweeps(role: str, amplitude: float, order: tuple[tuple[int, int], ...]):
    labels = {0: "az", 1: "alt"}
    return tuple(
        SweepSpec(
            f"{role}_{labels[axis]}_{'pos' if sign > 0 else 'neg'}",
            axis, sign, amplitude, role,
        )
        for axis, sign in order
    )


def calibration_profile(name: str) -> CalibrationProfile:
    normalized = str(name).strip().lower()
    if normalized in {"rapido", "quick"}:
        return CalibrationProfile(
            "rapido",
            "4 varreduras; ajuste e validacao interna em cerca de 30-60 s",
            _four_sweeps("fit", LOCAL_HALF_RANGE_DEG, ((0, +1), (1, -1), (0, -1), (1, +1))),
            False,
        )
    if normalized not in {"robusto", "robust", ""}:
        raise ValueError("Perfil invalido. Use 'rapido' ou 'robusto'.")
    fit = _four_sweeps("fit", LOCAL_HALF_RANGE_DEG, ((0, +1), (1, -1), (0, -1), (1, +1)))
    holdout = _four_sweeps("holdout_local", LOCAL_HALF_RANGE_DEG, ((1, +1), (0, -1), (1, -1), (0, +1)))
    wide = _four_sweeps("holdout_amplo", WIDE_HALF_RANGE_DEG, ((0, -1), (1, +1), (0, +1), (1, -1)))
    return CalibrationProfile(
        "robusto",
        "4 ajustes + 4 validacoes locais + 4 testes amplos; cerca de 1-3 min",
        fit + holdout + wide,
        True,
    )


def _offsets_from_start(initial_az: float, initial_alt: float, az: float, alt: float):
    return float(calc_error(0, az, initial_az)), float(alt - initial_alt)


def _return_to_absolute_start(initial_az: float, initial_alt: float) -> dict:
    result = {"success": False, "attempts": 0, "error": None}
    stop_axes_safely()
    try:
        for attempt in range(1, RETURN_ATTEMPTS + 1):
            az, alt = read_altaz()
            delta_az = float(calc_error(0, initial_az, az))
            delta_alt = float(initial_alt - alt)
            result["attempts"] = attempt
            if max(abs(delta_az), abs(delta_alt)) <= TOLERANCIA_GRAUS:
                break
            if max(abs(delta_az), abs(delta_alt)) > 0.05:
                raise RuntimeError("Retorno automatico recusado: deslocamento maior que 0.05 deg.")
            print(f"Retorno {attempt}/{RETURN_ATTEMPTS}: dAz={delta_az:+.5f} dAlt={delta_alt:+.5f} deg")
            move_axes_pid_2d(True, delta_az, delta_alt, max_velocity_deg_s=RETURN_MAX_RATE_DEG_S)
        final_az, final_alt = read_altaz()
        error_az = float(calc_error(0, initial_az, final_az))
        error_alt = float(initial_alt - final_alt)
        result.update(
            final_az_deg=final_az, final_alt_deg=final_alt,
            error_az_deg=error_az, error_alt_deg=error_alt,
            success=max(abs(error_az), abs(error_alt)) <= TOLERANCIA_GRAUS,
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        stop_axes_safely()
    return result


def _capture_valid_cm():
    frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
    return frame, foco.centro_massa(frame)


def _baseline_anchor(signature: dict, expected_x: float, expected_y: float):
    if not foco.initialize_focus_lock(
        signature, expected_x, expected_y, freeze_reference=True,
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    ):
        raise RuntimeError("Nao consegui inicializar a assinatura da luz.")
    xs, ys = [], []
    deadline = time.perf_counter() + 5.0
    while len(xs) < BASELINE_VALID_FRAMES and time.perf_counter() < deadline:
        _, cm = _capture_valid_cm()
        if cm is not None and not cm[3]:
            xs.append(float(cm[0]))
            ys.append(float(cm[1]))
    if len(xs) < BASELINE_VALID_FRAMES:
        raise RuntimeError(f"Baseline insuficiente: {len(xs)}/{BASELINE_VALID_FRAMES} frames validos.")
    anchor = float(np.median(xs)), float(np.median(ys))
    foco.set_focus_expected_position(*anchor, max_jump_px=TRACKER_MAX_SPOT_JUMP_PX)
    return anchor


def _run_one_sweep(*, spec: SweepSpec, initial_az: float, initial_alt: float, signature: dict, center_anchor):
    center_anchor = _baseline_anchor(signature, *center_anchor)
    samples: list[SweepSample] = []
    started = time.perf_counter()
    started_epoch = time.time()
    last_valid, last_print = started, 0.0
    timeout_s = spec.half_range_deg / SWEEP_RATE_DEG_S * 3.0 + 4.0
    hard_limit = spec.half_range_deg + max(0.004, 2.0 * TOLERANCIA_GRAUS)
    print(
        f"\n{spec.name}: eixo={'Az' if spec.axis == 0 else 'Alt'} comando={spec.command_sign:+d} | "
        f"amplitude={spec.half_range_deg:.4f} deg | velocidade={SWEEP_RATE_DEG_S:.4f} deg/s"
    )
    move_axis(spec.axis, spec.command_sign * SWEEP_RATE_DEG_S, True)
    try:
        while True:
            loop_t = time.perf_counter()
            if loop_t - started > timeout_s:
                raise RuntimeError(f"Tempo limite na varredura {spec.name}.")
            az_before, alt_before = read_altaz()
            capture_started = time.perf_counter()
            _, cm = _capture_valid_cm()
            capture_finished = time.perf_counter()
            az_after, alt_after = read_altaz()
            before = _offsets_from_start(initial_az, initial_alt, az_before, alt_before)
            after = _offsets_from_start(initial_az, initial_alt, az_after, alt_after)
            daz, dalt = 0.5 * (before[0] + after[0]), 0.5 * (before[1] + after[1])
            active = daz if spec.axis == 0 else dalt
            other = dalt if spec.axis == 0 else daz
            if abs(active) > hard_limit:
                raise RuntimeError(f"Watchdog: {spec.name} excedeu {hard_limit:.3f} deg.")
            if abs(other) > OTHER_AXIS_LIMIT_DEG:
                raise RuntimeError(f"Watchdog: outro eixo derivou {other:+.4f} deg.")
            if cm is not None and not cm[3]:
                last_valid = time.perf_counter()
                capture_mid = 0.5 * (capture_started + capture_finished)
                samples.append(SweepSample(
                    spec.name, spec.axis, spec.command_sign, capture_mid - started,
                    0.5 * (az_before + az_after), 0.5 * (alt_before + alt_after),
                    daz, dalt, float(cm[0]), float(cm[1]), spec.role,
                    spec.half_range_deg, capture_finished - capture_started,
                    started_epoch + capture_mid - started,
                ))
            elif cm is not None and cm[3]:
                raise RuntimeError(f"A luz tocou a borda durante {spec.name}.")
            if time.perf_counter() - last_valid > SIGNAL_LOSS_TIMEOUT_S:
                raise RuntimeError(f"Luz perdida por mais de {SIGNAL_LOSS_TIMEOUT_S:.1f}s.")
            if loop_t - last_print >= 0.5:
                cm_text = "sem sinal" if cm is None else f"CM=({cm[0]:.1f},{cm[1]:.1f})"
                print(f"  offset={active:+.5f} deg | {cm_text} | validos={len(samples)}")
                last_print = loop_t
            if abs(active) >= spec.half_range_deg:
                break
    finally:
        stop_axes_safely()
    minimum = max(MIN_VALID_SWEEP_SAMPLES, int(spec.half_range_deg / SWEEP_RATE_DEG_S * 5))
    if len(samples) < minimum:
        raise RuntimeError(f"{spec.name}: somente {len(samples)} amostras; minimo={minimum}.")
    values = np.array([s.delta_az_deg if spec.axis == 0 else s.delta_alt_deg for s in samples])
    span = float(np.ptp(values))
    if span < max(0.003, 0.65 * spec.half_range_deg):
        raise RuntimeError(f"{spec.name}: amplitude medida insuficiente ({span:.5f} deg).")
    print(f"  concluida: {len(samples)} amostras, amplitude medida={span:.5f} deg.")
    return samples, center_anchor


def _center_runs(runs: list[list[SweepSample]]):
    design_parts, pixel_parts = [], []
    for samples in runs:
        if not samples:
            continue
        design = np.array([[s.delta_az_deg, s.delta_alt_deg] for s in samples], dtype=float)
        pixels = np.array([[s.x_px, s.y_px] for s in samples], dtype=float)
        design_parts.append(design - np.median(design, axis=0))
        pixel_parts.append(pixels - np.median(pixels, axis=0))
    if not design_parts:
        raise RuntimeError("Nenhuma varredura valida para o ajuste.")
    return np.vstack(design_parts), np.vstack(pixel_parts)


def _robust_fit(design: np.ndarray, pixels: np.ndarray) -> dict:
    if design.ndim != 2 or design.shape[1] != 2 or pixels.shape != design.shape:
        raise ValueError("Dados do ajuste precisam ter formato Nx2.")
    weights = np.ones(design.shape[0])
    beta = np.linalg.lstsq(design, pixels, rcond=None)[0]
    for _ in range(ROBUST_ITERS):
        root_w = np.sqrt(np.clip(weights, 1e-6, None))[:, None]
        beta = np.linalg.lstsq(design * root_w, pixels * root_w, rcond=None)[0]
        residual = np.linalg.norm(pixels - design @ beta, axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        cutoff = HUBER_K * scale
        weights = np.where(residual <= cutoff, 1.0, cutoff / np.maximum(residual, 1e-9))
    residual = np.linalg.norm(pixels - design @ beta, axis=1)
    A = beta.T
    condition = float(np.linalg.cond(A))
    if not np.all(np.isfinite(A)) or condition > 100.0:
        raise RuntimeError(f"Matriz continua mal condicionada: cond={condition:.2f}.")
    return {
        "A": A, "A_inv": np.linalg.inv(A), "weights": weights,
        "rms_residual_px": float(np.sqrt(np.mean(residual**2))),
        "median_residual_px": float(np.median(residual)),
        "max_residual_px": float(np.max(residual)), "condition_number": condition,
    }


def _direction_slope(samples: list[SweepSample], axis: int) -> np.ndarray:
    d = np.array([s.delta_az_deg if axis == 0 else s.delta_alt_deg for s in samples])
    p = np.array([[s.x_px, s.y_px] for s in samples])
    d, p = d - np.median(d), p - np.median(p, axis=0)
    denom = float(d @ d)
    if denom <= 1e-10:
        raise RuntimeError("Trajetoria sem variacao angular suficiente.")
    slope = (d[:, None] * p).sum(axis=0) / denom
    for _ in range(6):
        residual = np.linalg.norm(p - d[:, None] * slope, axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        cutoff = HUBER_K * scale
        weights = np.where(residual <= cutoff, 1.0, cutoff / np.maximum(residual, 1e-9))
        slope = np.sum((weights * d)[:, None] * p, axis=0) / float(np.sum(weights * d * d))
    return slope


def _validate_fit(runs: list[list[SweepSample]], fit: dict, half_range_deg=LOCAL_HALF_RANGE_DEG) -> dict:
    checks, failures = {}, []
    for axis, label in ((0, "az"), (1, "alt")):
        axis_runs = [r for r in runs if r and r[0].axis == axis]
        if len(axis_runs) != 2:
            failures.append(f"{label}: faltam duas direcoes")
            continue
        vectors = [_direction_slope(r, axis) for r in axis_runs]
        norms = [float(np.linalg.norm(v)) for v in vectors]
        cosine = float(np.dot(*vectors) / max(norms[0] * norms[1], 1e-12))
        ratio = max(norms) / max(min(norms), 1e-12)
        checks[label] = {"slopes_px_per_deg": [v.tolist() for v in vectors], "cosine": cosine, "magnitude_ratio": ratio}
        if cosine < 0.55:
            failures.append(f"{label}: ida/volta discordam (cosseno={cosine:.2f})")
        if ratio > 3.0:
            failures.append(f"{label}: escalas ida/volta diferem {ratio:.1f}x")
    response = {}
    rms = float(fit["rms_residual_px"])
    for axis, label in ((0, "az"), (1, "alt")):
        response[label] = float(np.linalg.norm(fit["A"][:, axis]) * half_range_deg)
        if response[label] < max(8.0, 1.5 * rms):
            failures.append(f"{label}: resposta {response[label]:.1f}px insuficiente para residuo {rms:.1f}px")
    return {"ok": not failures, "failures": failures, "direction_checks": checks,
            "predicted_response_at_half_range_px": response, "half_range_deg": half_range_deg}


def _validate_holdout(runs: list[list[SweepSample]], fit: dict, *, label: str) -> dict:
    if len(runs) != 4:
        return {"ok": False, "failures": [f"{label}: esperava 4 varreduras, recebeu {len(runs)}"], "run_count": len(runs)}
    design, pixels = _center_runs(runs)
    residual = np.linalg.norm(pixels - design @ fit["A"].T, axis=1)
    rms = float(np.sqrt(np.mean(residual**2)))
    failures, directions = [], {}
    for axis in (0, 1):
        reference = fit["A"][:, axis]
        for samples in [r for r in runs if r[0].axis == axis]:
            slope = _direction_slope(samples, axis)
            nr, ns = float(np.linalg.norm(reference)), float(np.linalg.norm(slope))
            cosine = float(np.dot(reference, slope) / max(nr * ns, 1e-12))
            ratio = max(nr, ns) / max(min(nr, ns), 1e-12)
            directions[samples[0].run] = {"cosine_with_fit": cosine, "magnitude_ratio_with_fit": ratio}
            if cosine < 0.75:
                failures.append(f"{samples[0].run}: direcao diverge (cosseno={cosine:.2f})")
            if ratio > 2.5:
                failures.append(f"{samples[0].run}: escala diverge ({ratio:.1f}x)")
    predicted = float(np.median(np.linalg.norm(design @ fit["A"].T, axis=1)))
    relative = rms / max(predicted, 1e-9)
    if rms > max(10.0, 3.0 * float(fit["rms_residual_px"])) and relative > 0.25:
        failures.append(f"{label}: residuo alto ({rms:.1f}px; relativo={relative:.2f})")
    return {"ok": not failures, "failures": failures, "run_count": len(runs),
            "sample_count": sum(map(len, runs)), "rms_residual_px": rms,
            "median_residual_px": float(np.median(residual)), "max_residual_px": float(np.max(residual)),
            "relative_rms": relative, "directions": directions}


def _write_csv(path: Path, runs: list[list[SweepSample]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(SweepSample.__dataclass_fields__))
        writer.writeheader()
        for samples in runs:
            writer.writerows(asdict(sample) for sample in samples)


def _backup_active_matrices(matrix_dir: Path, backup_dir: Path, prefix: str) -> list[str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for regime in ("fine", "coarse"):
        for kind in ("A", "A_inv"):
            source = matrix_dir / f"{prefix}_{kind}_{regime}.npy"
            if source.exists():
                destination = backup_dir / source.name
                shutil.copy2(source, destination)
                copied.append(str(destination))
    return copied


def _atomic_save_matrix(path: Path, matrix: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".novo.npy")
    np.save(temporary, matrix)
    temporary.replace(path)


def _promote_matrices(matrix_dir: Path, backup_dir: Path, prefix: str, A, A_inv) -> list[str]:
    copied = _backup_active_matrices(matrix_dir, backup_dir, prefix)
    for regime in ("fine", "coarse"):
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_{regime}.npy", A)
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_inv_{regime}.npy", A_inv)
    return copied


def _matrix_prefix() -> str:
    return foco.IDS_MATRIX_PREFIX if backend_name() == "ids" else "foco_temp"


def _output_dirs():
    results = Path(__file__).resolve().parents[1] / "resultados"
    return (
        Path(os.environ.get("QKD_CALIBRATION_OUTPUT_DIR", results / "calibracao")),
        Path(os.environ.get("QKD_CALIBRATION_METADATA_DIR", results / "json")),
        Path(os.environ.get("QKD_CALIBRATION_MATRIX_DIR", results / "matrizes")),
    )


def _raw_target_from_display(sensor_w, sensor_h, x, y):
    return (sensor_w - 1 - x, sensor_h - 1 - y) if foco.ROTATE_IMAGE_180 else (x, y)


def _local_target_from_raw(w, h, ox, oy, raw_x, raw_y):
    x, y = raw_x - ox, raw_y - oy
    return (float(w - 1 - x), float(h - 1 - y)) if foco.ROTATE_IMAGE_180 else (float(x), float(y))


def main(profile_name: str | None = None) -> None:
    if backend_name() not in {"ids", "zwo_sdk"}:
        raise RuntimeError("A calibracao continua requer backend 'ids' ou 'zwo_sdk'.")
    profile = calibration_profile(profile_name or os.environ.get("QKD_CALIBRATION_PROFILE", "robusto"))
    calibration_dir, metadata_dir, matrix_dir = _output_dirs()
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = calibration_dir / "continua" / f"calibracao_{timestamp}_{profile.name}"
    backup_dir = calibration_dir / "backups" / f"antes_continua_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    matrix_dir.mkdir(parents=True, exist_ok=True)
    initial_position = None
    connected = promoted = False
    all_runs, failed_optional = [], []
    summary = {"started_epoch": time.time(), "status": "iniciado", "run_dir": display_path(run_dir),
               "backend": backend_name(), "profile": profile.name,
               "profile_description": profile.description, "trajectory": [asdict(s) for s in profile.specs]}
    try:
        ensure_connected(); ensure_unparked(); ensure_not_tracking()
        connect_camera(); connected = True
        set_gain(foco.CAMERA_GAIN); foco.set_focus_mode("dual")
        camera = direct_camera()
        camera.reset_roi()
        full_frame = foco.capture_frame(foco.EXPOSURE_SECONDS, light=True)
        selection = foco.escolher_ilha_manualmente(full_frame, max_jump_px=TRACKER_MAX_SPOT_JUMP_PX)
        sensor_h, sensor_w = full_frame.shape[:2]
        roi_size = roi_size_for_backend(backend_name())
        raw_x, raw_y = _raw_target_from_display(sensor_w, sensor_h, selection["x_px"], selection["y_px"])
        start_x, start_y, _, _ = roi_incluindo_alvo(sensor_w, sensor_h, roi_size, roi_size, raw_x, raw_y)
        actual_w, actual_h, actual_x, actual_y = camera.set_roi(roi_size, roi_size, start_x, start_y)
        target_local = _local_target_from_raw(actual_w, actual_h, actual_x, actual_y, raw_x, raw_y)
        if not (0 <= target_local[0] < actual_w and 0 <= target_local[1] < actual_h):
            raise RuntimeError("O alinhamento da ROI deixou a luz fora do recorte.")
        initial_position = read_altaz()
        initial_az, initial_alt = initial_position
        summary.update(initial_az_deg=initial_az, initial_alt_deg=initial_alt,
                       roi=[actual_w, actual_h, actual_x, actual_y],
                       target_full_px=[selection["x_px"], selection["y_px"]],
                       target_local_px=list(target_local), sweep_rate_deg_s=SWEEP_RATE_DEG_S,
                       local_half_range_deg=LOCAL_HALF_RANGE_DEG, wide_half_range_deg=WIDE_HALF_RANGE_DEG,
                       exposure_seconds=foco.EXPOSURE_SECONDS, gain=foco.CAMERA_GAIN)
        print(f"\nCalibracao continua {profile.name} | camera={backend_name()} | ROI={actual_w}x{actual_h}")
        print(profile.description)
        print("Ctrl+C para parar os eixos e retornar a posicao absoluta inicial.")
        center_anchor = target_local
        for spec in profile.specs:
            returned = _return_to_absolute_start(initial_az, initial_alt)
            if not returned["success"]:
                raise RuntimeError(f"Retorno antes de {spec.name} falhou: {returned}")
            try:
                samples, center_anchor = _run_one_sweep(
                    spec=spec, initial_az=initial_az, initial_alt=initial_alt,
                    signature=selection["signature"], center_anchor=center_anchor,
                )
                all_runs.append(samples)
            except Exception as exc:
                if spec.role != "holdout_amplo":
                    raise
                failed_optional.append({"run": spec.name, "error": str(exc)})
                print(f"Aviso: teste amplo indisponivel: {exc}")
        returned = _return_to_absolute_start(initial_az, initial_alt)
        if not returned["success"]:
            raise RuntimeError(f"Retorno final nao confirmado: {returned}")

        fit_runs = [r for r in all_runs if r[0].role == "fit"]
        local_runs = [r for r in all_runs if r[0].role == "holdout_local"]
        wide_runs = [r for r in all_runs if r[0].role == "holdout_amplo"]
        fit = _robust_fit(*_center_runs(fit_runs))
        fit_validation = _validate_fit(fit_runs, fit)
        local_validation = (_validate_holdout(local_runs, fit, label="holdout_local")
                            if profile.requires_holdout else {"ok": True, "not_independent": True})
        wide_validation = _validate_holdout(wide_runs, fit, label="holdout_amplo")
        activation_ok = bool(fit_validation["ok"] and local_validation["ok"])
        capture_times = np.array(
            [sample.capture_duration_s for run in all_runs for sample in run],
            dtype=float,
        )
        _write_csv(run_dir / "amostras.csv", all_runs)
        np.save(run_dir / "A_continua.npy", fit["A"])
        np.save(run_dir / "A_inv_continua.npy", fit["A_inv"])
        summary.update(status="validada" if activation_ok else "rejeitada", finished_epoch=time.time(),
                       sample_count=sum(map(len, all_runs)), fit_sample_count=sum(map(len, fit_runs)),
                       A=fit["A"].tolist(), A_inv=fit["A_inv"].tolist(),
                       rms_residual_px=fit["rms_residual_px"], median_residual_px=fit["median_residual_px"],
                       max_residual_px=fit["max_residual_px"], condition_number=fit["condition_number"],
                       fit_validation=fit_validation, local_holdout_validation=local_validation,
                       wide_validation=wide_validation, failed_optional_runs=failed_optional,
                       capture_mean_ms=float(1000.0 * np.mean(capture_times)),
                       capture_p95_ms=float(1000.0 * np.percentile(capture_times, 95)),
                       capture_rate_hz=float(1.0 / max(np.mean(capture_times), 1e-9)),
                       validated_half_range_deg=WIDE_HALF_RANGE_DEG if wide_validation.get("ok") else LOCAL_HALF_RANGE_DEG,
                       return_to_start=returned)
        print(f"\nA =\n{fit['A']}")
        print(f"RMS ajuste={fit['rms_residual_px']:.2f}px | cond={fit['condition_number']:.2f} | amostras={summary['sample_count']}")
        print(
            f"Captura={summary['capture_rate_hz']:.1f} Hz | "
            f"media={summary['capture_mean_ms']:.1f} ms | p95={summary['capture_p95_ms']:.1f} ms"
        )
        if profile.requires_holdout:
            print(f"Holdout local: {'OK' if local_validation['ok'] else 'FALHOU'}")
            print(f"Amplitude {WIDE_HALF_RANGE_DEG:.3f} deg: {'linear' if wide_validation.get('ok') else 'nao validada'}")
        if not activation_ok:
            print("REJEITADA; matrizes atuais preservadas:")
            for failure in fit_validation["failures"] + local_validation.get("failures", []):
                print(f"  - {failure}")
        else:
            registrar = input(
                "Validacao local aprovada. Registrar este ponto no mapa de Jacobianas? [S/n]: "
            ).strip().lower()
            if registrar not in {"n", "nao", "não"}:
                prefix = _matrix_prefix()
                mapa_path = matrix_dir / f"{prefix}_mapa_jacobianas.json"
                no_nome = f"{timestamp}_{initial_az:.6f}_{initial_alt:.6f}"
                registrar_no(
                    mapa_path,
                    nome=no_nome,
                    azimute_deg=initial_az,
                    altitude_deg=initial_alt,
                    A=fit["A"],
                    A_inv=fit["A_inv"],
                    rms_residual_px=fit["rms_residual_px"],
                    raio_validado_deg=summary["validated_half_range_deg"],
                    metadata={
                        "backend": backend_name(),
                        "profile": profile.name,
                        "run_dir": display_path(run_dir),
                        "rotate_image_180": foco.ROTATE_IMAGE_180,
                        "fit_validation": fit_validation,
                        "local_holdout_validation": local_validation,
                        "wide_validation": wide_validation,
                    },
                )
                summary.update(
                    jacobian_map_path=display_path(mapa_path),
                    jacobian_node_name=no_nome,
                )
                print(f"No local registrado em: {display_path(mapa_path)}")

        if activation_ok and input("Ativar esta matriz no tracker? (s/N): ").strip().lower() in {"s", "sim"}:
            prefix = _matrix_prefix()
            previous_target = metadata_dir / TARGET_FILENAME
            if previous_target.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(previous_target, backup_dir / previous_target.name)
            backups = _promote_matrices(matrix_dir, backup_dir, prefix, fit["A"], fit["A_inv"])
            target_path = salvar_alvo(selection["x_px"], selection["y_px"],
                                      source="continuous_sweep_calibration", frame_shape=full_frame.shape,
                                      samples=sum(map(len, all_runs)), focus_mode="dual",
                                      focus_signature=selection["signature"])
            promoted = True
            summary.update(promoted_to_tracker=True, matrix_prefix=prefix,
                           backup_files=backups, saved_target_path=display_path(target_path))
            print(f"Matriz ativada. Backup anterior: {display_path(backup_dir)}")
        elif activation_ok:
            summary["promoted_to_tracker"] = False
            print("Resultado salvo na auditoria; tracker nao foi alterado.")
    except KeyboardInterrupt:
        summary.update(status="interrompida", finished_epoch=time.time())
        print("\nCalibracao continua interrompida pelo usuario.")
    except Exception as exc:
        summary.update(status="erro", error=str(exc), finished_epoch=time.time())
        print(f"\nErro na calibracao continua: {exc}")
    finally:
        stop_axes_safely()
        if initial_position is not None:
            returned = _return_to_absolute_start(*initial_position)
            summary["return_to_start_finally"] = returned
            print("Posicao absoluta inicial restaurada." if returned["success"] else f"ALERTA: retorno nao confirmado: {returned}")
        try:
            if connected:
                direct_camera().reset_roi()
                disconnect_camera()
        except Exception as exc:
            summary["camera_close_error"] = str(exc)
        summary["promoted"] = promoted
        summary.setdefault("finished_epoch", time.time())
        (run_dir / "resumo.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        metadata = metadata_dir / f"{_matrix_prefix()}_calibracao_continua_meta.json"
        metadata.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Auditoria salva em: {display_path(run_dir)}")
