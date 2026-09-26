"""Leitor do medidor de potencia Thorlabs PM100, pelo VISA ou pela API TLPM.

Tira do otimizar_acoplamento_pm100.py, onde nasceu, para ser usado tambem pelo
registrador do CBPF sem arrastar junto o controle do mount. Tenta primeiro o
VISA (pyvisa, comandos SCPI); se nao houver recurso VISA, cai na DLL TLPM que o
software da Thorlabs instala. O app Optical Power Monitor da Thorlabs precisa
estar FECHADO: ele segura o instrumento.
"""

from __future__ import annotations

import ctypes
import time


class PM100Reader:
    def __init__(self, wavelength_nm: float, resource_name: str | None = None):
        self.backend = "pyvisa"
        self.tlpm = None
        self.tlpm_session = None
        try:
            import pyvisa
        except ImportError as exc:
            raise RuntimeError(
                "pyvisa nao esta instalado. No PC2 rode: python -m pip install pyvisa"
            ) from exc

        self.pyvisa = pyvisa
        try:
            self.rm = pyvisa.ResourceManager()
        except ValueError:
            self.rm = pyvisa.ResourceManager("@py")
        resources = list(self.rm.list_resources())
        if not resources:
            self._init_tlpm(wavelength_nm)
            return

        if resource_name is None:
            resource_name = self._pick_pm100_resource(resources)
        self.resource_name = resource_name
        self.instrument = self.rm.open_resource(resource_name)
        self.instrument.timeout = 3000

        self.idn = self._query_first(["*IDN?"]).strip()
        self.set_wavelength(wavelength_nm)

    def _init_tlpm(self, wavelength_nm: float) -> None:
        dll_candidates = [
            r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPM_64.dll",
            r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPMX_64.dll",
            r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\Bin\TLPM_32.dll",
        ]
        last_exc = None
        for dll_path in dll_candidates:
            try:
                tlpm = ctypes.WinDLL(dll_path)
                prefix = "TLPMX" if "TLPMX" in dll_path.upper() else "TLPM"
                session = ctypes.c_uint32(0)
                count = ctypes.c_uint32(0)
                status = getattr(tlpm, f"{prefix}_findRsrc")(session, ctypes.byref(count))
                if status != 0 or count.value <= 0:
                    continue

                resource = ctypes.create_string_buffer(1024)
                status = getattr(tlpm, f"{prefix}_getRsrcName")(
                    session,
                    ctypes.c_uint32(0),
                    resource,
                )
                if status != 0:
                    continue

                opened_session = ctypes.c_uint32(0)
                status = getattr(tlpm, f"{prefix}_init")(
                    resource,
                    ctypes.c_bool(True),
                    ctypes.c_bool(True),
                    ctypes.byref(opened_session),
                )
                if status != 0:
                    continue

                self.backend = "tlpm"
                self.tlpm = tlpm
                self.tlpm_prefix = prefix
                self.tlpm_session = opened_session
                self.resource_name = resource.value.decode(errors="replace")
                self.idn = f"Thorlabs {prefix} ({self.resource_name})"
                self.set_wavelength(wavelength_nm)
                return
            except Exception as exc:
                last_exc = exc

        raise RuntimeError(
            "Nenhum recurso VISA encontrado e nao consegui abrir a API TLPM da Thorlabs. "
            "Feche o app da Thorlabs, confira o USB e teste novamente."
        ) from last_exc

    @staticmethod
    def _pick_pm100_resource(resources: list[str]) -> str:
        for resource in resources:
            upper = resource.upper()
            if "1313" in upper or "8072" in upper or "PM100" in upper:
                return resource
        for resource in resources:
            if resource.upper().startswith("USB"):
                return resource
        return resources[0]

    def _query_first(self, commands: list[str]) -> str:
        last_exc = None
        for command in commands:
            try:
                return str(self.instrument.query(command))
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"Falha consultando PM100 com {commands}: {last_exc}")

    def set_wavelength(self, wavelength_nm: float) -> None:
        if self.backend == "tlpm":
            try:
                status = getattr(self.tlpm, f"{self.tlpm_prefix}_setWavelength")(
                    self.tlpm_session,
                    ctypes.c_double(wavelength_nm),
                )
                if status == 0:
                    return
            except Exception:
                pass
            print("Aviso: nao consegui configurar wavelength via TLPM; confira no OPM.")
            return

        commands = [
            f"SENS:CORR:WAV {wavelength_nm}",
            f"SENSE:CORRECTION:WAVELENGTH {wavelength_nm}",
        ]
        for command in commands:
            try:
                self.instrument.write(command)
                return
            except Exception:
                continue
        print("Aviso: nao consegui configurar wavelength via SCPI; confira no OPM.")

    def read_power_w(self) -> float:
        if self.backend == "tlpm":
            power = ctypes.c_double()
            status = getattr(self.tlpm, f"{self.tlpm_prefix}_measPower")(
                self.tlpm_session,
                ctypes.byref(power),
            )
            if status != 0:
                raise RuntimeError(f"TLPM_measPower falhou com status {status}")
            return float(power.value)

        response = self._query_first(["READ?", "MEAS:POW?", "MEASURE:POWER?"])
        return float(response.strip().split(",")[0])

    def read_average_w(self, samples: int, delay_s: float = 0.08) -> float:
        values = []
        for _ in range(samples):
            values.append(self.read_power_w())
            time.sleep(delay_s)
        return sum(values) / len(values)
