# 08. Medida conjunta UFF e CBPF

Conferido contra o commit `a2179ed`, em 2026-09-30.

Camada 2 da documentação. Cobre as duas peças do experimento que mede, na
outra ponta do enlace, o que o tracker faz: os **blocos com e sem correção** no
tracker da UFF, e o **registrador do CBPF**
(`programas_principais/registrar_cbpf.py`).

**Nada deste documento rodou ainda com o enlace.** Os blocos foram testados com
o laço de controle real e o mount simulado; o registrador, com imagens
sintéticas.

---

## 0. A pergunta

Até aqui, tudo que sabíamos sobre o tracker vinha **do sensor dele mesmo**, na
UFF. Isso tem um limite: medir o erro pelo próprio sensor do laço é
autorreferente. E há uma premissa nunca verificada, a **reciprocidade**: que
apontar o telescópio para o beacon do CBPF também aponte o feixe que sai da UFF
para o CBPF. Se o caminho de recepção e o de transmissão estiverem desalinhados
entre si, o tracker pode estar perfeitamente centrado no sensor dele enquanto o
feixe transmitido passeia para fora da fibra.

Uma câmera e um power meter no CBPF são uma testemunha **independente**. Com
eles se medem três coisas:

| pergunta | quem responde |
|---|---|
| o tracker melhora o acoplamento na fibra? | power meter, com e sem correção |
| quanto o apontamento da UFF muda a luz no CBPF? | `fluxo` da câmera e power meter |
| quanto o ângulo de chegada varia no CBPF? | posição da mancha na câmera do CBPF |
| a reciprocidade vale? | intensidade no CBPF nos blocos com correção |

> Revisado por Caio: ainda não

---

## 1. Os blocos com e sem correção

Comparar uma noite com tracker e outra sem não diz nada: a atmosfera muda de
uma noite para outra. A saída é alternar **na mesma noite**, em blocos de
15 minutos. Blocos vizinhos compartilham praticamente a mesma atmosfera.

`Codigos/modulos/configuracoes/tracker.py`, linhas 84 a 90

```python
# Blocos alternados COM e SEM correcao na mesma sessao, para medir o efeito do
# controle sem que a atmosfera mude entre as duas condicoes. No bloco sem
# correcao o laco continua medindo e gravando, mas nao comanda o mount em nada.
# O primeiro bloco corrige, para a sessao comecar centrada. Ligado pelo
# tracker.py com --blocos-minutos.
CORRECTION_BLOCKS_ENABLED = _chave("QKD_BLOCOS_CORRECAO", False)
CORRECTION_BLOCK_SECONDS = float(os.environ.get("QKD_BLOCOS_CORRECAO_S", "900"))
```

No laço de controle, o bloco é calculado pelo relógio da sessão; os pares
corrigem, os ímpares não:

`Codigos/modulos/controle/tracker_loop.py`, linhas 345 a 352

```python
                if CORRECTION_BLOCKS_ENABLED:
                    bloco_sc = int((loop_t0 - ab_started_at) // CORRECTION_BLOCK_SECONDS)
                    corrigindo = bloco_sc % 2 == 0
                    if bloco_sc != bloco_correcao:
                        bloco_correcao = bloco_sc
                        print()
                        print(f"Blocos: {'COM' if corrigindo else 'SEM'} correcao "
                              f"(bloco {bloco_sc})")
```

E num bloco sem correção só o comando vai a zero, pelo mesmo caminho de um
freio. O ciclo de pulso recebe `enabled=False`, que encerra na hora um pulso em
curso:

`Codigos/modulos/controle/tracker_loop.py`, linhas 573 a 578

```python
                # Bloco sem correcao: os estimadores seguem medindo, a deriva
                # acumulada entra na janela longa, e o primeiro bloco com
                # correcao a recolhe. So o comando e zerado, pelo mesmo caminho
                # de um freio, e um pulso em curso termina na hora.
                if not corrigindo:
                    target_cmd_az = target_cmd_alt = 0.0
```

Os estimadores **continuam medindo**. A deriva acumulada no bloco sem correção
entra na janela longa de 120 s, e o primeiro bloco com correção a recolhe. Por
isso a configuração exige blocos de pelo menos 240 s: com menos, a janela
longa nunca enche e o bloco com correção não chega a corrigir.

O freio de erro crescente não dispara à toa no bloco sem correção, embora o
erro cresça de propósito: ele só conta quando há comando saindo para o mount.

**Testado** com o laço real, o mount simulado e um erro grande o tempo todo:
nenhum comando sai no bloco sem correção, e a correção volta no seguinte
(`test_tracker_pulsos.py`, `BlocosDeCorrecaoTests`).

A telemetria ganha a coluna `correcao_ativa`, 1 ou 0, que é o que a análise usa
para separar os blocos.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que o primeiro bloco corrige, e não o contrário?
2. Num bloco sem correção de 15 minutos, quanto o feixe deve se afastar, pela
   deriva medida na UFF?
3. Por que zerar só o comando, em vez de pausar o laço inteiro?

---

## 2. O registrador do CBPF

Roda no PC do CBPF a noite toda. **Não conversa com a UFF**: cada lado grava
com o relógio do Windows sincronizado, e a análise cruza pelo horário. Por isso
cada linha começa com `t_unix`, o instante em segundos UTC, que é a mesma
escala nos dois PCs.

### A câmera do CBPF

Não é IDS: é uma **The Imaging Source DMK 27AUR0135**, monocromática, USB 3,
1280 × 960 em 8 bits, usada lá pelo IC Capture. O registrador a lê pelo
**DirectShow** do Windows, via OpenCV, sem SDK nenhum (`--camera dshow`, o
padrão). Exposição e ganho se ajustam na janela do próprio driver, aberta com
`--ajustes-camera`. Com a DMK, em 2026-09-29, a câmera
abriu (1280 × 960) mas **o ponto ainda não foi medido**: o teste respondeu "sem
ponto" com o feixe visível no IC Capture. Provavelmente a exposição do driver
não era a do IC Capture; ver o sinal, abaixo.

**Há uma lente grande antes do cubo divisor, e nenhuma entre o cubo e a
câmera** (a primeira parte informada pelo Caio em 2026-09-29, corrigindo a
leitura anterior deste documento). O feixe da UFF é focalizado por essa lente e
só então dividido entre a câmera e o acoplador da fibra. O centroide mede o
**ângulo de chegada**, não o deslocamento lateral: o raio que passa pelo centro
da lente chega inclinado de $	heta$ e cai a $d	heta$ do eixo, com $d$ a
distância da lente à câmera. É a grandeza que decide o acoplamento, porque a
ponta da fibra está no foco dessa lente.

**A câmera não está no foco.** Os quadros gravados na noite de 2026-09-30
mostram um disco de uns 760 px (cerca de 2,9 mm) com anéis de Fresnel e um pico
central com raias: a imagem desfocada da abertura do receptor. O "ponto" que o
`medir_ponto` segue é esse pico central, que anda junto com o disco. Duas
consequências: a escala em px por segundo de arco depende de $d$, não de $f$; e
o `sinal` e o `fluxo` medem o pico central, não toda a luz que chega.

Consequência: mover o mount da UFF muda **quanta** luz chega ao CBPF (onde o
feixe cai), mas não **onde** a mancha fica na câmera. O que move a mancha é o
ângulo de chegada, e ele varia com a refração atmosférica; ver o roteiro.

### O ponto na câmera

`Codigos/programas_principais/registrar_cbpf.py`, linhas 74 a 107

```python
def medir_ponto(quadro: np.ndarray, sinal_minimo: float = SINAL_MINIMO) -> dict | None:
    """Centroide do ponto mais brilhante, ignorando reflexos fantasmas.

    Tira o fundo pela mediana, suaviza para o ruido de pixel nao decidir onde
    esta o maximo, e pega so a mancha CONECTADA ao maximo acima de meia
    altura. Um reflexo separado, como os da estrutura da camera na bancada da
    USP, fica fora da conta mesmo passando da meia altura.
    """
    f = quadro.astype(np.float64)
    if f.ndim == 3:
        f = f.mean(axis=2)
    fundo = float(np.median(f))
    liquido = f - fundo
    suave = cv2.GaussianBlur(liquido, (0, 0), 2.0)
    pico_suave = float(suave.max())
    if pico_suave < sinal_minimo:
        return None
    mascara = (suave > 0.5 * pico_suave).astype(np.uint8)
    _, rotulos = cv2.connectedComponents(mascara)
    iy, ix = np.unravel_index(int(np.argmax(suave)), suave.shape)
    mancha = rotulos == rotulos[iy, ix]
    ys, xs = np.nonzero(mancha)
    pesos = np.clip(liquido[mancha], 0.0, None)
    if pesos.sum() <= 0:
        return None
    saturacao = 255 if quadro.dtype == np.uint8 else float(np.iinfo(quadro.dtype).max)
    return {
        "x_px": float(np.sum(xs * pesos) / pesos.sum()),
        "y_px": float(np.sum(ys * pesos) / pesos.sum()),
        "pico": float(f.max()),
        "fundo": fundo,
        "fluxo": float(pesos.sum()),
        "saturados": int((quadro >= saturacao).sum()),
    }
```

A escolha que importa é a **mancha conectada ao máximo**. Reflexos fantasmas,
como os que a estrutura da câmera faz na bancada da USP, podem passar da meia
altura, mas ficam separados da mancha principal e não entram na conta.
Testado: um fantasma a 60 px com 70% do pico desloca o centroide em menos de
0,3 px.

### Quando não há ponto

`Codigos/programas_principais/registrar_cbpf.py`, linhas 120 a 134

```python
def sinal_do_quadro(quadro: np.ndarray) -> tuple[float, float]:
    """Fundo e altura do maximo suavizado acima dele, haja ponto ou nao.

    Existe porque "sem ponto" nao diz se faltou pouco ou muito. Na primeira
    leitura da DMK, em 2026-09-29, o feixe aparecia no IC Capture e o
    registrador so respondia "sem ponto". Gravando esta altura sempre, da para
    ver a distancia ao limiar e acompanhar pela noite um feixe fraco demais
    para o centroide.
    """
    f = quadro.astype(np.float64)
    if f.ndim == 3:
        f = f.mean(axis=2)
    fundo = float(np.median(f))
    suave = cv2.GaussianBlur(f - fundo, (0, 0), 2.0)
    return fundo, float(suave.max())
```

A mesma suavização do `medir_ponto`, sem o limiar. Vai para a coluna `sinal` em
toda linha, haja ponto ou não, e o `--teste` mostra a distância ao limiar
(`sem ponto: sinal 4.2 de 10`); com ponto, mostra também o pico bruto e os
pixels saturados, que é o que se olha ao escolher a exposição. Com o feixe fraco, isso separa "faltou pouco"
de "não chega luz", e ainda acompanha a intensidade pela noite. O limiar é
`--sinal-minimo`. A potência do `--teste` sai na unidade do ruído, porque em
2026-09-29 o ruído de nW do power meter aparecia como `0.00 uW`.

### A luz do quadro inteiro

`Codigos/programas_principais/registrar_cbpf.py`, linhas 137 a 157

```python
def luz_total(quadro: np.ndarray, bloco: int = 8) -> float:
    """Luz do quadro inteiro acima do fundo, em contagens, medida em blocos.

    Existe porque a DMK do CBPF esta fora do foco (quadros de 2026-09-30): o
    laser vira um disco de ~760 px com aneis, e o `sinal` e o `fluxo` medem so o
    pico central. O disco fica poucas contagens acima de um fundo com ruido de
    ~6 por pixel, entao pixel a pixel ele se perde. Em blocos de 8 x 8 o ruido
    cai 8 vezes e o disco aparece: nos quadros daquela noite, o quadro sem
    laser (01:07) deu 8 mil contagens contra 190 mil a 970 mil com laser.
    Somam-se os blocos acima de 3 desvios (mediana dos desvios absolutos) da
    mediana; a parte mais fraca do disco fica de fora, entao a grandeza
    acompanha a luz que chega mas nao e a luz absoluta.
    """
    f = quadro.astype(np.float64)
    if f.ndim == 3:
        f = f.mean(axis=2)
    h, w = (f.shape[0] // bloco) * bloco, (f.shape[1] // bloco) * bloco
    blocos = f[:h, :w].reshape(h // bloco, bloco, w // bloco, bloco).mean(axis=(1, 3))
    acima = blocos - float(np.median(blocos))
    ruido = 1.4826 * float(np.median(np.abs(acima)))
    return float(acima[acima > 3.0 * max(ruido, 0.1)].sum() * bloco * bloco)
```

Vai para a coluna `luz_total`. É a medida de quanta luz chega que não depende de
onde o pico central cai: com a câmera fora do foco, o `sinal` e o `fluxo` medem
só esse pico. Testada nos quadros gravados em 2026-09-30, não ao vivo.

No CBPF a câmera vê o feixe antes da fibra, depois da lente do receptor. O
centroide é então o ângulo de chegada, e o `fluxo`, com o `sinal`, acompanha
quanta luz chega, que é o que o apontamento da UFF muda.

### O relógio

`Codigos/programas_principais/registrar_cbpf.py`, linhas 110 a 117

```python
def estado_do_relogio() -> str:
    """Saida do w32tm, para saber depois quao confiavel era o relogio."""
    try:
        return subprocess.run(["w32tm", "/query", "/status"], capture_output=True,
                              text=True, timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as exc:
        return f"w32tm indisponivel: {type(exc).__name__}: {exc}"
```

A saída do `w32tm` vai para os metadados no início e no fim da sessão. É o que
diz, depois, se os dois PCs estavam de fato sincronizados, e quanto. A precisão
necessária é folgada: o tempo de correlação da turbulência é de uns 7 s, então
meio segundo de erro já não atrapalha.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que não usar o maior pixel como posição do ponto?
2. Se o relógio do CBPF estiver 3 s adiantado, o que acontece com a comparação
   dos blocos de 15 minutos? E com a comparação quadro a quadro?

---

## 3. A noite, na prática

1. Nos dois PCs: `w32tm /resync`.
2. **CBPF**, fechados o IC Capture e o app da Thorlabs:
   `python programas_principais/registrar_cbpf.py --ajustes-camera --teste`,
   confere, e então `--horas 10`.
3. **UFF**: `python programas_principais/tracker.py --camera ids --horas 10
   --blocos-minutos 15`.
4. De manhã: `registro.csv` no CBPF e `telemetria.csv` na UFF, cruzados pelo
   instante: `t_unix` no CBPF, e na UFF a coluna `data_hora`, que já vem com
   fuso horário e milissegundos. Os blocos se separam por `correcao_ativa`.

A análise ainda não existe; ela vem depois da primeira noite, com os dados de
verdade na mão.
