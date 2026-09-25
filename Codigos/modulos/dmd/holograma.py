"""Hologramas binarios para o DMD: fase escondida na posicao das linhas de uma grade.

O DMD so liga e desliga espelhos, e a turbulencia e um efeito de FASE. O truque,
de Lee, e desenhar uma grade de difracao cujas linhas estao localmente
deslocadas pela fase desejada. A ordem +1 dessa grade carrega exatamente
exp(i * fase); a ordem 0 nao carrega fase nenhuma; a -1 carrega a conjugada.

Este modulo e so matematica: nao sabe nada de monitor nem de janela. Tudo e
medido em PIXELS DO DMD, e as fases em radianos.

Referencias:
    Cox e Drozdov, "Converting a Texas Instruments DLP4710 DLP evaluation module
    into a spatial light modulator", Applied Optics, Eq. (1).
    Schmidt, "Numerical Simulation of Optical Wave Propagation", SPIE Press,
    cap. 9, para a tela de Kolmogorov com sub-harmonicos.
"""

from __future__ import annotations

import numpy as np


def coordenadas(largura: int, altura: int, cx: float, cy: float) -> tuple[np.ndarray, np.ndarray]:
    """Coordenadas de cada pixel em relacao ao centro (cx, cy), em pixels."""
    x = np.arange(largura, dtype=np.float64) - cx
    y = np.arange(altura, dtype=np.float64) - cy
    return np.meshgrid(x, y)


def portadora(x: np.ndarray, y: np.ndarray, periodo_px: float, angulo_graus: float) -> np.ndarray:
    """Fase de uma grade reta: avanca 2*pi a cada periodo, na direcao do angulo.

    O angulo gira a grade, e com ela a direcao em que as ordens +1 e -1 saem.
    Periodo menor afasta mais as ordens: o angulo entre elas e lambda/periodo.
    """
    a = np.deg2rad(angulo_graus)
    return 2.0 * np.pi * (x * np.cos(a) + y * np.sin(a)) / float(periodo_px)


def fase_oam(x: np.ndarray, y: np.ndarray, ell: int) -> np.ndarray:
    """Fase em helice: da uma volta de 2*pi*ell em torno do centro.

    Somada a portadora, as linhas da grade se bifurcam no centro (a "grade em
    forquilha"), e a ordem +1 sai com momento angular orbital ell.
    """
    return float(ell) * np.arctan2(y, x)


def holograma_lee(fase: np.ndarray, fase_portadora: np.ndarray,
                  amplitude: np.ndarray | float = 1.0) -> np.ndarray:
    """Liga (True) ou desliga (False) cada espelho.

    Eq. (1) de Cox e Drozdov:

        T = 1/2 + 1/2 * sgn{ cos(portadora + fase) + cos[arcsin(A)] }

    Com A = 1 (so fase), cos[arcsin(1)] = 0 e sobra "acende onde o cosseno e
    positivo": linhas de meio periodo, deslocadas pela fase. Com A < 1 as
    linhas ficam mais largas e a ordem +1 enfraquece ali, o que da controle de
    amplitude ponto a ponto.

    Implementada numa forma equivalente, pela fracao do periodo que fica acesa:
    a condicao acima vale quando a fase total cai a menos de pi - arcsin(A) de
    um multiplo de 2*pi, ou seja, numa fracao d = 1 - arcsin(A)/pi do periodo.
    Escrever como "a posicao dentro do periodo e menor que d" resolve os
    empates: com periodo par, alguns pixels caem EXATAMENTE onde o cosseno vale
    zero, e o arredondamento decidia ao acaso se ligavam. Com periodo de 8 isso
    dava 49,2% de espelhos ligados em vez de 50%.
    """
    d = 1.0 - np.arcsin(np.clip(amplitude, 0.0, 1.0)) / np.pi
    # O arredondamento em 1e-9 de periodo tira o ruido da ida e volta por 2*pi
    # (0,25 virava 0,2499999...), que sozinho recriava os empates.
    ciclos = np.round((fase_portadora + fase) / (2.0 * np.pi), 9)
    posicao = np.mod(ciclos + d / 2.0, 1.0)
    return posicao < d


class TelaKolmogorov:
    """Tela de fase aleatoria com a estatistica de Kolmogorov, em pixels.

    O parametro que define a forca e r0, o raio de Fried: o tamanho tipico de
    uma "celula" da atmosfera. Pela teoria, a funcao de estrutura da fase e

        D(r) = < [fase(x + r) - fase(x)]^2 > = 6,88 (r / r0)^(5/3)

    ou seja, dois pontos a uma distancia r0 diferem em 6,88 rad^2 em media.
    Turbulencia forte e r0 PEQUENO comparado ao feixe.

    Metodo de Schmidt, cap. 9: ruido gaussiano complexo pesado pela raiz do
    espectro de potencia de Kolmogorov, 0,023 r0^(-5/3) f^(-11/3), e uma
    transformada de Fourier. A FFT subamostra as frequencias baixas, justamente
    as que mais pesam na inclinacao do feixe, e por isso se somam tres niveis de
    sub-harmonicos.

    A parte da FFT e periodica em n pixels, entao transladar a tela (para
    anima-la como vento, o "fluxo congelado" de Taylor) e so ler outra janela.
    Os sub-harmonicos sao ondas planas explicitas e acompanham a translacao.
    """

    def __init__(self, n: int, r0_px: float, semente: int | None = None,
                 subharmonicos: bool = True) -> None:
        self.n = int(n)
        self.r0_px = float(r0_px)
        rng = np.random.default_rng(semente)

        df = 1.0 / self.n
        f1 = (np.arange(self.n) - self.n // 2) * df
        fx, fy = np.meshgrid(f1, f1)
        psd = _psd_kolmogorov(np.hypot(fx, fy), self.r0_px)
        cn = (rng.standard_normal(psd.shape) + 1j * rng.standard_normal(psd.shape)) * np.sqrt(psd) * df
        campo = np.fft.ifftshift(np.fft.ifft2(np.fft.ifftshift(cn))) * (self.n ** 2)
        self._alta = np.real(campo)

        self._ondas: list[tuple[float, float, complex]] = []
        if subharmonicos:
            for p in (1, 2, 3):
                dfp = 1.0 / (3 ** p * self.n)
                for i in (-1, 0, 1):
                    for j in (-1, 0, 1):
                        if i == 0 and j == 0:
                            continue
                        f = np.hypot(i * dfp, j * dfp)
                        amp = np.sqrt(_psd_kolmogorov(np.array(f), self.r0_px)) * dfp
                        c = complex(rng.standard_normal(), rng.standard_normal()) * float(amp)
                        self._ondas.append((i * dfp, j * dfp, c))

    def janela(self, x0: int, y0: int, largura: int, altura: int) -> np.ndarray:
        """Fase (rad) num retangulo que comeca em (x0, y0), sem o piston."""
        ix = (int(x0) + np.arange(largura)) % self.n
        iy = (int(y0) + np.arange(altura)) % self.n
        fase = self._alta[np.ix_(iy, ix)].copy()
        if self._ondas:
            x = int(x0) + np.arange(largura, dtype=np.float64)
            y = int(y0) + np.arange(altura, dtype=np.float64)
            baixa = np.zeros((altura, largura), dtype=np.complex128)
            for fx, fy, c in self._ondas:
                # exp(i(a+b)) = exp(ia) exp(ib): produto externo de duas linhas,
                # em vez de uma exponencial por pixel. Uma ordem de grandeza
                # mais rapido, e e o que deixa a animacao fluida.
                baixa += c * np.outer(np.exp(2j * np.pi * fy * y), np.exp(2j * np.pi * fx * x))
            fase += np.real(baixa)
        return fase - fase.mean()


def _psd_kolmogorov(f: np.ndarray, r0: float) -> np.ndarray:
    """Espectro de potencia da fase, com a frequencia zero anulada."""
    f = np.asarray(f, dtype=np.float64)
    with np.errstate(divide="ignore"):
        psd = 0.023 * r0 ** (-5.0 / 3.0) * f ** (-11.0 / 3.0)
    return np.where(f > 0.0, psd, 0.0)


def funcao_de_estrutura(fase: np.ndarray, r_px: int) -> float:
    """< [fase(x + r) - fase(x)]^2 >, nas duas direcoes, para conferir a tela."""
    dx = fase[:, r_px:] - fase[:, :-r_px]
    dy = fase[r_px:, :] - fase[:-r_px, :]
    return float(0.5 * (np.mean(dx ** 2) + np.mean(dy ** 2)))
