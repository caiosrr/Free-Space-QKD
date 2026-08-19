"""Calibracao angular-pixel por varredura continua.

Este programa nao substitui a calibracao por pontos. Durante cada movimento a
camera continua adquirindo, de modo que a trava espacial acompanha a mesma luz
frame a frame. As matrizes ativas so sao promovidas depois das validacoes e de
uma confirmacao explicita; as matrizes anteriores sao copiadas para um backup.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from artifact_paths import display_path
from config_tracker import TRACKER_MAX_SPOT_JUMP_PX, roi_size_for_backend
from controle.alvo_alinhamento import roi_incluindo_alvo, salvar_alvo
from controle.camera_backend import backend_name, connect_camera, disconnect_camera, set_gain
from controle.mount_control import (
    TOLERANCIA_GRAUS,
    calc_error,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    move_axes_pid_2d,
    move_axis,
    read_altaz,
    stop_axes_safely,
)
from foco_multiplos import Center_of_Mass_foco_temp as foco


EXPOSURE_SECONDS = float(os.environ.get("QKD_IDS_EXPOSURE_US", "7276")) * 1e-6
CAMERA_GAIN = float(os.environ.get("QKD_IDS_ANALOG_GAIN", "1"))
SWEEP_RATE_DEG_S = 0.002
SWEEP_HALF_RANGE_DEG = 0.008
SWEEP_HARD_LIMIT_DEG = 0.012
OTHER_AXIS_LIMIT_DEG = 0.004
RETURN_MAX_RATE_DEG_S = 0.02
RETURN_ATTEMPTS = 2
BASELINE_VALID_FRAMES = 10
SIGNAL_LOSS_TIMEOUT_S = 1.5
MIN_VALID_SWEEP_SAMPLES = 35
HUBER_K = 1.5
ROBUST_ITERS = 10


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


def _offsets_from_start(
    initial_az: float,
    initial_alt: float,
    az: float,
    alt: float,
) -> tuple[float, float]:
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
                raise RuntimeError(
                    "Retorno automatico recusado: deslocamento maior que 0.05 deg."
                )
            print(
                f"Retorno {attempt}/{RETURN_ATTEMPTS}: "
                f"dAz={delta_az:+.5f} deg dAlt={delta_alt:+.5f} deg"
            )
            move_axes_pid_2d(
                True,
                delta_az,
                delta_alt,
                max_velocity_deg_s=RETURN_MAX_RATE_DEG_S,
            )

        final_az, final_alt = read_altaz()
        error_az = float(calc_error(0, initial_az, final_az))
        error_alt = float(initial_alt - final_alt)
        result.update(
            {
                "final_az_deg": final_az,
                "final_alt_deg": final_alt,
                "error_az_deg": error_az,
                "error_alt_deg": error_alt,
                "success": max(abs(error_az), abs(error_alt)) <= TOLERANCIA_GRAUS,
            }
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        stop_axes_safely()
    return result


def _capture_valid_cm() -> tuple[np.ndarray, tuple[float, float, float, bool] | None]:
    frame = foco.capture_frame(EXPOSURE_SECONDS, light=True)
    return frame, foco.centro_massa(frame)


def _baseline_anchor(
    signature: dict,
    expected_x: float,
    expected_y: float,
) -> tuple[float, float]:
    if not foco.initialize_focus_lock(
        signature,
        expected_x,
        expected_y,
        freeze_reference=True,
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    ):
        raise RuntimeError("Nao consegui inicializar a assinatura da luz.")

    xs: list[float] = []
    ys: list[float] = []
    deadline = time.perf_counter() + 5.0
    while len(xs) < BASELINE_VALID_FRAMES and time.perf_counter() < deadline:
        _, cm = _capture_valid_cm()
        if cm is None or cm[3]:
            continue
        xs.append(float(cm[0]))
        ys.append(float(cm[1]))
    if len(xs) < BASELINE_VALID_FRAMES:
        raise RuntimeError(
            f"Baseline insuficiente: {len(xs)}/{BASELINE_VALID_FRAMES} frames validos."
        )
    anchor = float(np.median(xs)), float(np.median(ys))
    foco.set_focus_expected_position(
        anchor[0],
        anchor[1],
        max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
    )
    return anchor


def _run_one_sweep(
    *,
    run_name: str,
    axis: int,
    command_sign: int,
    initial_az: float,
    initial_alt: float,
    signature: dict,
    center_anchor: tuple[float, float],
) -> tuple[list[SweepSample], tuple[float, float]]:
    center_anchor = _baseline_anchor(signature, *center_anchor)
    samples: list[SweepSample] = []
    started = time.perf_counter()
    last_valid = started
    last_print = 0.0
    timeout_s = (SWEEP_HALF_RANGE_DEG / SWEEP_RATE_DEG_S) * 3.0 + 4.0

    print(
        f"\n{run_name}: eixo={'Az' if axis == 0 else 'Alt'} "
        f"comando={command_sign:+d} rate={SWEEP_RATE_DEG_S:.4f} deg/s"
    )
    move_axis(axis, command_sign * SWEEP_RATE_DEG_S, True)
    try:
        while True:
            loop_t = time.perf_counter()
            if (loop_t - started) > timeout_s:
                raise RuntimeError(f"Tempo limite na varredura {run_name}.")

            az_before, alt_before = read_altaz()
            _, cm = _capture_valid_cm()
            az_after, alt_after = read_altaz()
            daz_before, dalt_before = _offsets_from_start(
                initial_az, initial_alt, az_before, alt_before
            )
            daz_after, dalt_after = _offsets_from_start(
                initial_az, initial_alt, az_after, alt_after
            )
            daz = 0.5 * (daz_before + daz_after)
            dalt = 0.5 * (dalt_before + dalt_after)
            active_offset = daz if axis == 0 else dalt
            other_offset = dalt if axis == 0 else daz

            if abs(active_offset) > SWEEP_HARD_LIMIT_DEG:
                raise RuntimeError(
                    f"Watchdog: {run_name} excedeu {SWEEP_HARD_LIMIT_DEG:.3f} deg."
                )
            if abs(other_offset) > OTHER_AXIS_LIMIT_DEG:
                raise RuntimeError(
                    f"Watchdog: outro eixo derivou {other_offset:+.4f} deg."
                )

            if cm is not None and not cm[3]:
                last_valid = time.perf_counter()
                samples.append(
                    SweepSample(
                        run=run_name,
                        axis=axis,
                        command_sign=command_sign,
                        elapsed_s=time.perf_counter() - started,
                        az_deg=0.5 * (az_before + az_after),
                        alt_deg=0.5 * (alt_before + alt_after),
                        delta_az_deg=daz,
                        delta_alt_deg=dalt,
                        x_px=float(cm[0]),
                        y_px=float(cm[1]),
                    )
                )
            elif cm is not None and cm[3]:
                raise RuntimeError(f"A luz tocou a borda durante {run_name}.")

            if (time.perf_counter() - last_valid) > SIGNAL_LOSS_TIMEOUT_S:
                raise RuntimeError(f"Luz perdida por mais de {SIGNAL_LOSS_TIMEOUT_S:.1f}s.")

            if (loop_t - last_print) >= 0.5:
                cm_text = "sem sinal" if cm is None else f"CM=({cm[0]:.1f},{cm[1]:.1f})"
                print(
                    f"  offset={active_offset:+.5f} deg | {cm_text} | "
                    f"validos={len(samples)}"
                )
                last_print = loop_t

            if abs(active_offset) >= SWEEP_HALF_RANGE_DEG:
                break
    finally:
        stop_axes_safely()

    if len(samples) < MIN_VALID_SWEEP_SAMPLES:
        raise RuntimeError(
            f"{run_name}: somente {len(samples)} amostras validas; "
            f"minimo={MIN_VALID_SWEEP_SAMPLES}."
        )
    active_values = np.array(
        [s.delta_az_deg if axis == 0 else s.delta_alt_deg for s in samples],
        dtype=float,
    )
    span = float(np.ptp(active_values))
    if span < 0.005:
        raise RuntimeError(f"{run_name}: amplitude medida insuficiente ({span:.5f} deg).")
    print(f"  concluida: {len(samples)} amostras, amplitude={span:.5f} deg.")
    return samples, center_anchor


def _center_runs(runs: list[list[SweepSample]]) -> tuple[np.ndarray, np.ndarray]:
    design_parts = []
    pixel_parts = []
    for samples in runs:
        design = np.array(
            [[sample.delta_az_deg, sample.delta_alt_deg] for sample in samples],
            dtype=float,
        )
        pixels = np.array([[sample.x_px, sample.y_px] for sample in samples], dtype=float)
        design_parts.append(design - np.median(design, axis=0))
        pixel_parts.append(pixels - np.median(pixels, axis=0))
    return np.vstack(design_parts), np.vstack(pixel_parts)


def _robust_fit(design: np.ndarray, pixels: np.ndarray) -> dict:
    if design.ndim != 2 or design.shape[1] != 2 or pixels.shape != design.shape:
        raise ValueError("Dados do ajuste precisam ter formato Nx2.")
    weights = np.ones(design.shape[0], dtype=float)
    beta = np.linalg.lstsq(design, pixels, rcond=None)[0]
    for _ in range(ROBUST_ITERS):
        root_w = np.sqrt(np.clip(weights, 1e-6, None))[:, None]
        beta = np.linalg.lstsq(design * root_w, pixels * root_w, rcond=None)[0]
        residual = np.linalg.norm(pixels - (design @ beta), axis=1)
        scale = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
        scale = max(scale, 0.25)
        cutoff = HUBER_K * scale
        weights = np.where(residual <= cutoff, 1.0, cutoff / np.maximum(residual, 1e-9))

    prediction = design @ beta
    residual_vectors = pixels - prediction
    residual_norm = np.linalg.norm(residual_vectors, axis=1)
    A = beta.T
    condition = float(np.linalg.cond(A))
    if not np.all(np.isfinite(A)) or condition > 100.0:
        raise RuntimeError(f"Matriz continua mal condicionada: cond={condition:.2f}.")
    return {
        "A": A,
        "A_inv": np.linalg.inv(A),
        "weights": weights,
        "rms_residual_px": float(np.sqrt(np.mean(residual_norm**2))),
        "median_residual_px": float(np.median(residual_norm)),
        "max_residual_px": float(np.max(residual_norm)),
        "condition_number": condition,
    }


def _direction_slope(samples: list[SweepSample], axis: int) -> np.ndarray:
    d = np.array(
        [sample.delta_az_deg if axis == 0 else sample.delta_alt_deg for sample in samples],
        dtype=float,
    )
    p = np.array([[sample.x_px, sample.y_px] for sample in samples], dtype=float)
    d = d - np.median(d)
    p = p - np.median(p, axis=0)
    denom = float(d @ d)
    if denom <= 1e-10:
        raise RuntimeError("Trajetoria sem variacao angular suficiente.")
    slope = (d[:, None] * p).sum(axis=0) / denom
    for _ in range(6):
        residual = np.linalg.norm(p - d[:, None] * slope[None, :], axis=1)
        scale = max(1.4826 * float(np.median(np.abs(residual - np.median(residual)))), 0.25)
        cutoff = HUBER_K * scale
        weights = np.where(residual <= cutoff, 1.0, cutoff / np.maximum(residual, 1e-9))
        denom = float(np.sum(weights * d * d))
        slope = np.sum((weights * d)[:, None] * p, axis=0) / denom
    return slope


def _validate_fit(runs: list[list[SweepSample]], fit: dict) -> dict:
    direction_checks = {}
    failures = []
    for axis, label in ((0, "az"), (1, "alt")):
        axis_runs = [samples for samples in runs if samples[0].axis == axis]
        if len(axis_runs) != 2:
            failures.append(f"{label}: faltam duas direcoes")
            continue
        v1 = _direction_slope(axis_runs[0], axis)
        v2 = _direction_slope(axis_runs[1], axis)
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        cosine = float(np.dot(v1, v2) / max(n1 * n2, 1e-12))
        ratio = float(max(n1, n2) / max(min(n1, n2), 1e-12))
        direction_checks[label] = {
            "slope_direction_1_px_per_deg": v1.tolist(),
            "slope_direction_2_px_per_deg": v2.tolist(),
            "cosine": cosine,
            "magnitude_ratio": ratio,
        }
        if cosine < 0.55:
            failures.append(f"{label}: ida/volta discordam (cosseno={cosine:.2f})")
        if ratio > 3.0:
            failures.append(f"{label}: escalas ida/volta diferem {ratio:.1f}x")

    rms = float(fit["rms_residual_px"])
    response = {}
    for axis, label in ((0, "az"), (1, "alt")):
        span_px = float(np.linalg.norm(fit["A"][:, axis]) * SWEEP_HALF_RANGE_DEG)
        response[label] = span_px
        if span_px < max(8.0, 1.5 * rms):
            failures.append(
                f"{label}: resposta {span_px:.1f}px insuficiente para residuo {rms:.1f}px"
            )

    return {
        "ok": not failures,
        "failures": failures,
        "direction_checks": direction_checks,
        "predicted_response_at_half_range_px": response,
    }


def _write_csv(path: Path, runs: list[list[SweepSample]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(SweepSample.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for samples in runs:
            for sample in samples:
                writer.writerow(sample.__dict__)


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


def _promote_matrices(
    matrix_dir: Path,
    backup_dir: Path,
    prefix: str,
    A: np.ndarray,
    A_inv: np.ndarray,
) -> list[str]:
    copied = _backup_active_matrices(matrix_dir, backup_dir, prefix)
    for regime in ("fine", "coarse"):
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_{regime}.npy", A)
        _atomic_save_matrix(matrix_dir / f"{prefix}_A_inv_{regime}.npy", A_inv)
    return copied


def main() -> None:
    if backend_name() != "ids":
        raise RuntimeError("Esta primeira versao da varredura continua requer a camera IDS.")
    if os.environ.get("QKD_ROTATE_IMAGE_180", "0") != "0":
        raise RuntimeError("Use ROTATE_IMAGE_180=False para a varredura continua IDS.")

    calibration_dir = Path(os.environ["QKD_CALIBRATION_OUTPUT_DIR"])
    metadata_dir = Path(os.environ["QKD_CALIBRATION_METADATA_DIR"])
    matrix_dir = Path(os.environ["QKD_CALIBRATION_MATRIX_DIR"])
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = calibration_dir / "varredura_continua" / f"varredura_{timestamp}"
    backup_dir = calibration_dir / "backups" / f"antes_varredura_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    matrix_dir.mkdir(parents=True, exist_ok=True)

    initial_position = None
    return_result = None
    connected = False
    promoted = False
    runs: list[list[SweepSample]] = []
    summary: dict = {
        "started_epoch": time.time(),
        "status": "iniciado",
        "run_dir": display_path(run_dir),
    }

    try:
        ensure_connected()
        ensure_unparked()
        ensure_not_tracking()
        connect_camera()
        connected = True
        set_gain(CAMERA_GAIN)
        foco.set_focus_mode("dual")

        from controle.camera_ids_peak import camera

        camera.reset_roi()
        full_frame = foco.capture_frame(EXPOSURE_SECONDS, light=True)
        selection = foco.escolher_ilha_manualmente(
            full_frame,
            max_jump_px=TRACKER_MAX_SPOT_JUMP_PX,
        )
        sensor_h, sensor_w = full_frame.shape[:2]
        roi_size = roi_size_for_backend("ids")
        start_x, start_y, _, _ = roi_incluindo_alvo(
            sensor_w,
            sensor_h,
            roi_size,
            roi_size,
            selection["x_px"],
            selection["y_px"],
        )
        actual_w, actual_h, actual_x, actual_y = camera.set_roi(
            roi_size, roi_size, start_x, start_y
        )
        target_x_local = float(selection["x_px"] - actual_x)
        target_y_local = float(selection["y_px"] - actual_y)
        if not (0 <= target_x_local < actual_w and 0 <= target_y_local < actual_h):
            raise RuntimeError("O alinhamento da ROI IDS deixou a luz fora do recorte.")

        initial_position = read_altaz()
        initial_az, initial_alt = initial_position
        summary.update(
            {
                "initial_az_deg": initial_az,
                "initial_alt_deg": initial_alt,
                "roi": [actual_w, actual_h, actual_x, actual_y],
                "target_full_px": [selection["x_px"], selection["y_px"]],
                "target_local_px": [target_x_local, target_y_local],
                "sweep_rate_deg_s": SWEEP_RATE_DEG_S,
                "sweep_half_range_deg": SWEEP_HALF_RANGE_DEG,
            }
        )
        print(
            "\nVarredura continua IDS pronta. "
            f"ROI={actual_w}x{actual_h}; posicao inicial "
            f"Az={initial_az:.6f} Alt={initial_alt:.6f}."
        )
        print("Ctrl+C para e para a posicao inicial. Nao feche o ASCOM Remote.")

        center_anchor = (target_x_local, target_y_local)
        sequence = [
            ("az_comando_positivo", 0, +1),
            ("az_comando_negativo", 0, -1),
            ("alt_comando_positivo", 1, +1),
            ("alt_comando_negativo", 1, -1),
        ]
        for run_name, axis, command_sign in sequence:
            return_result = _return_to_absolute_start(initial_az, initial_alt)
            if not return_result["success"]:
                raise RuntimeError(f"Retorno antes de {run_name} falhou: {return_result}")
            samples, center_anchor = _run_one_sweep(
                run_name=run_name,
                axis=axis,
                command_sign=command_sign,
                initial_az=initial_az,
                initial_alt=initial_alt,
                signature=selection["signature"],
                center_anchor=center_anchor,
            )
            runs.append(samples)

        return_result = _return_to_absolute_start(initial_az, initial_alt)
        if not return_result["success"]:
            raise RuntimeError(f"Retorno final nao confirmado: {return_result}")

        design, pixels = _center_runs(runs)
        fit = _robust_fit(design, pixels)
        validation = _validate_fit(runs, fit)
        _write_csv(run_dir / "amostras.csv", runs)
        np.save(run_dir / "A_continua.npy", fit["A"])
        np.save(run_dir / "A_inv_continua.npy", fit["A_inv"])

        summary.update(
            {
                "status": "validada" if validation["ok"] else "rejeitada",
                "finished_epoch": time.time(),
                "sample_count": int(sum(len(samples) for samples in runs)),
                "A": fit["A"].tolist(),
                "A_inv": fit["A_inv"].tolist(),
                "rms_residual_px": fit["rms_residual_px"],
                "median_residual_px": fit["median_residual_px"],
                "max_residual_px": fit["max_residual_px"],
                "condition_number": fit["condition_number"],
                "validation": validation,
                "return_to_start": return_result,
            }
        )

        print("\nResultado da varredura continua:")
        print(f"A =\n{fit['A']}")
        print(
            f"RMS={fit['rms_residual_px']:.2f}px | "
            f"cond={fit['condition_number']:.2f} | amostras={summary['sample_count']}"
        )
        print(
            "Resposta prevista em 0.008 deg: "
            f"Az={validation['predicted_response_at_half_range_px']['az']:.1f}px | "
            f"Alt={validation['predicted_response_at_half_range_px']['alt']:.1f}px"
        )
        if not validation["ok"]:
            print("REJEITADA; matrizes atuais preservadas:")
            for failure in validation["failures"]:
                print(f"  - {failure}")
        else:
            activate = input(
                "Validacao aprovada. Ativar esta matriz no tracker? (s/N): "
            ).strip().lower()
            if activate in {"s", "sim"}:
                prefix = "ids_raw_foco_temp"
                previous_target = metadata_dir / "alvo_alinhamento_camera_sem_rotacao.json"
                if previous_target.exists():
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(previous_target, backup_dir / previous_target.name)
                backups = _promote_matrices(
                    matrix_dir,
                    backup_dir,
                    prefix,
                    fit["A"],
                    fit["A_inv"],
                )
                target_path = salvar_alvo(
                    selection["x_px"],
                    selection["y_px"],
                    source="continuous_sweep_calibration",
                    frame_shape=full_frame.shape,
                    samples=int(sum(len(samples) for samples in runs)),
                    std_x_px=None,
                    std_y_px=None,
                    focus_mode="dual",
                    focus_signature=selection["signature"],
                )
                promoted = True
                summary["promoted_to_tracker"] = True
                summary["backup_files"] = backups
                summary["saved_target_path"] = display_path(target_path)
                print(f"Matriz ativada. Backup anterior: {display_path(backup_dir)}")
            else:
                summary["promoted_to_tracker"] = False
                print("Matriz aprovada foi salva na auditoria, mas o tracker nao foi alterado.")

    except KeyboardInterrupt:
        summary.update({"status": "interrompida", "finished_epoch": time.time()})
        print("\nVarredura interrompida pelo usuario.")
    except Exception as exc:
        summary.update(
            {
                "status": "erro",
                "error": str(exc),
                "finished_epoch": time.time(),
            }
        )
        print(f"\nErro na varredura continua: {exc}")
    finally:
        stop_axes_safely()
        if initial_position is not None:
            return_result = _return_to_absolute_start(*initial_position)
            summary["return_to_start_finally"] = return_result
            if return_result["success"]:
                print("Posicao absoluta inicial restaurada.")
            else:
                print(f"ALERTA: retorno inicial nao confirmado: {return_result}")
        try:
            if connected:
                from controle.camera_ids_peak import camera

                camera.reset_roi()
                disconnect_camera()
        except Exception as exc:
            summary["camera_close_error"] = str(exc)
        summary["promoted"] = promoted
        (run_dir / "resumo.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        metadata_path = metadata_dir / "ids_raw_calibracao_varredura_continua_meta.json"
        metadata_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Auditoria salva em: {display_path(run_dir)}")


if __name__ == "__main__":
    main()
