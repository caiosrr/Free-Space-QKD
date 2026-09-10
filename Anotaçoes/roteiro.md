# Roteiro da próxima utilização do tracker

Data prevista: **29/08/2026**.

Objetivo: validar primeiro o tracker novo com zona de repouso de **1,0/2,0 px**
e correção fina por micropulsos. Somente depois decidir se vale mudar para
**1,0/1,5 px**.

## 1. Atualizar e preparar

1. No computador da UFF, abrir a pasta `Free-Space-QKD` no VS Code.
2. Conferir se não há uma execução antiga do tracker ou do IDS peak aberta.
3. No terminal, executar:

   ```powershell
   git status --short
   git pull --ff-only origin main
   git log -1 --oneline
   ```

4. Confirmar que o commit `df89dae` ou um commit mais novo aparece no `git log`.
5. Conferir alimentação do computador, suspensão desativada, conexão do mount,
   câmera IDS e espaço livre em disco.
6. Manter inicialmente a exposição que estiver mostrando o beacon com boa
   separação do fundo e sem saturação.

## 2. Não recalibrar antes do primeiro teste

Usar primeiro a mesma matriz que funcionou na sessão de 6 horas. Isso permite
avaliar apenas a mudança do controlador.

Recalibrar antes somente se alguma geometria tiver mudado desde o último teste:

- rotação ou reposicionamento da câmera;
- alteração do telescópio, espelhos ou óptica;
- desmontagem do conjunto câmera-mount;
- matriz ausente;
- correção claramente no sentido errado.

Um erro residual próximo do centro, sozinho, não significa que a matriz esteja
errada.

## 3. Teste curto obrigatório — 20 a 30 minutos

1. Abrir `Codigos/programas_principais/tracker.py` e executar pelo botão de play.
2. Escolher câmera `2` para IDS.
3. Para 20 minutos, informar `0,333` hora. Para 30 minutos, informar `0,5` hora.
4. Selecionar manualmente e travar a ilha correta.
5. Responder `s` para executar o autoteste temporário de recuperação.
6. Acompanhar o autoteste:

   - ele deve deslocar o beacon aproximadamente 10 px;
   - deve recuperar erro menor ou igual a 1 px por três medições;
   - possui timeout de 60 s;
   - quando aprovado, **não deve encerrar**: a sessão normal deve continuar;
   - se falhar, o tracker deve parar e retornar à posição inicial.

7. Durante a sessão, observar:

   - se os micropulsos aproximam o erro para menos de 1 px;
   - se cada pulso é seguido por cerca de 2 s de espera pela nova média;
   - se não aparecem correções rápidas alternando de direção;
   - se o mount permanece parado durante perda de sinal ou anomalia óptica;
   - se não ocorre afastamento contínuo do centro.

Em qualquer comportamento perigoso, usar `Ctrl+C`. O tracker deve parar os eixos
e executar o retorno seguro à posição absoluta inicial.

## 4. Avaliar antes de qualquer sessão longa

Guardar o nome da pasta mais recente em
`Codigos/Link UFF/resultados/tracker/sessoes/` e analisar:

- motivo de encerramento e sucesso do retorno seguro;
- aprovação do autoteste;
- porcentagem de sinal válido;
- erro mediano, P95 e máximo;
- porcentagem das medições abaixo de 1 px e de 1,5 px;
- quantidade e duração total dos comandos do mount;
- presença de oscilações ou comandos alternados;
- comportamento durante perdas de sinal e anomalias ópticas.

Resultado desejável para manter `1,0/2,0 px`:

- autoteste aprovado;
- erro mediano próximo ou abaixo de 1 px;
- P95 próximo ou abaixo de 1,5 px;
- poucos comandos, sem oscilação;
- nenhuma correção durante sinal rejeitado;
- retorno seguro confirmado.

## 5. Decidir os próximos limites

### Manter `1,0/2,0 px`

Manter essa configuração se o teste já concentrar bem o erro em torno do centro
e os micropulsos funcionarem de forma suave.

### Testar `1,0/1,5 px`

Considerar essa mudança se o erro permanecer frequentemente entre 1 e 2 px,
com o mount parado por muito tempo e sem sinais de oscilação. Depois da mudança,
repetir obrigatoriamente o teste curto antes de uma sessão longa.

### Não iniciar sessão longa

Não prosseguir se houver:

- autoteste reprovado;
- afastamento do centro;
- ultrapassagens repetidas ou correções alternadas;
- movimentos durante perda de sinal;
- retorno seguro com falha.

Nesse caso, preservar a telemetria e revisar os micropulsos ou a calibração.

## 6. Sessão longa

Somente depois de um teste curto aprovado:

1. Repetir a seleção manual da ilha.
2. Usar novamente o autoteste antes da sessão.
3. Definir a duração desejada.
4. Confirmar energia, suspensão desativada e espaço em disco.
5. Evitar atravessar amanhecer ou entardecer enquanto a exposição automática
   ainda não estiver implementada.
6. Ao terminar, preservar `telemetria.csv`, `resumo.json` e imagens de eventos.

## 7. Desenvolvimento seguinte — controle lento buscando zero

Depois de validar os micropulsos com a zona `1,0/2,0 px`, estudar uma malha em
duas escalas:

1. Manter a média robusta de 2 s para detecção, segurança e recuperação de
   deslocamentos maiores.
2. Calcular em paralelo uma estimativa lenta da posição média, inicialmente com
   janelas de 5, 10, 15, 20 e 30 s.
3. Usar essa estimativa para separar deriva persistente de turbulência rápida.
4. Manter o alvo matemático em zero, mas não deixar o mount continuamente em
   movimento: acumular evidência e enviar um micropulso somente quando a deriva
   for confiável e superar a resolução útil do mount.
5. Perto do centro, usar o estimador lento. Para erro grande ou recuperação,
   continuar usando a média de 2 s.
6. Antes de enviar movimentos, executar um modo sombra que apenas registre:

   - erro lento em X e Y;
   - duração da persistência;
   - momentos em que uma correção seria elegível;
   - direção e amplitude hipotéticas;
   - comportamento durante perda de sinal e anomalia óptica.

7. Comparar o modo sombra com os resultados do tracker `1,0/2,0 px` usando erro
   mediano, P95, tempo de atuação, inversões de comando e deslocamento acumulado.
8. Só depois executar um teste limitado com o mount.

Importante: aumentar uma única janela não basta. Uma janela longa reduz a
turbulência, mas aumenta o atraso. A proposta é combinar resposta rápida e
estimativa lenta, não substituir toda a medição de 2 s por uma média muito
atrasada.

A análise sombra da sessão de 6 h fica em
`Arquivos/Tracker 6h 2026-08-26/Controle zero sombra/`. Ela identifica onde o
erro lento permaneceu deslocado, mas não prevê quantos comandos seriam enviados:
uma correção real alteraria todas as medições posteriores.

## Ideias trazidas do tracker externo (2026-09-07)

Comparacao com o `Tracker amnd`, que resolve o mesmo problema com estrategia
diferente: erro em metros no alvo, pulso de duracao fixa no eixo dominante,
serial LX200 direto, sem matriz de calibracao. O controle dele e mais fraco que
o nosso (sem matriz, so um eixo por vez, sem watchdog nem retorno seguro, media
de 6 frames contra a janela de 2 s). O que ele tem de melhor e a infraestrutura
em volta. Foram adotadas as IDEIAS, com implementacao propria.

### Implementado

* **Mascara de pixels defeituosos** (`modulos/visao/pixels_ruins.py`). Medida
  com o feixe bloqueado, marca hot/cold a 5 sigma sobre desvio robusto e
  substitui cada defeito pela mediana 3x3 vetorizada. Verificado ponta a ponta:
  no regime real (pico 15, fundo 2) **um pixel quente de apenas 25 contagens ja
  faz o detector PERDER o alvo**, porque a normalizacao divide pelo maximo do
  frame e rebaixa a mancha abaixo do limiar. Nao e erro de centro, e ausencia.
  Isso e compativel com os 72 episodios curtos de ausencia em 5,9 h.
* **Escala fisica** (`modulos/configuracoes/optica.py`). pixel/EFL/distancia dao
  arcsec/px e cm/px no alvo; a telemetria ganha `distancia_alvo_m`. Os limiares
  do controle seguem em pixels, amarrados a resolucao do mount. `avisos()`
  denuncia explicitamente milimetro escrito onde se esperava metro.
* **`diagnosticar.py`**: sinal, fundo, amplitude, SNR, saturacao, fracao da
  escala do sensor em uso, taxa, **duty cycle de fotons** e dispersao do
  centroide. Aceita varias exposicoes e compara. E a ferramenta que responde o
  item 1 sem gastar uma sessao.
* **Incerteza do centroide por frame** (`sigma_centroide_px`, raio RMS sobre a
  raiz do sinal integrado). Separa "mancha larga por turbulencia" de "sinal
  fraco demais para localizar o centro"; a dispersao empirica ja registrada
  mede o efeito combinado.

### Adiado de proposito

Precisam de bancada acompanhada, entao nao entram numa sessao desassistida:

* **Re-aquisicao automatica.** A deles chama auto-deteccao cega, que pega o
  maximo global do sensor: na nossa cena, com fachadas, travaria em qualquer
  luz. A versao correta aqui e re-aquisicao pela ASSINATURA da ilha ja travada,
  que temos e eles nao. Mexe no tratamento de perda de sinal.
* **ROI auto-curativa.** Recentralizar a ROI perto da borda exige rebasear o
  alvo e a ancora para o novo sistema de coordenadas. E o tipo de mudanca que
  falha em silencio deslocando tudo por um offset.
* **Produtor/consumidor na aquisicao.** As notas ja pedem medir antes de mudar
  a arquitetura; a referencia deles e uma fila de 4 com descarte.
* **Hot-reload de configuracao.** Util em sessao de 6 h, mas hoje as constantes
  sao importadas diretamente por cada modulo; tornar isso recarregavel e um
  refactor amplo.

## Analise das sessoes do tracker (2026-09-06)

Levantamento sobre as sete sessoes de setembro em
`Codigos/Link UFF/resultados/tracker/sessoes/`. A referencia principal e a
sessao de 5,9 h `tracker_2026-09-04_23-44-30`, a primeira com o controle por
pulsos limitados.

### O controle novo funcionou

| | 09-04 00:04 (anterior) | 09-04 23:44 (pulsos limitados) |
|---|---|---|
| duracao | 6,5 h | 5,9 h |
| erro mediano | 1,80 px | **1,02 px** |
| P95 | 5,78 px | **2,07 px** |
| abaixo de 2 px | 55,8% | **93,9%** |
| tempo comandando o mount | 5,18% | **0,04%** |

Erro menor movendo o mount cerca de 100x menos. Na sessao longa nao houve
deriva acumulada: X = -0,002 px/h e Y = +0,027 px/h, com o mount deslocando ao
todo 4 arcsec em Az e 11 arcsec em Alt. Sinal valido em 98,6% do tempo.

### 1. A exposicao descarta 95% dos fotons sem ganhar nada

Prioridade mais alta, e a mais barata de testar.

* exposicao mediana de 1154 us num periodo de laco de 26 ms: **duty cycle de
  4,4%**;
* pico do beacon em 15 de 255, ou seja 5,7% da escala do sensor.

A estrategia atual e "menor exposicao que mantenha CNR, para preservar FPS".
Essa premissa foi testada contra a telemetria e **e falsa neste caso**: de 1154
para 18000 us (16x), o laco fica em 36-38,5 Hz, com correlacao r = -0,05 entre
exposicao e taxa. O laco e limitado por processamento e pelas chamadas HTTP do
ASCOM, nao pela exposicao. A 50 fps o periodo do sensor e 20 ms, entao subir a
exposicao para cerca de 10 ms nao custaria taxa nenhuma.

Ressalva: os dados NAO provam que o centroide melhoraria. Os trechos de
exposicao alta coincidem com o beacon enfraquecendo na madrugada (o pico cai de
15 para 10 conforme a exposicao sobe, ou seja causalidade invertida), entao
estao confundidos. Se o erro atual e dominado por fotons ou por turbulencia
segue em aberto.

**Teste que resolve, cerca de 20 min e sem mover o mount:** rodar
`caracterizar_beacon_ids.py` duas vezes, a ~1200 us e a ~10000 us, e comparar a
dispersao do centroide. Se cair, mexer na autoexposicao (o alvo de CNR, nao o
teto de 18000 us, que ja esta dimensionado para o periodo de 50 fps). Se nao
cair, o erro e atmosferico e a exposicao atual esta correta.

### 1b. A autoexposicao nao recupera de uma queda rapida

Em regime estavel nao ha risco de ficar fraca demais: o controlador mira CNR
entre 8 e 16 e ficou em 12,8 na sessao de 5,9 h, meio da faixa. O piso de
1000 us nunca foi atingido (minimo observado 1154 us).

O problema e a assimetria numa queda de brilho:

* subir a exposicao anda 10% por atualizacao, a cada 5 s, ou seja no maximo
  4,2x dentro dos 75 s do limite de perda de sinal;
* pior, quando o alvo some a exposicao **congela** (`congelada_alvo_ausente`).
  Nos ultimos 200 s da sessao foram 385 linhas congeladas contra 527
  ajustando. Se a queda derrubar o alvo antes de a exposicao ter subido, o
  controlador espera um alvo que nao consegue ver porque a exposicao esta
  baixa: nao ha caminho de volta e a sessao morre em 75 s.

No amanhecer de 09-04 isso nao mordeu porque o desvanecimento levou uns 4 min e
o controlador acompanhou (1154 -> 8054 -> 14268 -> 18000 us) ate o beacon
realmente acabar. O encerramento foi correto.

O modo de falha e "a sessao termina cedo", nao movimento perigoso: na perda o
mount para e nao ha busca cega.

Mitigacoes, em ordem de preferencia:

1. **Subir o piso da exposicao** (mesmo ajuste do item 1). Com ~8000 us em vez
   de 1154 sobra cerca de 7x de margem antes de perder o alvo numa queda, alem
   de recuperar os fotons hoje descartados. Um ajuste resolve os dois.
2. Nao congelar a exposicao na ausencia do alvo: fazer uma rampa de busca
   limitada para cima, revertendo quando o alvo voltar.
3. Deixar a subida bem mais rapida que a descida. Hoje ja e assimetrico (10%
   para cima contra 5% para baixo), mas 10% ainda e lento demais para uma
   queda abrupta.

### 2. Vies parado dentro da zona de repouso

Na sessao de 5,9 h existe um deslocamento constante de +0,58 px em Y a noite
inteira. Removendo so esse vies, o erro mediano cairia de 1,02 para 0,78 px
(-23%).

Nao e desalinhamento fixo: em sete sessoes o vies fica entre 0,03 e 0,23 px na
maioria, e chega a 0,60-1,04 px em duas. A explicacao que fecha e que **e o
residuo da zona de repouso**: o controle para assim que o erro entra em
`HOLD_ENTER_RADIUS_PX = 1.0` e fica onde parou por horas. Os dois viesses
observados sao menores ou iguais a 1,0 px. Na sessao antiga, agressiva, havia
correcao suficiente para o residuo se diluir, ao custo de um erro bem pior.

O mount consegue corrigir nessa escala. Nos 89 ciclos de correcao da sessao:

* erro antes do pulso 2,29 px, depois 1,42 px;
* **90% dos pulsos reduziram o erro**;
* deslocamento optico por pulso: mediana 1,11 px, p10 0,39 px.

Isso tambem aposenta a duvida deixada pelo diagnostico de micropulsos da
calibracao (0/8 resolvidos): aquilo falhou por julgar UM pulso contra ~2 px de
ruido, nao por incapacidade mecanica do mount.

Nao baixar `HOLD_ENTER_RADIUS_PX` no escuro: com passo minimo de 0,4 a 1,1 px,
mirar em 0,5 px fica no limite e pode oscilar numa sessao longa. O ganho e real
mas modesto (~0,25 px). O caminho correto e o modo sombra da secao 7 deste
roteiro, que agora tem evidencia de que encontraria algo.

### 3. Eventos de ausencia curta poluem o log

72 episodios de alvo ausente em 5,9 h, com mediana de 0,26 s; apenas 4 passam
de 5 s. Mesmo assim cada um gera evento: 18 imagens salvas e 176 suprimidas
pelo intervalo minimo de 30 s. Valeria so tratar como evento uma ausencia acima
de cerca de 1 s.

### Ordem sugerida

1. Medir a dispersao do centroide em duas exposicoes com o mount parado.
2. Filtrar eventos de ausencia curta.
3. Vies da zona de repouso, e somente via modo sombra, depois de uma
   calibracao aprovada: uma matriz confiavel muda o quanto se pode confiar nos
   pulsos pequenos.

## Referência física aproximada

Com a calibração usada no enlace de 7 km:

- 1 px corresponde a aproximadamente 1,04 arcsec;
- 1 px equivale geometricamente a cerca de 3,5 cm no plano a 7 km;
- 1,5 px equivale a cerca de 5,3 cm;
- 2 px equivale a cerca de 7,1 cm.

Esses valores representam uma estimativa de erro de apontamento. A perda óptica
real também depende da divergência do feixe, abertura, alinhamento entre os
canais e acoplamento na fibra.

## Sessão 2026-09-09 06:07–09:09 (3h02 de 5h previstas)

Primeira sessão longa com o piso de sinal, a busca por alvo ausente e o modo
sombra ativos ao mesmo tempo. Ela atravessou o amanhecer inteiro sem perder o
beacon e morreu depois, por um defeito da própria busca.

### O que funcionou

- **Amanhecer:** de 06:07 às 08:52 a detecção ficou em 100%. O fundo subiu de 7
  para 40 contagens com o sol e o pico do beacon ficou parado em 29–33
  contagens, com CNR entre 14 e 19. A autoexposição segurou tudo isso mexendo
  muito pouco: 737–817 µs o tempo todo.
- **Piso de sinal:** a exposição nunca desceu abaixo de 737 µs
  (`piso_de_sinal_atingido` 217 vezes). Sem ele teria ido para a faixa dos
  400 µs, onde o contraste era de ~16 contagens — o mesmo regime da sessão de
  06/09 que morreu.
- **Ausências curtas:** 18 episódios ≥ 1 s em 3 h, ou **5,9/h**, contra 176/h em
  06/09. Só 2 deles aconteceram antes do colapso final.
- **Apontamento:** o mount andou 5 arcsec no total em 3 h. A ilha ficou em torno
  de (127, 128) e nunca tocou a borda. A perda do beacon **não** foi deriva.

### O que matou a sessão

Às 09:05:11 o beacon sumiu de verdade, com a exposição em 764 µs e o fundo em
23 contagens (o pico vinha estável em ~30 e simplesmente parou de aparecer).
Passados os 8 s de espera, a busca por alvo ausente disparou — corretamente,
pelas regras dela. O problema é o que veio depois:

- A rampa é **multiplicativa e leva o fundo junto com o sinal**. Em 23 s ela
  subiu 764 → 1031 → 1392 → 1879 → 2537 → 3425 → 4624 → 6242 → 7584 µs, e o
  fundo foi junto: 23 → 32 → 45 → 57 → 78 → 107 → 141 → 187 → 235 → 255.
- Com o quadro inteiro em 255 o alvo não tinha contraste em lugar nenhum. As
  "detecções" a partir das 09:06 são ilhas espúrias num campo branco.
- A redução de emergência (`reducao_emergencial_cena_saturando`) disparou **uma
  única vez** (6242 → 5618 µs), porque `_untrusted_safety_used` trava por
  episódio. A busca imediatamente desfez a correção subindo para 7584 µs.
- Daí em diante a exposição ficou congelada no topo por 4 minutos: a redução só
  roda com alvo confiável, e não havia alvo. A sessão morreu no limite de 90 s.

15 das 18 ausências ≥ 1 s da sessão estão espremidas entre 09:05 e 09:08, ou
seja, **depois** que a rampa começou. A busca não causou a perda inicial, mas
transformou uma perda recuperável num buraco sem saída e apagou a evidência do
que estava acontecendo no céu.

Este é o defeito **espelhado** do de 06/09: lá a exposição travava no piso e só
subir resolveria; aqui ela travou no teto e só descer resolveria. O conserto de
um criou o outro.

### Correção aplicada

1. **Limite de fundo próprio da busca**
   (`AUTO_EXPOSURE_LOSS_SEARCH_BACKGROUND_LIMIT = 120`), avaliado sobre o fundo
   **previsto** do próximo degrau, não sobre o fundo atual. O fundo de agora já
   é resultado do degrau anterior e sempre chega tarde. É mais baixo que o
   limite do controle normal (210) de propósito: ali existe um alvo medido, aqui
   a busca é cega e precisa preservar a margem em que o alvo apareceria.
2. **A busca vira uma hipótese com prazo.** Se a rampa parou porque clareou a
   própria cena, ela espera 20 s no topo (o alvo pode voltar no degrau mais
   alto) e então **desfaz o caminho**, voltando à exposição de onde partiu.
3. **Se a rampa parou no teto do hardware com a cena ainda escura, ela fica onde
   está** (`busca_alvo_ausente_no_teto`). Nada foi estragado, e exposição alta é
   o melhor lugar para esperar um beacon fraco. Isso preserva o conserto de
   06/09, que é exatamente esse caso.
4. **Não reinicia sozinha depois de falhar** — só depois que um alvo confiável
   reaparecer. Sem isso a rampa viraria um ciclo sobe-desce sem fim.

Replay da cena real de hoje pelo controlador corrigido: a rampa para em ~3400 µs
com o fundo em 103 contagens (cena ainda legível) e volta a 764 µs aos 43 s,
dentro da janela de 90 s.

Testes em `diversos/testes/test_exposicao_busca.py`, classe
`BuscaNaoPodeEstourarACenaTests`. O ponto cego dos testes antigos era usar cena
de fundo fixo em 2 contagens: nenhuma rampa estoura nada assim, e o defeito
passava despercebido. Os novos usam uma cena cujo fundo **escala com a
exposição**.

### Modo sombra — primeira safra de dados

94,4% das linhas com estimativa pronta, ao longo de 3 h:

| viés radial (px) | valor |
|---|---|
| mediana | 0,733 |
| p25 / p75 | 0,427 / 1,099 |
| p90 | 1,550 |
| máximo | 5,844 |

- Fração do tempo acima de 0,6 px (gatilho da sombra): **61,1%**
- Fração do tempo acima de 1,0 px (`HOLD_ENTER_RADIUS_PX` atual): **29,8%**
- Correções hipotéticas da sombra: 38 (12,5/h)
- Correções reais executadas: 217 (71,5/h)
- Erro radial instantâneo: mediana 1,182 px, p90 2,669 px
- Raio de controle (mediana temporal): 0,888 px

Leitura: existe um viés persistente de ~0,73 px que a zona de repouso de 1,0 px
deixa passar. Ele é **maior** que o de 06/09 (0,47 px) e de 04/09 (0,60 px), e
está em 61% do tempo acima do gatilho. As 12,5 correções/h que a sombra pediria
são um sexto das 71,5/h que o tracker já faz, ou seja, apertar a zona custaria
pouco em atividade de mount.

Ainda assim, **não apertar ainda**: 0,73 px de viés contra um erro instantâneo
de mediana 1,18 px significa que o viés é da ordem do próprio ruído de
turbulência, e a sombra não prova que uma correção o reduziria — só que ele
existe. O que decide é a distribuição do viés **depois** de uma correção
hipotética, e para isso a sombra precisa fechar o laço (estimar o efeito, não só
o erro). Próximo passo natural do modo sombra.

### Pendências que esta sessão não resolve

- Por que o beacon sumiu às 09:05 continua em aberto. Não foi apontamento, não
  foi deriva, não foi o fundo do céu (o fundo em 764 µs estava em 23 contagens,
  igual ao das 3 h anteriores). Falta o lado do UFF: o laser continuou ligado?
- A máscara de pixels ruins ainda foi medida a 18000 µs, não na faixa de
  operação real (~750 µs). Separar defeito fixo de corrente escura.

### Oclusão por embarcação: a perda mais comum não é falta de exposição

O enlace UFF–CBPF atravessa a baía de Guanabara e embarcações cortam o feixe
com frequência, tipicamente por 1 a 2 minutos. Isso muda o diagnóstico padrão de
uma perda de sinal. Confirmado no dia 09/09: por volta das 12h a luz continuava
ligada, apenas com baixa visibilidade — o beacon das 09:05 não tinha sido
desligado.

**Oclusão e falta de exposição são diagnósticos opostos e pedem respostas
opostas.** Uma embarcação tira o beacon inteiro de um sinal saudável; falta de
exposição produz desvanecimento, com o contraste raspando o limite por minutos
antes de a ilha sumir. Subir a exposição não traz de volta um feixe bloqueado —
só estraga a cena, como aconteceu hoje.

Diante de uma oclusão a resposta certa é **congelar tudo e esperar**: o mount já
fica parado, a assinatura da ilha travada e a posição alvo continuam guardadas,
a exposição fica congelada, e o feixe volta com as mesmas características. O céu
não muda em dois minutos — hoje o fundo subiu 7 → 40 contagens em 3 h, ou
~0,2 contagem por minuto.

#### O discriminador: CNR do último alvo confiável

Medido nas duas sessões reais, na janela imediatamente anterior à perda:

| | CNR mediano | CNR p10 | nível (pico − fundo local) |
|---|---|---|---|
| 06/09 — faltava exposição mesmo | **7,5–8,0** | 7,0 | 6,2–8,7 |
| 09/09 — oclusão, beacon intacto | **16,9** | 15,3 | 4,1–8,0 |

Separação limpa, sem sobreposição. O **nível absoluto não serve** — 6,2–8,7
contra 4,1–8,0 nas mesmas janelas, completamente sobrepostos. Vale registrar
porque `AUTO_EXPOSURE_MIN_TARGET_LEVEL` era o discriminador óbvio e teria
classificado a oclusão de hoje como desvanecimento, fazendo exatamente a coisa
errada.

O diagnóstico é **congelado no instante da perda**: o histórico de qualidade da
autoexposição dura só 2 s e já se esvaziou quando a perda se confirma.

#### Mudanças

- `AUTO_EXPOSURE_LOSS_SEARCH_OCCLUSION_SECONDS = 150` — espera antes da busca
  quando o beacon sumiu saudável. Cobre a ocultação típica com folga.
- `AUTO_EXPOSURE_LOSS_FADING_CNR = 10.0` — abaixo disso a perda é lida como
  desvanecimento e a busca começa nos 8 s de sempre. Fica acima de
  `AUTO_EXPOSURE_CNR_LOW` (8,0) de propósito: errar para oclusão custa 150 s de
  um orçamento de 600 s, errar para desvanecimento dispara uma rampa na cena
  errada. Sem medida anterior, espera-se o tempo longo.
- `SIGNAL_LOSS_LIMIT_SECONDS` de 90 para **600 s**. Um limite curto transforma um
  navio passando em fim de sessão. Esperar não tem custo mecânico nenhum; o
  único custo é tempo de sessão. Um navio de 2 min consome 20% do orçamento.

Simulação ponta a ponta sobre a cena real de hoje (beacon saudável → navio de
120 s → beacon volta): a exposição fica em 764 µs o tempo inteiro, congelada em
`congelada_alvo_ausente`, e volta a medir com as mesmas características.

Testes: `OcultacaoTests` em `diversos/testes/test_exposicao_busca.py`. Suíte em
172 testes.

#### O que ainda não é tratado

Numa ausência muito longa (perto dos 600 s) o céu pode ter mudado o bastante
para a exposição congelada não servir mais na volta. Hoje isso não importa
(~0,2 contagem/min), mas ao amanhecer ou anoitecer o fundo se move mais rápido.
O tracker poderia acompanhar o **fundo** durante a ausência — ele é medível sem
o alvo — em vez de congelar às cegas. Fica para depois de haver dado de uma
ocultação longa de verdade.

### O viés não é um desvio permanente — e a sombra não consegue decidir sozinha

Análise da sessão 09/09 (48.284 amostras de sombra prontas, 3 h). Duas perguntas
diferentes que eu tinha misturado numa só:

**Existe desvio permanente?** Não.

| | |
|---|---|
| vetor médio do viés em 3 h | (+0,089, +0,082) px — módulo **0,121** |
| módulo médio do viés | **0,880** px |
| razão \|média\| / média\|·\| | **0,14** |

Por blocos de 30 min o sinal troca nos dois eixos (y: +0,43, +0,20, −0,12, +0,31,
−0,29, −0,04). Não há desalinhamento fixo a corrigir: a calibração e o
apontamento estão sãos. Nada de correção estática a aplicar.

**O erro tem direção no instante?** Sim, na maior parte do tempo. Coerência
direcional dentro da janela de 60 s (|média dos vetores unitários|):

| p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|
| 0,200 | 0,323 | **0,515** | 0,739 | 0,873 |

Ou seja: existe uma deriva de apontamento real, coerente por ~1 min, que reverte
em escala de dezenas de minutos. É corrigível em princípio. O erro médio não é
"turbulência pura" nem "desalinhamento" — é uma deriva lenta que vagueia.

#### Custo de apertar a zona

| | |
|---|---|
| tempo em `pulso` + `acomodacao` | **4,9%** da sessão (217 correções) |
| custo médio por correção | 2,5 s |
| erro de controle acima de 1,0 px (zona atual) | 43,2% do tempo |
| erro de controle acima de 0,6 px | 69,3% do tempo |

Apertar para 0,6 px levaria a ~1,6x mais correções (≈350) e o tempo morto de 5%
para ~8%. Barato.

#### O limite de fundo da sombra

A sombra **não pode responder** se apertar melhora: ela mede o erro que sobra,
mas o efeito de uma correção que não aconteceu não é observável. Contar
travessias de limiar não é o mesmo que estimar benefício. Isso é uma limitação
de projeto, não um parâmetro mal escolhido — e a contagem de 38 correções ainda
é subestimada, porque o rearme abaixo da metade do gatilho limita quantas ela
registra.

Só um experimento responde, e ele precisa ser **pareado**: comparar duas sessões
diferentes compara o céu, não o parâmetro.

#### A/B da zona de repouso (`HOLD_RADIUS_AB_TEST_ENABLED`, desligado)

Ligado, o raio de entrada alterna entre 1,0 px e 0,6 px a cada 10 min dentro da
mesma sessão. A telemetria ganhou a coluna `zona_parada_raio_px`; a análise
depois separa por ela e compara a distribuição de `distancia_px` nos dois
regimes, com turbulência, beacon e céu iguais.

Desligado por padrão de propósito: mexe em controle de verdade e uma sessão
longa sem operador não pode ganhar isso de surpresa por um commit. Há teste que
trava esse padrão.

## A/B do regime de controle — sessão 2026-09-09 20:39–23:39 (3 h)

Sessão completa e saudável: 99,88% com alvo, ausência máxima de 1,8 s, retorno
ao início bem-sucedido, encerrada por tempo máximo. 18 blocos de 10 min,
9 de cada regime, split de amostras 25430/25508.

**O regime lento perdeu nos dois critérios.** Descartando os 2 primeiros
minutos de cada bloco como transição:

| | atual | lento |
|---|---|---|
| erro radial mediano | **1,100 px** | 1,567 px |
| erro radial médio | **1,331 px** | 1,790 px |
| erro radial p90 | **2,265 px** | 3,382 px |
| raio de controle mediano | **0,947 px** | 1,427 px |
| DC médio por bloco | **0,62 px** | 1,41 px |
| correções/h | 65,4 | 44,7 |
| tempo morto | 4,58% | 3,12% |
| **% do tempo sem poder agir** | **6,9%** | **77,2%** |

### A causa

A última linha explica tudo. No regime lento o estimador de 120 s é zerado
depois de cada pulso e precisa de 60 s de aquecimento; como as correções saem a
cada ~80 s, o controlador fica **cego 75% do tempo** (60/80 = 0,75, e o medido
foi 77%). Durante a cegueira o erro cresce sem ninguém olhando, e quando o
estimador fica pronto ele já está grande — o que dispara outra correção, outro
reset, outra cegueira.

O desenho se auto-sabota: reset → cego → erro cresce → corrige → reset.

E a economia de correções que o justificava **não aconteceu**. O bloco 14 do
regime atual teve 52 correções sozinho (turbulência 0,409 px contra 0,28 px da
mediana da sessão — foi uma rajada real, qualidade óptica normal o tempo todo).
Tirando esse outlier:

- atual, condições típicas: **34,5 correções/h**
- lento: **44,7 correções/h**

Ou seja, o regime lento corrige **mais**, com 40% mais erro. O oposto do
objetivo nos dois eixos.

### Onde a simulação errou

Ela previu erro parecido e 9× menos correções. Errou porque não modelou o custo
da cegueira pós-reset: no modelo o estimador continuava observando e só o
`ready` gateava; na prática os 60 s de aquecimento são 60 s em que o mount não
pode agir sobre nada. A simulação também superestimou o regime atual em 6×
(400/h contra os 65/h reais), então a razão entre regimes nunca foi confiável.

### O conserto possível

O defeito é o **reset**, não a janela longa. Em vez de descartar a janela após
cada pulso, **subtrair do histórico o deslocamento aplicado**: cada amostra
guardada leva o mesmo desconto que o mount executou, a mediana passa a refletir
o estado corrigido no mesmo instante, e não há aquecimento nem cegueira.

O erro de 5% entre o comandado e o executado (medido: encoder entrega 0,95 do
comandado) entra como resíduo nas amostras novas, que é exatamente o
comportamento correto — o estimador vê e corrige de novo.

### O que fica valendo por enquanto

O regime atual está bem: 34,5 correções/h em condições típicas, erro mediano
1,10 px, 4,6% de tempo morto. O desvio sistemático de 0,62 px continua sendo
~56% do erro mediano e continua valendo a pena atacar — mas não por este
caminho, como está.
