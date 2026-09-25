# 07. Holograma de fase no DMD

Conferido contra o commit `615152c`, em 2026-09-25.

Camada 2 da documentação. Cobre `modulos/dmd/holograma.py`, a física, e
`diversos/ferramentas/dmd_holograma.py`, o programa de bancada. A base teórica
está no Saleh e Teich, capítulo 4 (óptica de Fourier) e seções 3.1 e 3.4
(feixes gaussianos e Laguerre-Gauss): se algum passo daqui parecer rápido, é
lá que ele está explicado.

**Nada deste documento rodou ainda no DMD.** Tudo foi conferido em simulação e
por testes automáticos (`diversos/testes/test_holograma_dmd.py`). A seção 7
lista o que precisa ser visto na bancada.

---

## 0. O problema

A turbulência é um efeito de **fase**: partes do feixe chegam atrasadas em
relação a outras, e a frente de onda fica enrugada. O DMD só sabe fazer
**amplitude binária**: cada espelho manda a luz para a saída útil ou não.

Não dá para escrever $e^{i\psi}$ diretamente no DMD. O truque, de Lee, é
esconder a fase na **posição** das linhas de uma grade de difração. O que o
DMD desenha continua sendo só 0 e 1; a informação está em onde as linhas
estão.

> Revisado por Caio: ainda não

---

## 1. A grade e as ordens

Uma grade reta é uma fase que avança $2\pi$ a cada período $\Lambda$:

`Codigos/modulos/dmd/holograma.py`, linhas 30 a 37

```python
def portadora(x: np.ndarray, y: np.ndarray, periodo_px: float, angulo_graus: float) -> np.ndarray:
    """Fase de uma grade reta: avanca 2*pi a cada periodo, na direcao do angulo.

    O angulo gira a grade, e com ela a direcao em que as ordens +1 e -1 saem.
    Periodo menor afasta mais as ordens: o angulo entre elas e lambda/periodo.
    """
    a = np.deg2rad(angulo_graus)
    return 2.0 * np.pi * (x * np.cos(a) + y * np.sin(a)) / float(periodo_px)
```

Tudo em **pixels do DMD**: $x$ e $y$ contados a partir do centro da abertura,
e o período em pixels. A luz que sai de uma grade se divide em **ordens**, em
ângulos fixos. A primeira sai desviada de

$$\theta_1 = \frac{\lambda}{\Lambda\,p}$$

com $p = 5{,}4$ µm o tamanho do espelho e $\lambda = 633$ nm. O ângulo da
grade gira a direção das ordens junto.

| período | ângulo entre ordens vizinhas | separação a 3 m |
|---|---|---|
| 4 px | 29,3 mrad | 8,8 cm |
| 6 px | 19,5 mrad | 5,9 cm |
| 8 px | 14,7 mrad | 4,4 cm |
| 10 px | 11,7 mrad | 3,5 cm |

A 3 m, um espelho de 1" pega uma ordem só com qualquer desses períodos. É ele
que faz o papel da íris do artigo.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que diminuir o período afasta as ordens?
2. Girar a grade 90° faz o quê com as ordens no cartão?

---

## 2. Escondendo a fase nas linhas

Desloque cada linha da grade localmente, de acordo com a fase $\psi(x,y)$ que
você quer, e binarize:

$$T = \tfrac12 + \tfrac12\,\mathrm{sgn}\!\left[\cos(u) + \cos(\arcsin A)\right], \qquad u = \frac{2\pi x}{\Lambda} + \psi$$

É a Eq. (1) de Cox e Drozdov. No código ela está numa forma equivalente, pela
fração do período que fica acesa:

`Codigos/modulos/dmd/holograma.py`, linhas 70 a 75

```python
    d = 1.0 - np.arcsin(np.clip(amplitude, 0.0, 1.0)) / np.pi
    # O arredondamento em 1e-9 de periodo tira o ruido da ida e volta por 2*pi
    # (0,25 virava 0,2499999...), que sozinho recriava os empates.
    ciclos = np.round((fase_portadora + fase) / (2.0 * np.pi), 9)
    posicao = np.mod(ciclos + d / 2.0, 1.0)
    return posicao < d
```

### Por que a ordem +1 carrega a fase

Para amplitude $A = 1$, a condição vira "acende onde $\cos u > 0$": uma onda
quadrada em $u$. A série de Fourier dela é

$$\mathrm{sgn}(\cos u) = \frac{4}{\pi}\left(\cos u - \frac{\cos 3u}{3} + \frac{\cos 5u}{5} - \dots\right)$$

e, escrevendo cada cosseno como soma de exponenciais,

$$T = \frac12 + \frac1\pi\left(e^{iu} + e^{-iu}\right) - \frac{1}{3\pi}\left(e^{3iu} + e^{-3iu}\right) + \dots$$

Cada termo $e^{imu}$ é uma **ordem** $m$, e como $u$ contém $\psi$, ela carrega
$e^{im\psi}$:

| ordem | carrega | uso |
|---|---|---|
| 0 | nada: é a média, $\tfrac12$ | descartar; é a mais forte e não tem a turbulência |
| **+1** | $e^{+i\psi}$ | **a que queremos** |
| −1 | $e^{-i\psi}$ | a conjugada |
| ±2 | não existem com meio período aceso | |
| ±3 | $e^{\pm 3i\psi}$: a fase triplicada | descartar |

Com $A < 1$ as linhas ficam mais largas, e a amplitude da ordem +1 cai
exatamente para $A/\pi$. A potência dela é no máximo $(1/\pi)^2 \approx 10\%$
da luz que chega à abertura.

**Conferido em teste**: com fases de 0,4, 1,3 e 2,9 rad, a ordem +1 sai com a
mesma fase, a −1 com a oposta e a 0 com zero, todas dentro de 0,01 rad; e a
amplitude da +1 bate com $A/\pi$ para $A$ = 0,3, 0,6 e 1.

### Duas armadilhas encontradas ao escrever o código

**Empates.** Com período par, alguns pixels caem exatamente onde o cosseno
vale zero, e o arredondamento do computador decidia ao acaso se ligavam. Com
período 8, isso acendia 49,2% dos espelhos em vez de 50%. A forma com a
fração do período, arredondada, torna a decisão determinística.

**A fase anda em degraus.** Uma linha só pode deslocar pixels inteiros. Com a
grade alinhada aos pixels, a fase só é representada em passos de
$2\pi/\Lambda$: com período 8, erro de até 0,36 rad numa fase uniforme.
Inclinando a grade, cada fileira arredonda num ponto diferente e os erros se
cancelam: a 30°, o erro cai para 0,001 rad. Com uma tela de turbulência de
verdade, a própria fase variando já faz esse papel, e o erro medido ficou
entre 0,02 e 0,05 rad rms **em qualquer ângulo**. Só importa para fases lisas.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que a ordem 0 não carrega fase nenhuma? Olhe o primeiro termo da série.
2. Se usássemos a ordem 3 por engano, a turbulência apareceria mais forte ou
   mais fraca? Quanto?
3. Por que as ordens pares somem quando exatamente metade do período está
   acesa?

---

## 3. Momento angular orbital

Uma fase que dá $\ell$ voltas em torno do centro:

`Codigos/modulos/dmd/holograma.py`, linhas 40 a 46

```python
def fase_oam(x: np.ndarray, y: np.ndarray, ell: int) -> np.ndarray:
    """Fase em helice: da uma volta de 2*pi*ell em torno do centro.

    Somada a portadora, as linhas da grade se bifurcam no centro (a "grade em
    forquilha"), e a ordem +1 sai com momento angular orbital ell.
    """
    return float(ell) * np.arctan2(y, x)
```

Somada à grade, as linhas se **bifurcam** no centro: a "grade em forquilha".
A ordem +1 sai com fase $e^{i\ell\theta}$, e no campo distante isso é um
**anel**, porque no centro a fase é indefinida e a intensidade tem de ser
zero. A −1 sai com $-\ell$, também um anel. A ordem 0 continua um ponto.

Isso dá o melhor teste de alinhamento que temos: **se o ponto na câmera vira
anel ao ligar o OAM, você está numa ordem ±1**. A ordem 0 não muda.

Para a turbulência, não importa se o espelho pegou a +1 ou a −1: a tela da
−1 é $-\psi$, e uma tela de Kolmogorov e sua negativa têm a mesma estatística.

> Revisado por Caio: ainda não

**Para conferir**

1. O que você esperaria ver na câmera com $\ell = 0$?
2. Por que o anel com $\ell = 3$ é maior que com $\ell = 1$?

---

## 4. A tela de turbulência

### O parâmetro: $r_0$

A força da turbulência é dada pelo **raio de Fried** $r_0$: o tamanho típico
de uma "célula" da atmosfera. A teoria de Kolmogorov diz que dois pontos a uma
distância $r$ têm fases que diferem, em média quadrática, por

$$D(r) = \langle [\psi(\mathbf{x} + \mathbf{r}) - \psi(\mathbf{x})]^2 \rangle = 6{,}88 \left(\frac{r}{r_0}\right)^{5/3}$$

Turbulência forte é $r_0$ **pequeno** comparado ao feixe. O número que resume
tudo é $D/r_0$, com $D$ a largura do feixe: no enlace da UFF, calculado a
partir do jitter medido, $D/r_0 \approx 0{,}4$, menos de uma célula na
abertura. O "pior caso" que queremos simular é $D/r_0$ de 5 a 10.

### Como a tela é gerada

Ruído gaussiano complexo, pesado pela raiz do espectro de potência de
Kolmogorov, $0{,}023\, r_0^{-5/3} f^{-11/3}$, e uma transformada de Fourier
inversa (Schmidt, cap. 9):

`Codigos/modulos/dmd/holograma.py`, linhas 106 a 112

```python
        df = 1.0 / self.n
        f1 = (np.arange(self.n) - self.n // 2) * df
        fx, fy = np.meshgrid(f1, f1)
        psd = _psd_kolmogorov(np.hypot(fx, fy), self.r0_px)
        cn = (rng.standard_normal(psd.shape) + 1j * rng.standard_normal(psd.shape)) * np.sqrt(psd) * df
        campo = np.fft.ifftshift(np.fft.ifft2(np.fft.ifftshift(cn))) * (self.n ** 2)
        self._alta = np.real(campo)
```

A FFT representa mal as frequências mais baixas, justamente as que mais
inclinam o feixe. Por isso se somam três níveis de **sub-harmônicos**: ondas
planas com frequências menores que a menor da FFT, cada uma com o peso do
espectro.

`Codigos/modulos/dmd/holograma.py`, linhas 115 a 125

```python
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
```

Medido, com 30 telas de 256 px e $r_0 = 16$ px:

| $r$ | com sub-harmônicos | sem |
|---|---|---|
| 2 px | 90% da teoria | 77% |
| 16 px ($= r_0$) | 84% | 57% |
| 32 px | 81% | 46% |

Os 81 a 90% são a precisão conhecida desse método. Sem os sub-harmônicos, a
metade da inclinação some nas escalas grandes, por isso eles ficam ligados.

### O vento

A parte da FFT é **periódica**: a tela se repete a cada 2048 pixels. Então
fazê-la andar, como a atmosfera levada pelo vento (a hipótese do "fluxo
congelado" de Taylor), é só ler outra janela dela:

`Codigos/modulos/dmd/holograma.py`, linhas 127 a 142

```python
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
```

### Mudar a força sem gerar outra tela

O espectro cresce como $r_0^{-5/3}$, então a fase cresce como $r_0^{-5/6}$. O
programa gera a tela uma vez, com $r_0 = 1$ px, e só multiplica:

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 105 a 112

```python
    fase = np.zeros_like(x)
    if estado.modo in ("o", "c"):
        fase = fase + fase_oam(x, y, estado.ell)
    if estado.modo in ("t", "c"):
        h, w = x.shape
        base = tela.janela(x0 + estado.deslocamento, y0, w, h)
        fase = fase + base * estado.r0 ** (-5.0 / 6.0)
    return fase
```

> Revisado por Caio: ainda não

**Para conferir**

1. Dobrar $r_0$ multiplica a fase por quanto?
2. Por que as frequências baixas da tela importam mais para o tracker que as
   altas?
3. Com vento de 2 px por quadro e a tela de 2048 px, depois de quanto tempo a
   turbulência se repete?

---

## 5. O programa na bancada

O holograma é desenhado só dentro de uma **abertura** circular, que você
centra no feixe. Fora dela o DMD fica preto:

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 132 a 135

```python
    mascara = np.hypot(x, y) <= r

    fase = fase_do_modo(estado, x, y, x0, y0, tela)
    ligados = holograma_lee(fase, portadora(x, y, estado.periodo, estado.angulo)) & mascara
```

Na tela principal aparecem a **fase** desenhada e o **campo distante**
simulado, que é o que a lente faz com o holograma, com a ordem +1 marcada.

**E o campo distante simulado é uma previsão da câmera.** Com o telescópio
focado no infinito, a câmera mostra a intensidade em função do **ângulo**, e
isso não muda enquanto a luz propaga do DMD até o telescópio. Então a região
da ordem +1 na prévia é o que deve aparecer na câmera. A escala: com a ASI585MC
(pixel de 2,9 µm) e focal de 700 mm, 1 pixel da câmera são 4,1 µrad.

### Alinhamento, passo a passo

1. **Modo `g`**, abertura sobre o feixe (`w a s d`). No cartão aparecem a
   ordem 0, forte, e as ±1 dos lados. Gire a grade (`,` e `.`) até elas
   ficarem na altura certa para a segunda mesa.
2. O espelho da segunda mesa pega **só a +1** e a devolve ao telescópio.
3. **Modo `o`**: se o ponto na câmera vira anel, você está na ordem certa.
4. **Modo `t` ou `c`**: a turbulência entra. `z` e `x` mudam a força; `v` liga
   o vento; `n` sorteia outra tela.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que o DMD fica preto fora da abertura, e não branco?
2. A prévia do campo distante mostra a ordem 0 muito mais forte que a +1. Na
   câmera, depois do espelho da segunda mesa, a ordem 0 deveria aparecer?

---

## 6. Limites práticos

**O feixe define quantas células cabem.** O HeNe tem ~1 mm, ou ~185 espelhos.
Para $D/r_0 = 10$, $r_0 \approx 18$ px. Para mais células, é preciso expandir o
feixe antes do DMD.

**O período tem de ser menor que as células.** A fase precisa variar devagar
comparada às linhas da grade, senão a +1 não consegue representá-la. O
programa avisa quando o período passa de $r_0/3$. Com $r_0 = 18$ px, período
de 6 px ou menos.

**O espalhamento tem de caber no telescópio.** A turbulência abre o feixe num
ângulo da ordem de $\lambda/(r_0 p)$: com $r_0 = 18$ px, 6,5 mrad, ou 4 cm a
6 m. Cabe nos 10 cm do telescópio. Com $r_0$ bem menor, deixa de caber, e a
câmera veria só parte da turbulência.

**O espelho de 1" também tem de caber.** Com turbulência forte a própria ordem
+1 se abre: com $r_0 = 18$ px, uns 2 cm a 3 m, quase o espelho inteiro. Aí o
período de 6 px, que afasta as ordens vizinhas 5,9 cm, deixa folga.

**A potência é pouca, mas basta.** No máximo ~10% da luz que chega à abertura
vai para a +1: com o HeNe de 0,9 mW, uns 90 µW antes das perdas do DMD.

---

## 7. O que precisa ser visto na bancada

Nada disto rodou com o DMD e o laser. Na ordem em que vale conferir:

1. As ordens ±1 aparecem no cartão onde a tabela da seção 1 prevê.
2. O anel do OAM aparece na câmera, e a ordem 0 não muda com ele.
3. A ordem 0 não chega à câmera depois do espelho da segunda mesa.
4. A prévia do campo distante bate com o que a câmera mostra.
5. A animação com vento roda fluida na tela do DMD.
