# Programas principais

Esta e a pasta para abrir no VS Code quando for executar o experimento. Os
arquivos daqui sao iniciadores; a implementacao fica nas pastas internas.

| Programa | Funcao | Move o mount? |
|---|---|---|
| `testar_camera_ids.py` | Testa aquisicao, ganho, exposicao e FPS da IDS | Nao |
| `caracterizar_beacon_ids.py` | Mede posicao, forma e intensidade do beacon ao longo do tempo | Nao |
| `centro_de_massa.py` | Seleciona a ilha e abre observacao/alinhamento de baixa frequencia | Depende da opcao escolhida |
| `calibracao.py` | Gera a matriz angular-pixel com ZWO SDK ou IDS | Sim |
| `tracker.py` | Executa o tracker principal com ASI/ASCOM ou IDS | Sim |
| `diagnosticar.py` | Mede sinal, SNR, escala e pixels defeituosos | Nao |

Cada programa aceita argumentos e, sem eles, pergunta o que precisa:

```powershell
python .\programas_principais\centro_de_massa.py --camera ids
python .\programas_principais\calibracao.py --camera ids --perfil robusto
python .\programas_principais\tracker.py --camera ids --horas 0.5 --sem-autoteste
```

## Ordem recomendada

0. Diagnosticar sinal e, uma vez por montagem, medir os pixels defeituosos:

   ```powershell
   python .\programas_principais\diagnosticar.py --camera ids --calibrar-pixels
   python .\programas_principais\diagnosticar.py --camera ids --exposicao 1200 10000
   ```

   A mascara e medida com o feixe BLOQUEADO e a objetiva tampada; depois disso
   o tracker a carrega sozinho. Com o beacon perto de 15 contagens, um unico
   pixel quente de 25 contagens ja faz o detector perder o alvo, entao esta
   medida vale por si so.

1. Testar a camera.
2. Caracterizar ou observar o beacon sem movimento.
3. Executar a calibracao.
4. Executar o tracker.

O tracker pergunta se deve executar um autoteste temporario de recuperacao.
Responder Enter ou `n` ignora o teste; `s` aplica um pequeno deslocamento antes
da sessao e exige que a mesma ilha volte ao centro.

Durante a sessao, mudancas bruscas no tamanho, intensidade ou forma da ilha sao
tratadas como anomalia optica: esses frames nao entram na media e o mount fica
parado ate a luz permanecer normal por alguns segundos.

Para usar o botao Play, abra o arquivo desejado. `calibracao.py`,
`centro_de_massa.py` e `tracker.py` perguntam qual camera usar.

Antes de qualquer programa que mova o telescopio, confirme folga mecanica,
matriz correta, comunicacao com o mount e acesso a uma parada fisica. `Q`,
`Esc` e `Ctrl+C` solicitam parada por software, mas nao substituem o limite
mecanico ou o corte de energia.

Os parametros da ASI ficam em `../modulos/configuracoes/camera_asi.py`. Os da IDS ficam
em `../modulos/configuracoes/camera_ids.py`. Resultados IDS continuam em
`../Link UFF/resultados/`.

## Calibracao por varredura continua

Cada sentido de cada eixo recebe UMA varredura continua longa. A matriz e
ajustada somente sobre a fase de velocidade constante dessa varredura.

Por que assim, e nao por passos curtos: a sessao de 2026-09-05 mediu que este
mount reporta a posicao em degraus de 1 a 4 arcsec a cerca de 2 Hz, quantizados
em 1 arcsec, e que o eixo leva cerca de 1 s para vencer o atrito estatico. Num
passo de 0,002 grau (7 arcsec, 1,5 s) isso deixava 30 a 60% de erro no angulo e
ate 20x de espalhamento na velocidade optica: para o MESMO angulo relatado de
11 arcsec, o deslocamento observado variou de 9 a 29 px. A turbulencia nao era o
fator limitante (referencias paradas com 1,9 px de dispersao e 0,14 px/s de
deriva, contra respostas de 10 a 35 px).

A correcao e aumentar o braco de alavanca. Com amplitude de 0,040 grau
(144 arcsec) a 0,004 grau/s, cada fonte de erro cai na proporcao da amplitude:
a quantizacao de 1 arcsec sai de 10-30% para cerca de 1%, o transiente de
partida sai de 40% para poucos por cento e ainda e descartado, e a turbulencia
sai de 10-20% para cerca de 1%.

### Como cada varredura funciona

1. Referencia parada antes, para medir ruido e deriva local.
2. Movimento continuo num sentido so, ate o que vier primeiro: a amplitude
   angular, o orcamento de 400 px ou um dos watchdogs. O orcamento em pixels
   protege a borda da ROI sem precisar conhecer a escala, que e justamente o
   que estamos medindo.
3. Deteccao automatica da fase estavel: mede-se a velocidade optica da segunda
   metade da varredura e descarta-se todo o inicio abaixo de 90% dela. O tempo
   descartado e registrado como `transient_seconds`, entao da para acompanhar o
   atrito do mount ao longo das noites.
4. Os frames da fase estavel sao agrupados em bins angulares de 7,2 arcsec,
   que e o passo real da telemetria: o driver so atualiza a posicao a cerca de
   2 Hz, entao bins mais estreitos apenas se dividem dentro do mesmo patamar.
   Cada bin exige ao menos 3 frames e vira uma amostra do ajuste.
5. Referencia parada depois, e retorno a posicao absoluta inicial.

A varredura exige pelo menos 3 s de fase estavel e 8 bins validos. Abaixo disso
ela e recusada em vez de produzir uma escala ruim em silencio.

### Duas reguas independentes

Cada varredura reporta a escala medida de duas formas: pelo angulo relatado
pelo mount e por tempo x taxa comandada. As duas cobrem o mesmo deslocamento
optico. A matriz usa somente a primeira, mas a razao entre elas fica na
auditoria e no resumo: se ela fugir de 1, o problema esta na telemetria ou na
taxa do mount, e nao na deteccao do centroide.

### Validacao

O perfil `robusto` faz 4 varreduras de ajuste e 4 de validacao independente; o
`rapido` faz apenas as 4 de ajuste. Ida e volta e holdout exigem razao entre
escalas <= 1,35 e cosseno entre direcoes >= 0,98. O holdout reprova residuo RMS
acima de 3 px quando tambem excede 25% da resposta mediana prevista. Uma
rejeicao preserva a matriz ativa.

O retorno usa alvo absoluto fixo no PID e exige pelo menos 1,5 s de leituras
dentro de 0,0005 grau do alvo, com variacao <= 1 arcsec na janela. Isso confirma
a telemetria do driver, nao substitui uma verificacao mecanica.

A calibracao usa ROI de 1024 px (limitada pelo sensor), independente da ROI
menor do tracker, para caber a excursao da varredura longe da borda.

## Correcoes limitadas e teste acompanhado

O tracker limita cada comando a 35% do menor erro angular entre o historico
de controle e a media atual. Um eixo com sinais discordantes nao recebe pulso.
Os limites de duracao sao 0,12 s no modo fino e 0,25 s no modo de erro grande,
a velocidade minima do mount. O prazo e checado em cada ciclo de controle,
mesmo sem novo frame; atrasos de software/driver ainda afetam a parada fisica.

Depois dos comandos zero confirmados, o controle descarta suas medianas e
espera 0,3 s de acomodacao mais a janela de imagem de 2 s. So entao recompõe
o historico com medias posteriores ao movimento, mantendo o mesmo alvo.
A imagem e a deteccao continuam funcionando durante essa espera. O CSV inclui
`fase_correcao`, `ciclos_correcao` e `espera_pos_movimento_s`.

Primeiro executar calibracao robusta com IDS e verificar a aprovacao; depois
tracker acompanhado por 10-15 min, zona 1/2 px e media 2 s inalteradas.
Avaliar os sinais antes/depois dos pulsos e a convergencia antes de teste longo.
