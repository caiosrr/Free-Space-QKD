"""Watchdog, parada imediata e retorno controlado do mount."""

import time

from modulos.configuracoes.tracker import (
    MAX_OFFSET_ALT_DEG,
    MAX_OFFSET_AZ_DEG,
    POSITION_WATCHDOG_HZ,
    RETURN_ATTEMPTS,
    RETURN_MAX_RATE_DEG_S,
    RETURN_TOLERANCE_DEG,
    WATCHDOG_READ_FAILURES,
)
from modulos.controle.mount_ascom import (
    calc_error,
    read_altaz,
    stop_axes_safely,
)
from modulos.controle.mount_pid import move_axes_pid_2d
from modulos.controle.tracker_estado import TrackerState


def solicitar_parada(state: TrackerState, reason: str) -> bool:
    """Registra o primeiro motivo de seguranca e envia velocidade zero."""
    accepted = state.request_stop(reason)
    if accepted:
        stop_axes_safely()
    return accepted


def motivo_permite_retorno(reason: str | None) -> bool:
    """Nao inicia movimento cego quando o beacon desapareceu por completo."""
    return reason != "sinal_perdido_por_tempo_excessivo"


def deslocamentos_desde_inicio(
    initial_az_deg: float,
    initial_alt_deg: float,
    current_az_deg: float,
    current_alt_deg: float,
) -> tuple[float, float]:
    """Calcula deslocamentos assinados, incluindo Az 359/0 graus."""
    offset_az = -float(calc_error(0, initial_az_deg, current_az_deg))
    offset_alt = float(current_alt_deg - initial_alt_deg)
    return offset_az, offset_alt


def monitorar_posicao(
    state: TrackerState,
    initial_az_deg: float,
    initial_alt_deg: float,
    session_started: float,
    max_session_seconds: float,
) -> None:
    """Interrompe a sessao por tempo, deslocamento ou falha de leitura."""
    interval = 1.0 / POSITION_WATCHDOG_HZ
    consecutive_failures = 0

    while True:
        loop_started = time.perf_counter()
        with state.lock:
            if state.stop:
                return

        if (loop_started - session_started) >= max_session_seconds:
            solicitar_parada(state, "tempo_maximo_da_sessao")
            return

        try:
            az_deg, alt_deg = read_altaz()
            offset_az, offset_alt = deslocamentos_desde_inicio(
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
                solicitar_parada(
                    state,
                    f"limite_absoluto_az_{offset_az:+.4f}_deg",
                )
                return
            if abs(offset_alt) >= MAX_OFFSET_ALT_DEG:
                solicitar_parada(
                    state,
                    f"limite_absoluto_alt_{offset_alt:+.4f}_deg",
                )
                return
        except Exception as exc:
            consecutive_failures += 1
            with state.lock:
                state.watchdog_error = str(exc)
            if consecutive_failures >= WATCHDOG_READ_FAILURES:
                solicitar_parada(state, "watchdog_mount_sem_resposta")
                return

        elapsed = time.perf_counter() - loop_started
        time.sleep(max(0.0, interval - elapsed))


def retornar_posicao_inicial(
    initial_az_deg: float,
    initial_alt_deg: float,
) -> dict:
    """Tenta restaurar a posicao absoluta sem exceder os limites configurados."""
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
