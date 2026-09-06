"""Movimento PID simultaneo em Az/Alt e a linha de status do console.

Objetivo: levar o mount a um alvo angular e parar. Usado pela calibracao, pelo
retorno seguro do tracker e pelo autoteste.
Entradas/saidas: deltas ou alvo absoluto em graus; devolve nada, mas garante
velocidade zero ao sair.
Hardware: telescopio via ``mount_ascom``.
Seguranca: o ``finally`` sempre chama ``stop_axes_safely``.

Os comandos crus do mount ficam em ``mount_ascom.py``.
"""

from __future__ import annotations

import ctypes
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from modulos.controle.mount_ascom import (
    TOLERANCIA_GRAUS,
    VEL_MAX_LIMITE,
    VEL_MIN_LIMITE,
    calc_error,
    ensure_connected,
    ensure_not_tracking,
    ensure_unparked,
    move_axis,
    read_altaz,
    stop_axes_safely,
)


MAX_TEMPO_MOV = 450
MAX_CORRECOES = 10
_status_line_len = 0
_status_slot_active = False


class PID:
    def __init__(self, kp, ki, kd, setpoint=0.0,
                 output_limits=(None, None),
                 integral_limit=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint

        self._integral = 0.0
        self._last_error = None
        self._last_time = None

        self.min_output, self.max_output = output_limits
        self.integral_limit = integral_limit

    def reset(self):
        self._integral = 0.0
        self._last_error = None
        self._last_time = None

    def clamp_integral(self):
        if self.integral_limit is not None:
            self._integral = np.clip(
                self._integral,
                -self.integral_limit,
                self.integral_limit,
            )

    def update(self, axis, measured_value):
        error = calc_error(axis, self.setpoint, measured_value)
        now = time.time()

        if self._last_time is None:
            self._last_time = now
            self._last_error = error
            return 0.0, error

        dt = now - self._last_time
        de = error - self._last_error

        P = self.kp * error
        self._integral += error * dt

        self.clamp_integral()

        I = self.ki * self._integral
        D = self.kd * (de / dt) if dt > 0 else 0.0

        output = P + I + D

        if self.min_output is not None and self.max_output is not None:
            output = np.clip(output, self.min_output, self.max_output)

        self._last_error = error
        self._last_time = now
        return output, error


def _enable_virtual_terminal() -> bool:
    if sys.platform != "win32":
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle == 0 or handle == -1:
            return False

        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False

        enable_vt = 0x0004
        if (mode.value & enable_vt) == 0:
            if kernel32.SetConsoleMode(handle, mode.value | enable_vt) == 0:
                return False
        return True
    except Exception:
        return False


_ansi_cursor_ok = _enable_virtual_terminal()


def _status_write(message: str):
    global _status_line_len, _status_slot_active
    padded = message.ljust(max(_status_line_len, len(message)))

    if _ansi_cursor_ok:
        if not _status_slot_active:
            sys.stdout.write("\n")
            _status_slot_active = True
        sys.stdout.write("\x1b[1A\x1b[2K" + padded + "\n")
    else:
        sys.stdout.write("\r" + padded)

    sys.stdout.flush()
    _status_line_len = len(message)


def _status_clear():
    global _status_line_len, _status_slot_active
    if _status_line_len:
        if _ansi_cursor_ok and _status_slot_active:
            sys.stdout.write("\x1b[1A\x1b[2K")
        else:
            sys.stdout.write("\r" + (" " * _status_line_len) + "\r")
        sys.stdout.flush()
        _status_line_len = 0
    _status_slot_active = False


def move_axes_pid_2d(
    mount: bool,
    delta_az: float,
    delta_alt: float,
    max_velocity_deg_s: float | None = None,
    *,
    absolute_target: tuple[float, float] | None = None,
    telemetry_callback=None,
    tolerance_deg: float | None = None,
):
    """Movimento continuo simultaneo nos dois eixos usando PID e Threads.

    ``tolerance_deg`` permite que um chamador (o ``mount_agent``, por exemplo)
    afrouxe a chegada sem alterar uma constante global do processo.
    """

    tolerance = TOLERANCIA_GRAUS if tolerance_deg is None else abs(float(tolerance_deg))
    if not tolerance > 0.0:
        raise ValueError("tolerance_deg precisa ser positiva.")

    velocity_limit = VEL_MAX_LIMITE
    if max_velocity_deg_s is not None:
        velocity_limit = min(VEL_MAX_LIMITE, abs(float(max_velocity_deg_s)))
        if velocity_limit < VEL_MIN_LIMITE:
            raise ValueError(
                "max_velocity_deg_s precisa ser maior ou igual a VEL_MIN_LIMITE"
            )

    az0, alt0 = read_altaz()
    alvo_az = (az0 + delta_az) % 360
    alvo_alt = alt0 + delta_alt
    if absolute_target is not None:
        if len(absolute_target) != 2 or not np.all(np.isfinite(absolute_target)):
            raise ValueError("Alvo absoluto invalido.")
        # Nao somar um delta calculado com uma leitura antiga a uma nova leitura.
        alvo_az = float(absolute_target[0]) % 360.0
        alvo_alt = float(absolute_target[1])
        delta_az = float(calc_error(0, alvo_az, az0))
        delta_alt = alvo_alt - alt0

    # Travas de seguranca fisicas para a Altitude
    if alvo_alt > 90:
        print("Movimento ultrapassa limite superior em altitude. Ajustado para 90 graus.")
        alvo_alt = 90.0
    elif alvo_alt < -90:
        print("Movimento ultrapassa limite inferior em altitude. Ajustado para -90 graus.")
        alvo_alt = -90.0

    # Instancia dois PIDs independentes
    pid_az = PID(kp=1.3967, ki=0.0001, kd=0.1015, setpoint=alvo_az,
                 output_limits=(-velocity_limit, velocity_limit), integral_limit=5)
    pid_alt = PID(kp=1.3967, ki=0.0001, kd=0.1015, setpoint=alvo_alt,
                  output_limits=(-velocity_limit, velocity_limit), integral_limit=5)

    print("\nMovimento PID 2D Iniciado:")
    print(f"  Alvo Azimute  = {alvo_az:.4f} deg (delta {delta_az:+.4f})")
    print(f"  Alvo Altitude = {alvo_alt:.4f} deg (delta {delta_alt:+.4f})")

    t0 = time.time()
    tempo_decorrido = 0.0

    error_last_az = None
    error_last_alt = None
    inversoes_az = 0
    inversoes_alt = 0
    az_parado = False
    alt_parado = False

    # Inicializa o executor de threads FORA do loop para economizar CPU
    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            while True:
                az, alt = read_altaz()
                sample_t = time.perf_counter()
                tempo_decorrido = time.time() - t0

                cmd_az, error_az = pid_az.update(0, az)
                cmd_alt, error_alt = pid_alt.update(1, alt)

                err_abs_az = abs(error_az)
                err_abs_alt = abs(error_alt)

                if tempo_decorrido > MAX_TEMPO_MOV:
                    _status_clear()
                    print("\nTempo limite atingido.")
                    break

                az_ok = err_abs_az < tolerance
                alt_ok = err_abs_alt < tolerance

                if az_ok and alt_ok:
                    _status_clear()
                    print("\nAmbos os eixos dentro da tolerancia.")
                    executor.submit(move_axis, 0, 0.0, mount)
                    executor.submit(move_axis, 1, 0.0, mount)
                    break

                # Overshoot Azimute
                if error_last_az is not None and error_az * error_last_az < 0:
                    inversoes_az += 1
                    if inversoes_az > MAX_CORRECOES:
                        _status_clear()
                        print("\nExcesso de inversoes em Azimute. Abortando eixo 0.")
                        cmd_az = 0.0
                    else:
                        pid_az.reset()
                        cmd_az = 0.0  # Zera a velocidade momentaneamente para frear

                # Overshoot Altitude
                if error_last_alt is not None and error_alt * error_last_alt < 0:
                    inversoes_alt += 1
                    if inversoes_alt > MAX_CORRECOES:
                        _status_clear()
                        print("\nExcesso de inversoes em Altitude. Abortando eixo 1.")
                        cmd_alt = 0.0
                    else:
                        pid_alt.reset()
                        cmd_alt = 0.0

                # Zona morta e velocidade minima
                if az_ok:
                    cmd_az = 0.0
                elif 0 < abs(cmd_az) < VEL_MIN_LIMITE:
                    cmd_az = VEL_MIN_LIMITE * np.sign(cmd_az)

                if alt_ok:
                    cmd_alt = 0.0
                elif 0 < abs(cmd_alt) < VEL_MIN_LIMITE:
                    cmd_alt = VEL_MIN_LIMITE * np.sign(cmd_alt)

                future_az = None
                future_alt = None

                if not (az_ok and az_parado):
                    future_az = executor.submit(move_axis, 0, cmd_az, mount)
                    az_parado = az_ok

                if not (alt_ok and alt_parado):
                    future_alt = executor.submit(move_axis, 1, cmd_alt, mount)
                    alt_parado = alt_ok

                # Aguarda as threads confirmarem envio antes de prosseguir o loop
                if future_az:
                    future_az.result()
                if future_alt:
                    future_alt.result()

                if telemetry_callback is not None:
                    telemetry_callback(dict(
                        t=sample_t, az_deg=float(az), alt_deg=float(alt),
                        target_az_deg=float(alvo_az), target_alt_deg=float(alvo_alt),
                        error_az_deg=float(error_az), error_alt_deg=float(error_alt),
                        cmd_az_deg_s=float(cmd_az), cmd_alt_deg_s=float(cmd_alt),
                    ))

                _status_write(
                    f"Az E:{error_az:+.4f} V:{cmd_az:+.4f} | "
                    f"Alt E:{error_alt:+.4f} V:{cmd_alt:+.4f}"
                )

                error_last_az = error_az
                error_last_alt = error_alt

                # Ajusta o descanso do loop conforme o maior erro atual
                max_err = max(err_abs_az, err_abs_alt)
                if max_err > 1.0:
                    dt_sleep = 0.1
                elif max_err > 0.1:
                    dt_sleep = 0.05
                else:
                    dt_sleep = 0.01

                time.sleep(dt_sleep)

        finally:
            # Parada dura sequencial para emergencias e fim de execucao
            stop_axes_safely()

            _status_clear()
            azf, altf = read_altaz()
            print(
                f"\nPos final: Az={azf:.4f} deg, Alt={altf:.4f} deg | "
                f"Tempo total: {tempo_decorrido:.2f}s"
            )


def main():
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()

    print("=== Movimento PID 2D Simultaneo ===\n")
    mount = bool(int(input("mount 1, simulador 0: ")))
    while True:
        try:
            az, alt = read_altaz()
            print(f"Pos atual: Az={az:.3f} deg, Alt={alt:.3f} deg")

            delta_az = float(input("Delta Azimute (graus +/-): ").strip())
            delta_alt = float(input("Delta Altitude (graus +/-): ").strip())

            if delta_az == 0.0 and delta_alt == 0.0:
                print("Nenhum movimento ordenado.")
                continue

            move_axes_pid_2d(mount, delta_az, delta_alt)
            print()

        except KeyboardInterrupt:
            print("\nSaindo...")
            break
        except Exception as e:
            print(f"Erro: {e}\n")


if __name__ == "__main__":
    main()
