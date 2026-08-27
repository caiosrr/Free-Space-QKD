"""Estudo 01: reconstruir o centro de massa usado para localizar um beacon.

Este arquivo usa somente imagens sinteticas. Nao acessa camera nem mount.
Complete as tres funcoes marcadas com TODO e execute o arquivo para conferir.
"""

from __future__ import annotations

import numpy as np


# =============================================================================
# EXERCICIO 1 — DEFINICAO DIRETA COM LACOS
# =============================================================================


def centro_de_massa_loops(frame: np.ndarray) -> tuple[float, float] | None:
    """Retorna (x_cm, y_cm), ou None quando nao existe intensidade positiva."""
    massa_total = 0
    soma_x = 0
    soma_y = 0
    for y in range(frame.shape[0]):
        for x in range(frame.shape[1]):
            intensidade = frame[y, x]
            if intensidade <= 0:
                continue
            massa_total += intensidade
            X = x*intensidade
            Y = y*intensidade
            soma_x += X
            soma_y += Y

    if massa_total <= 0:
        return None
    else:
        x_cm = soma_x / massa_total
        y_cm = soma_y / massa_total
        return (x_cm, y_cm)


# =============================================================================
# EXERCICIO 2 — REMOVER O FUNDO COM UM THRESHOLD RELATIVO
# =============================================================================


def aplicar_limiar_relativo(frame: np.ndarray, fracao: float) -> np.ndarray:
    """Zera pixels abaixo de `fracao * maior_intensidade` e preserva os demais."""
    if fracao < 0 or fracao > 1:
        raise ValueError(f"fracao deve estar entre 0 e 1; obtido {fracao}")
    frame_modificado = frame.astype(float, copy=True)
    maior_intensidade = np.max(frame)
    for y in range(frame.shape[0]):
        for x in range(frame.shape[1]):
            intensidade = frame[y, x]
            if intensidade < maior_intensidade * fracao:
                frame_modificado[y, x] = 0
    return frame_modificado



# =============================================================================
# EXERCICIO 3 — A MESMA CONTA, AGORA VETORIZADA COM NUMPY
# =============================================================================


def centro_de_massa_numpy(frame: np.ndarray) -> tuple[float, float] | None:
    """Calcula o mesmo CM do exercicio 1 sem percorrer os pixels explicitamente."""
    intensidade = np.where(frame > 0, frame, 0).astype(float)
    massa_total = intensidade.sum()
    if massa_total <= 0:
        return None
    y, x = np.indices(frame.shape)
    Y, X = (y*intensidade).sum(), (x*intensidade).sum()
    y_cm, x_cm = Y/massa_total, X/massa_total
    return (x_cm, y_cm)

# =============================================================================
# BANCADA SINTETICA E VERIFICACOES
# =============================================================================


def criar_gaussiana(
    altura: int,
    largura: int,
    x0: float,
    y0: float,
    sigma: float = 2.0,
    amplitude: float = 100.0,
) -> np.ndarray:
    """Cria um spot gaussiano com centro conhecido, inclusive subpixel."""
    yy, xx = np.indices((altura, largura), dtype=np.float64)
    raio_quadrado = (xx - x0) ** 2 + (yy - y0) ** 2
    return amplitude * np.exp(-raio_quadrado / (2.0 * sigma**2))


def _proximo(valor: float, esperado: float, tolerancia: float = 1e-9) -> bool:
    return abs(valor - esperado) <= tolerancia


def verificar_exercicio_1() -> None:
    vazio = np.zeros((4, 5), dtype=float)
    if centro_de_massa_loops(vazio) is not None:
        raise AssertionError("Um frame vazio deve retornar None.")

    ponto = np.zeros((4, 5), dtype=float)
    ponto[1, 3] = 10.0
    resultado = centro_de_massa_loops(ponto)
    if resultado is None or not (_proximo(resultado[0], 3.0) and _proximo(resultado[1], 1.0)):
        raise AssertionError(f"Um ponto em frame[1, 3] deve dar (x=3, y=1); obtido: {resultado}")

    pesos = np.zeros((3, 5), dtype=float)
    pesos[1, 1] = 1.0
    pesos[1, 3] = 3.0
    resultado = centro_de_massa_loops(pesos)
    if resultado is None or not (_proximo(resultado[0], 2.5) and _proximo(resultado[1], 1.0)):
        raise AssertionError(f"O caso ponderado deve dar (2.5, 1.0); obtido: {resultado}")


def verificar_exercicio_2() -> None:
    frame = np.array([[0.0, 2.0, 4.0, 8.0]])
    filtrado = aplicar_limiar_relativo(frame, 0.5)
    esperado = np.array([[0.0, 0.0, 4.0, 8.0]])
    if not np.array_equal(filtrado, esperado):
        raise AssertionError(f"Threshold incorreto. Esperado {esperado}; obtido {filtrado}")
    if np.shares_memory(frame, filtrado):
        raise AssertionError("A funcao deve devolver uma copia, sem alterar o frame original.")


def verificar_exercicio_3() -> None:
    spot = criar_gaussiana(61, 71, x0=34.25, y0=27.75, sigma=2.5)
    cm_loops = centro_de_massa_loops(spot)
    cm_numpy = centro_de_massa_numpy(spot)
    if cm_loops is None or cm_numpy is None or not np.allclose(cm_loops, cm_numpy, atol=1e-9):
        raise AssertionError(f"Loops e NumPy devem concordar: loops={cm_loops}, numpy={cm_numpy}")
    if not np.allclose(cm_numpy, (34.25, 27.75), atol=1e-6):
        raise AssertionError(f"O CM deve recuperar o centro sintetico (34.25, 27.75): {cm_numpy}")


def executar_verificacoes() -> None:
    etapas = [
        ("1 — centro de massa com loops", verificar_exercicio_1),
        ("2 — threshold relativo", verificar_exercicio_2),
        ("3 — centro de massa com NumPy", verificar_exercicio_3),
    ]
    print("Estudo 01 — centro de massa\n")
    for nome, verificacao in etapas:
        try:
            verificacao()
        except NotImplementedError as exc:
            print(f"[PENDENTE] {nome}: {exc}")
        except Exception as exc:
            print(f"[REVER]    {nome}: {exc}")
        else:
            print(f"[OK]       {nome}")


if __name__ == "__main__":
    executar_verificacoes()
