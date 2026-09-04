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

## Referência física aproximada

Com a calibração usada no enlace de 7 km:

- 1 px corresponde a aproximadamente 1,04 arcsec;
- 1 px equivale geometricamente a cerca de 3,5 cm no plano a 7 km;
- 1,5 px equivale a cerca de 5,3 cm;
- 2 px equivale a cerca de 7,1 cm.

Esses valores representam uma estimativa de erro de apontamento. A perda óptica
real também depende da divergência do feixe, abertura, alinhamento entre os
canais e acoplamento na fibra.
