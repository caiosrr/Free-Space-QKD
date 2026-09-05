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

## Ordem recomendada

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

## Auditoria da calibracao continua

O estimador usa referencias paradas A-B-A: origem, ponto deslocado e retorno
a origem. Cada referencia espera 0,8 s de acomodacao e mede pelo menos 2 s
de imagens. Faz medias de imagem por blocos de 0,4 s e usa a mediana dos centros
dos blocos, sem tratar frames consecutivos como amostras independentes.
Acima de 120 FPS, a referencia guarda uma subamostragem temporal para limitar
memoria, sem encurtar os 2 s. A auditoria distingue frames capturados e retidos.

A coleta exige >=20 frames validos, >=60% de validade na janela recente e
quatro blocos utilizaveis. Pode esperar ate 12 s por referencia; nao busca outra
ilha nem muda a exposicao. Os JSONs `*_referencia_A/B/A_retorno.json` guardam
posicoes, blocos e motivos de rejeicao, inclusive nas tentativas malsucedidas.

A origem e interpolada entre A e A_retorno no instante de B, em pixels e nos
angulos informados. Isso compensa deriva aproximadamente linear durante o ciclo,
nao separa atmosfera de mecanica. O retorno optico desconta a pequena diferenca
angular residual usando a resposta A->B do proprio eixo, sem matriz anterior.
Nao extrapola alem de 15% da excursao nem compensa um eixo ortogonal sem escala.
Se o residuo optico divergir mais que 25% da resposta (com piso de 3 px e teto
de 10 px), o ciclo e recusado. A dispersao dos
blocos serve como indicador conservador de qualidade, nao intervalo de confianca.

`amostras.csv` e `*_diferenca_estatica.csv` contem pares virtuais origem/deslocamento
com `sample_kind=stationary_ABA_difference`; nao sao coordenadas reais do sensor.
Estas estao nos JSONs de referencias. `stationary_aggregation` no resumo conta
as referencias e os frames efetivamente usados. O perfil robusto leva tipicamente
3-5 min; perdas de sinal e retornos demorados podem alongar a execucao.

As varreduras continuam com os watchdogs de movimento e perda de sinal. Os CSVs
`*_frames.csv` e `*_bins.csv` sao diagnosticos, nao entram na matriz; o primeiro
segundo do movimento fica fora dos bins de diagnostico. Os frames ja adquiridos
sao salvos mesmo quando a captura e interrompida, depois de solicitar a parada.

`raw_centroid_spread_px` inclui o movimento durante cada bin. Ja
`centroid_spread_px` desconta uma tendencia linear robusta da varredura inteira
antes de medir a dispersao; nao e uma medida exclusiva de turbulencia.
`bin_duration_s` e `trend_x_px_s`/`trend_y_px_s` permitem auditar esse desconto.
Essa tendencia pertence apenas ao diagnostico dinamico. As referencias paradas
exigem dispersao P90 dos centros dos blocos <=5 px e variacao angular entre
blocos <=0,00056 grau. Ida/volta e validacoes independentes continuam necessarias.

A calibracao usa ROI de pelo menos 512 px (limitada pelo sensor), independente
da ROI menor do tracker. Ida/volta e holdout exigem razao entre escalas <= 1,35
e cosseno entre direcoes >= 0,98. O holdout reprova residuo RMS acima de 3 px
quando tambem excede 25% da resposta mediana prevista. Esses limites iniciais
precisam de validacao experimental; uma rejeicao preserva a matriz ativa.

O retorno usa alvo absoluto fixo no PID e exige pelo menos 1,5 s de leituras
dentro de 0,0005 grau do alvo (1,8 arcsec), com variacao <=1 arcsec na janela.
Cada verificacao dura no maximo 4 s, com ate duas tentativas de retorno.
Para A_retorno, o alvo e o angulo efetivamente medido em A; a referencia optica
tambem exige permanencia dentro da tolerancia durante sua janela de coleta.
Isso confirma a telemetria do driver, nao substitui uma verificacao mecanica.

`*_retorno.json` registra alvos, pedidos de movimento, leituras/comandos do PID
e leituras apos parar. `*_fechamento.json` registra retorno observado em pixels,
deslocamento previsto pela diferenca angular e residuo, inclusive nas rejeicoes.

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
