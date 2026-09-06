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
