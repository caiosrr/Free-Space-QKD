"""Mapa seguro de calibracoes locais entre movimento Alt-Az e pixels.

Cada no representa a Jacobiana local ``[dx, dy] = A @ [dAz, dAlt]`` em uma
posicao absoluta do mount. Uma consulta so e aceita dentro do raio validado de
um ou mais nos. Quando regioes validas se sobrepoem, as matrizes ``A`` sao
interpoladas e a inversa e recalculada; nunca se interpola ``A_inv`` diretamente.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


MAX_CONDITION_NUMBER = 100.0


def diferenca_az_graus(alvo_deg: float, atual_deg: float) -> float:
    """Menor diferenca assinada ``alvo - atual`` no intervalo [-180, 180)."""
    return float((float(alvo_deg) - float(atual_deg) + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class NoJacobiana:
    nome: str
    azimute_deg: float
    altitude_deg: float
    A: np.ndarray
    A_inv: np.ndarray
    rms_residual_px: float
    condition_number: float
    raio_validado_deg: float

    def distancia_deg(self, azimute_deg: float, altitude_deg: float) -> float:
        daz = diferenca_az_graus(self.azimute_deg, azimute_deg)
        dalt = self.altitude_deg - float(altitude_deg)
        return float(np.hypot(daz, dalt))


@dataclass(frozen=True)
class SelecaoJacobiana:
    A: np.ndarray
    A_inv: np.ndarray
    nome: str
    distancia_mais_proxima_deg: float
    nos_usados: tuple[str, ...]
    pesos: tuple[float, ...]
    condition_number: float


def _matriz_2x2(valor, campo: str) -> np.ndarray:
    matriz = np.asarray(valor, dtype=float)
    if matriz.shape != (2, 2) or not np.all(np.isfinite(matriz)):
        raise ValueError(f"{campo} precisa ser uma matriz 2x2 finita.")
    return matriz


def _carregar_no(item: dict, indice: int) -> NoJacobiana:
    nome = str(item.get("name") or item.get("nome") or f"no_{indice:03d}")
    A = _matriz_2x2(item.get("A"), f"{nome}.A")
    condition = float(np.linalg.cond(A))
    if condition > MAX_CONDITION_NUMBER:
        raise ValueError(f"{nome}: matriz mal condicionada (cond={condition:.2f}).")

    A_inv_calculada = np.linalg.inv(A)
    if item.get("A_inv") is not None:
        A_inv_salva = _matriz_2x2(item["A_inv"], f"{nome}.A_inv")
        if not np.allclose(A_inv_salva, A_inv_calculada, rtol=2e-3, atol=1e-9):
            raise ValueError(f"{nome}: A_inv salva nao corresponde a inversa de A.")

    raio = float(item.get("validated_radius_deg", item.get("raio_validado_deg", 0.0)))
    if not np.isfinite(raio) or raio <= 0.0:
        raise ValueError(f"{nome}: raio validado precisa ser positivo.")

    return NoJacobiana(
        nome=nome,
        azimute_deg=float(item["azimuth_deg"]),
        altitude_deg=float(item["altitude_deg"]),
        A=A,
        A_inv=A_inv_calculada,
        rms_residual_px=float(item.get("rms_residual_px", 0.0)),
        condition_number=condition,
        raio_validado_deg=raio,
    )


class MapaJacobianas:
    """Colecao de Jacobianas locais com recusa explicita de extrapolacao."""

    def __init__(self, nos: list[NoJacobiana], *, origem: str | None = None):
        if not nos:
            raise ValueError("O mapa precisa conter ao menos um no valido.")
        self.nos = tuple(nos)
        self.origem = origem

    @classmethod
    def carregar(cls, caminho: str | Path) -> "MapaJacobianas":
        path = Path(caminho)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("version", 1)) != 1:
            raise ValueError("Versao de mapa de Jacobianas nao suportada.")
        itens = payload.get("nodes", payload.get("nos"))
        if not isinstance(itens, list):
            raise ValueError("O mapa precisa possuir uma lista 'nodes'.")
        nos = [_carregar_no(item, indice) for indice, item in enumerate(itens, 1)]
        return cls(nos, origem=str(path))

    def selecionar(self, azimute_deg: float, altitude_deg: float) -> SelecaoJacobiana | None:
        """Seleciona/interpola apenas nos cujo raio validado cobre a posicao."""
        cobertos: list[tuple[NoJacobiana, float]] = []
        for no in self.nos:
            distancia = no.distancia_deg(azimute_deg, altitude_deg)
            if distancia <= no.raio_validado_deg:
                cobertos.append((no, distancia))

        if not cobertos:
            return None

        cobertos.sort(key=lambda item: item[1])
        mais_proximo, menor_distancia = cobertos[0]
        exatos = [(no, distancia) for no, distancia in cobertos if distancia <= 1e-12]
        if len(exatos) == 1:
            return SelecaoJacobiana(
                A=mais_proximo.A.copy(),
                A_inv=mais_proximo.A_inv.copy(),
                nome=f"mapa:{mais_proximo.nome}",
                distancia_mais_proxima_deg=menor_distancia,
                nos_usados=(mais_proximo.nome,),
                pesos=(1.0,),
                condition_number=mais_proximo.condition_number,
            )

        if exatos:
            cobertos = exatos
            pesos = np.full(len(cobertos), 1.0 / len(cobertos), dtype=float)
        elif len(cobertos) == 1:
            return SelecaoJacobiana(
                A=mais_proximo.A.copy(),
                A_inv=mais_proximo.A_inv.copy(),
                nome=f"mapa:{mais_proximo.nome}",
                distancia_mais_proxima_deg=menor_distancia,
                nos_usados=(mais_proximo.nome,),
                pesos=(1.0,),
                condition_number=mais_proximo.condition_number,
            )
        else:
            # So entram nos que declararam esta posicao dentro da propria regiao
            # validada. O peso normalizado pela distancia suaviza a troca de no.
            pesos_brutos = np.asarray(
                [1.0 / max(distancia / no.raio_validado_deg, 1e-9) for no, distancia in cobertos],
                dtype=float,
            )
            pesos = pesos_brutos / pesos_brutos.sum()

        A = sum(peso * no.A for peso, (no, _) in zip(pesos, cobertos))
        condition = float(np.linalg.cond(A))
        if not np.all(np.isfinite(A)) or condition > MAX_CONDITION_NUMBER:
            return None

        return SelecaoJacobiana(
            A=A,
            A_inv=np.linalg.inv(A),
            nome="mapa:interpolada[" + ",".join(no.nome for no, _ in cobertos) + "]",
            distancia_mais_proxima_deg=menor_distancia,
            nos_usados=tuple(no.nome for no, _ in cobertos),
            pesos=tuple(float(peso) for peso in pesos),
            condition_number=condition,
        )


def registrar_no(
    caminho: str | Path,
    *,
    nome: str,
    azimute_deg: float,
    altitude_deg: float,
    A,
    A_inv,
    rms_residual_px: float,
    raio_validado_deg: float,
    metadata: dict | None = None,
) -> Path:
    """Acrescenta atomicamente uma calibracao aprovada ao mapa.

    Repeticoes na mesma posicao sao preservadas de proposito para permitir
    avaliar reprodutibilidade. Como suas regioes se sobrepoem, o seletor usa a
    media ponderada das Jacobianas aprovadas naquele local.
    """
    path = Path(caminho)
    matriz = _matriz_2x2(A, f"{nome}.A")
    inversa = _matriz_2x2(A_inv, f"{nome}.A_inv")
    if not np.allclose(inversa, np.linalg.inv(matriz), rtol=2e-3, atol=1e-9):
        raise ValueError("A_inv nao corresponde a inversa de A.")
    if float(raio_validado_deg) <= 0.0:
        raise ValueError("O raio validado precisa ser positivo.")

    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Valida integralmente o mapa anterior antes de altera-lo.
        MapaJacobianas.carregar(path)
    else:
        payload = {"version": 1, "created_epoch": time.time(), "nodes": []}

    item = {
        "name": str(nome),
        "created_epoch": time.time(),
        "azimuth_deg": float(azimute_deg) % 360.0,
        "altitude_deg": float(altitude_deg),
        "A": matriz.tolist(),
        "A_inv": inversa.tolist(),
        "rms_residual_px": float(rms_residual_px),
        "condition_number": float(np.linalg.cond(matriz)),
        "validated_radius_deg": float(raio_validado_deg),
        "metadata": dict(metadata or {}),
    }
    payload.setdefault("nodes", []).append(item)
    payload["updated_epoch"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".novo")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
