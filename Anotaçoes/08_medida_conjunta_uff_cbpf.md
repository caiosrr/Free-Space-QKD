# 08. Medida conjunta UFF e CBPF

Conferido contra o commit `36c13b5`, em 2026-09-25.

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
| quanto o feixe da UFF anda no CBPF? | câmera do CBPF, se ela vê o feixe antes da fibra |
| a reciprocidade vale? | câmera do CBPF nos blocos com correção |

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

### O ponto na câmera

`Codigos/programas_principais/registrar_cbpf.py`, linhas 62 a 95

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

Se a câmera vê o feixe **antes** da fibra, o centroide é a posição do feixe da
UFF no CBPF. Se ela vê a **saída** da fibra, o centroide fica parado e o que
importa é o `fluxo`, que acompanha o acoplamento.

### O relógio

`Codigos/programas_principais/registrar_cbpf.py`, linhas 98 a 105

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
2. **CBPF**, fechados o IDS peak Cockpit e o app da Thorlabs:
   `python programas_principais/registrar_cbpf.py --teste`, confere, e então
   `--horas 10`.
3. **UFF**: `python programas_principais/tracker.py --camera ids --horas 10
   --blocos-minutos 15`.
4. De manhã: `registro.csv` no CBPF e `telemetria.csv` na UFF, cruzados pelo
   instante: `t_unix` no CBPF, e na UFF a coluna `data_hora`, que já vem com
   fuso horário e milissegundos. Os blocos se separam por `correcao_ativa`.

A análise ainda não existe; ela vem depois da primeira noite, com os dados de
verdade na mão.
