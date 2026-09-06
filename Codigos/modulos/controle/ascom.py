"""Cliente HTTP unico do ASCOM Remote / Alpaca.

Objetivo: uma so implementacao de ``call`` para camera e mount.
Entradas/saidas: metodo HTTP e comando Alpaca; devolve o campo ``Value``.
Unidades: as do proprio driver (graus, segundos, contagens).
Hardware: ASCOM Remote Server. Nao move nada por conta propria.
Seguranca: erro do driver vira ``RuntimeError``; nunca e engolido.

Cada dispositivo tem sua sessao persistente e seu contador de transacao, como
o ASCOM espera. Antes esse mesmo codigo estava copiado em ``cameras/alpaca.py``,
``mount_ascom.py`` e ``tracker_camera.py``.
"""

from __future__ import annotations

import itertools

import requests

from modulos.configuracoes.alpaca import (
    ALPACA_ADDRESS,
    CAMERA_DEVICE_NUMBER,
    TELESCOPE_DEVICE_NUMBER,
)


class AlpacaDevice:
    """Um dispositivo Alpaca com sessao HTTP reutilizada."""

    def __init__(
        self,
        device_type: str,
        device_number: int,
        address: str = ALPACA_ADDRESS,
        client_id: int = 1,
    ) -> None:
        self.address = address
        self.device_type = device_type
        self.device_number = int(device_number)
        self.base_url = f"http://{address}/api/v1/{device_type}/{self.device_number}"
        self.client_id = int(client_id)
        self._transaction_ids = itertools.count(1)
        self.session = requests.Session()

    def call(self, method: str, command: str, timeout: float = 5.0, **extra_args):
        params = {
            "ClientID": self.client_id,
            "ClientTransactionID": next(self._transaction_ids),
        }
        params.update(extra_args.pop("params", {}))
        response = self.session.request(
            method,
            f"{self.base_url}/{command}",
            params=params,
            timeout=timeout,
            **extra_args,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ErrorNumber", 0):
            raise RuntimeError(f"{command}: {payload.get('ErrorMessage')}")
        return payload.get("Value")


camera_device = AlpacaDevice("camera", CAMERA_DEVICE_NUMBER)
telescope_device = AlpacaDevice("telescope", TELESCOPE_DEVICE_NUMBER)
