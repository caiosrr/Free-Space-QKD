# 07. Holograma de fase no DMD

Conferido contra o commit `c3e3413`, em 2026-10-01.

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

A força da turbulência é dada pelo **parâmetro de Fried** $r_0$: o tamanho
típico de uma "célula" da atmosfera. Ele é um **diâmetro**, não um raio: a
maior abertura sobre a qual a fase ainda varia cerca de 1 rad rms. A teoria de Kolmogorov diz que dois pontos a uma
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

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 138 a 151

```python
    fase = np.zeros_like(x)
    if estado.modo in ("o", "c"):
        fase = fase + fase_oam(x, y, estado.ell)
    if estado.modo in ("t", "c"):
        h, w = x.shape
        turb = tela.janela(x0 + int(estado.deslocamento), y0, w, h) * estado.r0 ** (-5.0 / 6.0)
        gx, gy = inclinacao(turb, x, y, np.hypot(x, y) <= estado.raio)
        estado.inclinacao_tela_mrad = (gx * MRAD_POR_RAD_PX, gy * MRAD_POR_RAD_PX)
        if estado.sem_inclinacao:
            turb = turb - gx * x - gy * y
        fase = fase + turb
    # Mira: uma inclinacao conhecida, para levar a +1 a um angulo escolhido.
    fase = fase + (estado.mira_x_mrad * x + estado.mira_y_mrad * y) / MRAD_POR_RAD_PX
    return fase
```

### A inclinação anda junto com a força

Multiplicar a tela inteira tem uma consequência que se vê na bancada. A maior
parte da fase de Kolmogorov numa abertura circular é **inclinação** (um plano):
pela decomposição de Noll (J. Opt. Soc. Am. 66, 207, 1976), da variância total
$1{,}03\,(D/r_0)^{5/3}$ sobram $0{,}134\,(D/r_0)^{5/3}$ quando a inclinação
sai, ou seja, ela é 87 % do total. Na tela da semente 0, na abertura de 94 px
em (856, 615), são 77 %.

Uma inclinação de fase desvia a ordem +1 inteira. Um gradiente $g$ (rad/px)
vira o ângulo

$$\theta = \frac{g\,\lambda}{2\pi p}$$

com $p = 5{,}4$ µm o passo dos espelhos. Confere com a portadora: $g = 2\pi/7$
dá $\lambda/(7p) = 16{,}7$ mrad, a separação entre ordens. Como a tela é
uma só, multiplicada por $r_0^{-5/6}$, a inclinação tem **direção fixa** e cresce
com a força: apertar `z` empurra a +1 sempre para o mesmo lado. Na tela da
semente 0, calculado:

| $r_0$ (px) | inclinação da tela (mrad, x e y do DMD) |
|---|---|
| 2000 | (−0,04; −0,01) |
| 23 | (−1,49; −0,53) |
| 15 | (−2,08; −0,74) |

Com o vento, a janela desliza e a inclinação muda: é a dança do ângulo de
chegada, justamente o que o tracker corrige. A tecla `i` tira a inclinação
média, e o que sobra é o que um tracker perfeito deixaria. A legenda mostra a
inclinação a cada quadro.

> Revisado por Caio: ainda não

**Para conferir**

1. Dobrar $r_0$ multiplica a fase por quanto?
2. Por que as frequências baixas da tela importam mais para o tracker que as
   altas?
3. Com vento de 30 px/s e a tela de 2048 px, depois de quanto tempo a
   turbulência se repete?
4. Por que tirar só a inclinação média não tira a turbulência toda?

---

## 5. O programa na bancada

O holograma é desenhado só dentro de uma **abertura** circular, que você
centra no feixe. Fora dela o DMD fica preto:

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 171 a 174

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
   o vento, `h` e `j` mudam a velocidade; `i` tira a inclinação; `n` sorteia
   outra tela.

### Vento pelo relógio

Até 2026-10-01 o vento andava 2 px por volta do laço, e o laço dava umas 16
voltas por segundo (33 ms para montar o holograma e a prévia, mais 30 ms de
espera). Na câmera, o padrão levava mais de 1 s para se renovar (roteiro,
"Primeira turbulência na bancada"). Agora a tela anda em px/s, medidos pelo
relógio, e a espera caiu para 1 ms:

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 359 a 368

```python
            tecla = cv2.waitKeyEx(1 if estado.vento else 0)
            agora = time.monotonic()
            dt, anterior = agora - anterior, agora
            if estado.vento and estado.modo in ("t", "c"):
                # A tela anda pelo relogio, nao por volta do laco: a velocidade
                # em px/s vale igual num PC lento e num rapido. O teto de 0,5 s
                # evita um salto depois de uma espera longa por tecla.
                estado.deslocamento += estado.vento_px_s * min(dt, 0.5)
                if dt > 0:
                    estado.quadros_por_s = 0.9 * estado.quadros_por_s + 0.1 / dt
```

A legenda mostra o tempo de coerência equivalente, $\tau_0 = 0{,}314\, r_0/v$:
o tempo que o vento leva para renovar a fase num pedaço do tamanho de $r_0$. O
teto é o laço: para o padrão mudar inteiro a cada quadro, o vento tem de andar
cerca de $r_0$ por quadro, por exemplo 20 px × 16 quadros/s ≈ 320 px/s. Mais
rápido que isso o HDMI não acompanha, e na atmosfera $\tau_0$ é de poucos ms.

### Mira: levar a +1 a um ângulo escolhido

As teclas `4 6 8 2` somam ao holograma uma inclinação conhecida, de 0,25 mrad
por toque em x ou y do DMD (`5` zera), em qualquer modo. Servem para duas
coisas: mapear até que ângulo a câmera ainda vê a +1 (o campo útil da
montagem), e reproduzir sem turbulência a inclinação que a legenda mostra, para
saber se uma mancha na câmera é a +1 desviada ou outra coisa.

As teclas `h j i 4 6 8 2 5` e o vento pelo relógio **ainda não rodaram na
bancada**; foram conferidos pelos testes.

### Guardar o alinhamento

Achar o centro, o raio, o período e o ângulo que funcionam leva tempo, e na
primeira sessão (2026-10-01) eles se perderam ao fechar o programa. Agora, ao
sair, o programa imprime o comando que volta a eles:

`Codigos/diversos/ferramentas/dmd_holograma.py`, linhas 379 a 383

```python
        # O alinhamento custa caro: sai impresso o comando que volta a ele.
        print("\nPara voltar a esta configuracao:")
        print("  .venv\\Scripts\\python.exe diversos\\ferramentas\\dmd_holograma.py "
              f"--monitor {args.monitor} --centro {estado.cx} {estado.cy} "
              f"--raio {estado.raio} --periodo {estado.periodo} --angulo {estado.angulo:g}")
```

A configuração que mostrou a ordem +1 e o anel do OAM em 2026-10-01:

```
.venv\Scripts\python.exe diversos\ferramentas\dmd_holograma.py --monitor 2 --centro 856 615 --raio 94 --periodo 7 --angulo 90
```

As opções `--raio`, `--periodo` e `--angulo` **ainda não rodaram na bancada**;
foram conferidas só pelo `--help` e pelos testes.

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
ângulo da ordem de $\lambda/(r_0 p)$: com $r_0 = 18$ px, 6,5 mrad, ou 2 cm nos
~3 m do DMD ao telescópio (medidos com trena em 2026-10-01). Cabe nos 10 cm do
telescópio. Com $r_0$ bem menor, deixa de caber, e a câmera veria só parte da
turbulência.

**O espelho de 1" também tem de caber.** Ele fica perto do meio do caminho,
~1,5 m do DMD. Com $r_0 = 18$ px a +1 chega a ele com ~1 cm, mais o desvio da
inclinação (seção 4). Com $r_0$ de poucos px, passa de 2,5 cm, e o espelho
corta: em 2026-10-01 a +1 turbulenta chegou maior que ele.

**O halo tem de ser menor que a separação entre ordens.** Na razão das duas,
$\lambda/(r_0 p)$ dividido por $\lambda/(\Lambda p)$, sobra $\Lambda/r_0$, com
$\Lambda$ o período da grade. Com $\Lambda = 7$ px e $r_0 = 8$ px, o halo da +1
já tem a largura da distância até a ordem 0, e nenhum espelho separa as duas.

**O campo da câmera é estreito.** O sensor da ASI585 tem 3840 × 2160 pixels de
4,1 µrad: 15,9 × 8,9 mrad. No lado curto, ±4,5 mrad em torno do centro, menos
que a separação entre ordens.

**Qual elemento deve cortar depende do que se simula.** Para o lado do tracker
(a câmera vendo uma fonte através da turbulência), a abertura no DMD já faz o
papel da pupila do telescópio, e tudo depois dela deveria passar inteiro. Para
o lado do CBPF (feixe de metros sobre uma abertura pequena), o corte deveria
ser na entrada do telescópio, com uma íris, e não no espelho do meio do
caminho, que corta em ângulo.

**A potência é pouca, mas basta.** No máximo ~10% da luz que chega à abertura
vai para a +1: com o HeNe de 0,9 mW, uns 90 µW antes das perdas do DMD.

---

## 7. O que precisa ser visto na bancada

Primeiro teste com o DMD e o laser em 2026-10-01 (HDMI, branco, Look 2,
abertura de 94 px em (856, 615), período de 7 px; ASI585 com ND 3,0). Na ordem
em que vale conferir:

1. As ordens ±1 aparecem no cartão onde a tabela da seção 1 prevê. **Conferido
   em 2026-10-01**: 0 no meio, ±1 dos lados, uma delas mais forte (a inclinação
   dos espelhos favorece um lado).
2. O anel do OAM aparece na câmera, e a ordem 0 não muda com ele. **Conferido em
   2026-10-01**: rosquinha com centro escuro em ℓ = 1, maior em ℓ = 2, um pouco
   torta; listras finas de interferência atravessam a mancha (reflexo no filtro
   ND ou na janela da câmera, a confirmar).
3. A ordem 0 não chega à câmera depois do espelho da segunda mesa. **Conferido
   em 2026-10-01**: ela passa ao lado do espelho de 1".
4. A prévia do campo distante bate com o que a câmera mostra.
5. A animação com vento roda fluida na tela do DMD. **Visto em 2026-10-01**:
   roda, mas lenta demais comparada ao enlace (o padrão leva mais de 1 s para
   se renovar); daí o vento pelo relógio da seção 5.
6. A turbulência aparece na câmera e na parede. **Visto em 2026-10-01**, com
   duas coisas ainda sem explicação conferida:
   - ao apertar `z`, uma segunda mancha, ~1,9 mrad acima da +1 na câmera,
     cresce aos poucos; as duas convivem, e no $r_0$ em que surge o aviso de
     período grosso ($r_0 \approx 15$ px) a de baixo some e a de cima fica
     forte. Ela não desliza, troca. A inclinação calculada da tela nesse $r_0$
     é de 2,2 mrad, do tamanho do salto. Teste: em modo `g`, levar a +1 com a
     mira até onde a legenda mostra a inclinação da tela, e ver se ela vai
     para a mancha de cima; e, em modo `t` com $r_0 \approx 15$ px, tirar a
     inclinação com `i` e ver se a mancha de baixo volta;
   - o centro da turbulência sobe na parede quando ela fica mais forte, o que
     a inclinação da tela explica (seção 4).
7. Com a mira, até que ângulo a +1 ainda aparece na câmera em cada direção
   (o campo útil da montagem).

---

## 8. A piscada dos espelhos e como medi-la

O DLPC3439 é um controlador de vídeo: cada quadro de 16,667 ms vira uma
sequência de planos de bit, cor por cor (vermelho 33 %, verde 47 % e azul 20 %
do tempo, lidos do controlador em 2026-09-30). Um pixel 255 deveria deixar o
espelho ligado em todos os planos. Medido em 2026-09-30 (roteiro, "DMD: por que
os espelhos piscam no vídeo HDMI"): pelo gerador interno, quase isso; pelo HDMI,
o verde fica ligado ~0,68 do tempo que deveria e o azul ~0,50, e o branco pulsa.

A medida usa o obturador rolante da ASI: com a luz do DMD espalhada sobre o
sensor inteiro e exposição de 32 µs, cada linha é um instante de tempo.

`Codigos/diversos/ferramentas/dmd_piscada.py`, linhas 50 a 66

```python
def medir_quadro(img: np.ndarray, bayer: bool = True) -> dict:
    """Fracao ligada e trocas por 100 linhas, num quadro com a luz cobrindo o sensor."""
    r = img.astype(np.float64)
    if r.ndim == 3:
        r = r.mean(axis=2)
    if bayer:
        r = r[0::2, 0::2]  # vermelho do RGGB: o HeNe so acende esse canal
    col = r.mean(axis=0)
    colunas = col > 0.5 * col.max()
    perfil = _suavizar(r[:, colunas].mean(axis=1), 3)
    env = _suavizar(_max_movel(perfil, 151), 151)
    uteis = env > 0.25 * env.max()
    ligado = float(perfil[uteis].sum() / env[uteis].sum())
    on = (perfil > 0.5 * env) & uteis
    trocas = float(np.sum(np.diff(on.astype(int)) != 0) / max(uteis.sum(), 1) * 100)
    return {"ligado": ligado, "trocas": trocas, "linhas": int(uteis.sum()),
            "saturados": float(np.mean(r >= 250))}
```

O número `ligado` depende da forma da mancha desfocada, então só vale comparado
com outro grupo de quadros **na mesma montagem**, de preferência o gerador
interno com a mesma cor.

Os testes por comando ficam em `Codigos/diversos/dmd_lotes/`, arquivos de lote
para a aba "Batch Files" da interface da TI (`w 36 <comando> <dados>` escreve,
`r 36 <n>` lê). Todos são voláteis: desligar o DMD volta tudo ao gravado na
flash. Nenhum mexe na flash.

> Revisado por Caio: ainda não
