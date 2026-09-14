# O caminho de uma sessão do tracker

Escrito para quem quer saber **onde procurar**, não para quem vai ler o código
inteiro. São 25 mil linhas no repositório; este documento cobre o caminho que
uma sessão percorre e diz qual arquivo faz cada coisa.

Leia uma vez inteiro. Depois use como índice.

---

## A pergunta que o sistema responde

Um laser no CBPF, a 7 km, aponta para o telescópio na UFF. A imagem dele no
sensor precisa ficar parada num ponto escolhido, por horas, sem ninguém olhando.

Três coisas atrapalham:

- **turbulência atmosférica** — move a imagem uns 1,3 px a cada frame, em torno
  da posição verdadeira. É ruído: não adianta corrigir, ela volta sozinha.
- **deriva** — o apontamento sai do lugar devagar, uns 0,1 a 0,4 px por minuto.
  É isso que precisa ser corrigido.
- **a luz muda** — o céu clareia ao amanhecer, o beacon pisca, embarcações na
  baía cortam o feixe por minutos.

O sistema inteiro existe para separar deriva de turbulência e corrigir só a
primeira, enquanto mantém o beacon visível.

---

## Visão geral: três laços rodando ao mesmo tempo

```
                    ┌─────────────────────────────────┐
   câmera  ──────>  │  AQUISIÇÃO       ~36 Hz         │
                    │  vê, mede, decide a exposição   │
                    └───────────────┬─────────────────┘
                                    │ escreve em
                                    ▼
                            ┌───────────────┐
                            │ TrackerState  │  memória compartilhada
                            └───────┬───────┘
                                    │ lido por
                    ┌───────────────┴─────────────────┐
                    │  CONTROLE        ~34 Hz         │
                    │  decide se corrige e comanda    │  ──────>  mount
                    └─────────────────────────────────┘

                    ┌─────────────────────────────────┐
                    │  VIGIA           1 Hz           │
                    │  lê a posição do mount e para   │
                    │  a sessão se algo fugir         │
                    └─────────────────────────────────┘
```

Os três são *threads* independentes. Conversam apenas pelo `TrackerState`, que
é um objeto protegido por um cadeado (`state.lock`) para os três não escreverem
ao mesmo tempo. Nenhum chama o outro diretamente.

Isso importa: o laço de controle **nunca espera a câmera**. Se um frame demora,
o controle continua com a última medida válida e, se ela envelhecer demais,
para o mount.

---

## Parte 1: a preparação (antes de qualquer laço)

Arquivo: `modulos/controle/tracker_sessao.py`, função `main`.

Em ordem:

1. **Conecta o mount** e garante que ele está desestacionado e **sem rastreio
   sideral** (`ensure_not_tracking`). O alvo é terrestre e fixo; rastreio faria
   o mount fugir dele.

2. **Conecta a câmera** e carrega a **máscara de pixels ruins**, se existir.
   Ela vem de `programas_principais/diagnosticar.py --calibrar-pixels` e marca
   pixels defeituosos do sensor. Com o beacon em poucas dezenas de contagens,
   um pixel quente desloca o centro de massa.

3. **Carrega a matriz de calibração** (`carregar_matriz_calibracao`). É uma
   matriz 2×2 que converte movimento angular do mount em deslocamento na
   imagem. Sem ela não há como traduzir "o beacon está 2 px à esquerda" em
   "gire tantos graus".

4. **Compara a escala da calibração com a óptica configurada**. Se discordarem
   mais de 10%, avisa no terminal. Hoje discordam por 1,70×, uma questão em
   aberto do banco óptico.

5. **Você escolhe a ilha.** Aparece o sensor inteiro e você clica no beacon.
   O sistema guarda a **assinatura** dessa ilha: intensidade, área, largura,
   altura, forma. É por essa assinatura que ele vai reconhecê-la depois de uma
   perda, e é ela que impede o tracker de travar numa janela iluminada de
   prédio.

6. **Recorta a ROI** ao redor da ilha (256×256 px na IDS) e reancora a máscara
   de pixels ruins nesse recorte.

7. **Abre a telemetria** (`tracker_telemetria.py`), que grava um CSV com 93
   colunas a cada iteração, e o painel web.

8. **Dispara as threads** de controle e vigia, e entra no laço de aquisição.

---

## Parte 2: o laço de aquisição (o que vê)

Arquivo: `modulos/controle/tracker_aquisicao.py`, função `executar_aquisicao`.
Roda a ~36 Hz. Para cada frame:

### 2.1 Captura e encontra a ilha

`modulos/visao/detector_ilhas.py` recebe o frame e procura regiões acima de um
limiar. Entre os candidatos, escolhe o que **combina com a assinatura travada**
e está perto da última posição conhecida. Se nenhum combinar, não há alvo neste
frame.

### 2.2 Julga a aparência

`OpticalQualityGate` compara a ilha achada com a assinatura original. Se a área
dobrou, a intensidade despencou ou a forma mudou, o frame é **rejeitado sem
parar a sessão**: provavelmente foi turbulência forte ou uma nuvem fina
passando. O mount fica parado até a aparência normalizar.

### 2.3 Acumula a média temporal de 2 s

Esta é a medida que importa. Os frames aceitos são **somados** numa janela
deslizante de 2 segundos, e o centro de massa é medido na **imagem somada**.

Por que somar e não tirar a média dos centroides: somar dá mais peso aos frames
com mais sinal e é menos sensível a um frame ruim.

Por que 2 s: o centroide de um frame isolado pula ±1,3 px por turbulência.
Em 2 s isso promedia e sobra a posição.

**É este número, e só ele, que o controle recebe.**

### 2.4 Ajusta a exposição

`modulos/controle/tracker_exposicao.py`. Mede três coisas num anel ao redor do
beacon: o pico, a mediana do céu e o ruído do céu. Com elas calcula o

```
CNR = (pico − céu) / ruído do céu
```

e age:

| CNR | ação |
|---|---|
| < 8 | sobe a exposição 10% |
| 8 a 16 | não faz nada |
| > 16 | desce a exposição 5% |

Dois limites impedem que ele escolha uma exposição onde o próprio CNR deixa de
ser confiável: um **piso de sinal** (não desce se o beacon está a menos de 30
contagens acima do céu) e um **teto de fundo** (não sobe se o céu já está perto
da saturação).

Se o alvo sumir, há um comportamento especial descrito na Parte 5.

### 2.5 Publica

Escreve tudo no `TrackerState`, grava a linha do CSV e atualiza o painel.

---

## Parte 3: o laço de controle (o que age)

Arquivo: `modulos/controle/tracker_loop.py`, função `executar_loop_controle`.
Roda a ~34 Hz. Não toca na câmera.

### 3.1 Estima o desvio real

A média de 2 s ainda tem turbulência. Ela alimenta uma **mediana de 120 s**
(`SlowBiasEstimator`, em `tracker_controle.py`). Com ~30 amostras independentes
na janela, a incerteza dessa estimativa cai para ~0,24 px.

**Essa mediana é o que decide.** A média de 2 s não vai direto para a correção.

### 3.2 Decide se corrige

`SlowCorrectionGate`, com histerese:

- **dispara** quando a mediana passa de 0,6 px
- **solta** quando cai abaixo de 0,25 px

Entre os dois, mantém o estado anterior. Isso evita ligar e desligar a correção
a cada flutuação.

### 3.3 Monta o pulso

O mount tem **uma única velocidade utilizável** para correção fina:
0,001042 °/s. Ele não anda mais devagar. Então corrigir não é "mova X pixels",
é **"ande por T milissegundos"**:

```
duração = (fração × erro estimado) / velocidade_mínima
```

A **fração** é 0,35: cada pulso tenta remover um terço do desvio estimado.
Com a escala atual, 1 px equivale a 102 ms de pulso.

Duas travas antes de enviar, em `tracker_pulsos.py`:

- o pulso é **bloqueado** se a média de 2 s discorda do **sinal** da mediana
  longa ("não empurre contra o que a câmera está vendo agora")
- a duração fica entre 22 ms e 120 ms, ou seja entre 0,22 px e 1,18 px. Na
  prática o piso é maior: o laço roda a ~34 Hz medidos, e o pulso só pode
  terminar numa borda de laço, então a menor correção real vale ~0,29 px

### 3.4 Envia e desconta

Sai `MoveAxis(eixo, ±0,001042)`, espera T, sai `MoveAxis(eixo, 0)`.

Terminado o pulso, a janela de 120 s é **deslocada** pelo movimento aplicado:
cada amostra guardada leva o mesmo desconto que o mount executou. A mediana
passa a descrever o estado corrigido no mesmo instante.

Isso não é detalhe. Na primeira versão a janela era **descartada** após cada
pulso, e reconstruí-la levava 60 s. Com correções a cada ~80 s, o controlador
ficava cego 77% do tempo e o erro crescia sem ninguém olhando.

---

## Parte 4: o vigia (o que protege)

Arquivo: `modulos/controle/tracker_seguranca.py`, função `monitorar_posicao`.
Roda a 1 Hz e lê a posição do mount pelo encoder.

Encerra a sessão se:

| | limite |
|---|---|
| deslocamento absoluto em azimute ou altitude | 5° |
| tempo máximo da sessão | o que você pediu |
| mount sem responder | após N leituras falhas |

Há ainda dois freios dentro do laço de controle:

- **freio de movimento manual** — alguém encostou no telescópio: salto grande
  na imagem, o controle zera e espera
- **freio de erro crescente** — está comandando e o erro só aumenta: para

E, fora do processo, `programas_principais/parar_mount.py`, feito para ser
tarefa do Windows na inicialização. O `MoveAxis` do ASCOM não tem prazo: se o
PC morrer no meio de um pulso, o eixo anda até alguém mandar zero.

---

## Parte 5: quando o beacon some

Duas causas opostas, com respostas opostas. O sistema as distingue pelo **CNR
do último alvo confiável**, congelado no instante da perda:

| CNR antes da perda | diagnóstico | resposta |
|---|---|---|
| alto (saudável) | **oclusão** — embarcação na baía | congela tudo e espera 150 s |
| baixo (< 10) | **falta de exposição** | sobe a exposição em degraus após 8 s |

Subir a exposição não traz de volta um feixe bloqueado; só estraga a cena. Esse
foi o erro que matou uma sessão: a busca levou o fundo de 23 para 255 contagens
em 23 s e o beacon deixou de ter contraste em qualquer lugar do quadro.

Durante a espera **nada é descartado**: o mount fica parado, a assinatura e a
posição alvo continuam guardadas, a exposição congelada. A câmera segue
capturando e procurando. Quando o feixe volta, são precisos 5 frames válidos
consecutivos e a média de 2 s se reconstrói: medido, 2,4 a 3,7 segundos.

O limite para desistir é de 10 minutos.

---

## Onde fica cada coisa

| arquivo | responsabilidade |
|---|---|
| `programas_principais/tracker.py` | lançador; só lê argumentos |
| `modulos/controle/tracker_sessao.py` | prepara tudo e dispara as threads |
| `modulos/controle/tracker_aquisicao.py` | laço da câmera |
| `modulos/visao/detector_ilhas.py` | acha a ilha e trava a identidade |
| `modulos/controle/tracker_exposicao.py` | decide a exposição pelo CNR |
| `modulos/controle/tracker_loop.py` | laço de controle |
| `modulos/controle/tracker_controle.py` | estimadores e porta de correção |
| `modulos/controle/tracker_pulsos.py` | monta e limita o pulso |
| `modulos/controle/mount_ascom.py` | comandos crus do mount |
| `modulos/controle/tracker_seguranca.py` | vigia e paradas |
| `modulos/controle/tracker_telemetria.py` | CSV e resumo |
| `modulos/controle/tracker_estado.py` | a memória compartilhada |
| `modulos/configuracoes/tracker.py` | **todos os limiares, com o porquê** |
| `modulos/calibracao/calibracao_continua_core.py` | a calibração |

Quando quiser saber por que um número é o que é, comece pelo
`modulos/configuracoes/tracker.py`. Cada constante tem, no comentário, a medida
de onde ela saiu.

---

## Três coisas que valem saber de cor

1. **A média de 2 s mede; a mediana de 120 s decide; o micropulso age.** Se
   entender só isso, entendeu o controle.

2. **O mount tem uma velocidade só.** Tudo o que se controla é *por quanto
   tempo* ele anda. Daí vem o piso de 0,29 px, o teto de 1,18 px por pulso, e o
   fato de o limiar de 0,6 px não poder ser muito menor: ele já é só
   2,1 vezes o menor pulso que o mount consegue dar.

3. **Turbulência não se corrige.** Ela é ruído de média zero e volta sozinha.
   Todo o esforço do sistema é não confundi-la com deriva.
