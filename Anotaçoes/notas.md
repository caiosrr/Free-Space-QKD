# Notas de continuidade do Free-Space-QKD

Atualizado em 2026-08-20. Este documento guarda somente o estado atual,
decisoes tecnicas ainda validas, roteiro de estudo e ideias futuras.

## Estado atual do projeto

### Fluxo principal

1. `foco_multiplos/centro_massa.py` detecta e seleciona a fonte.
2. `calibracoes/calibracao_continua.py` mede a relacao angular-pixel.
3. `controle/Tracker.py` carrega a matriz, acompanha a ilha e controla o mount.
4. `controle/mount_control.py` concentra movimento local e parada segura.
5. `controle/alvo_alinhamento.py` guarda coordenadas, ROI e assinatura da ilha;
   e uma biblioteca interna, nao um programa para executar.

Os drivers ficam em `controle/cameras/`:

* `alpaca.py`: ASI pelo ASCOM/Alpaca;
* `ids_peak.py`: IDS pelo SDK peak;
* `zwo_sdk.py`: ASI pelo SDK nativo da ZWO;
* `backend.py`: interface comum usada pelos programas.

### Resultado experimental relevante

O tracker IDS estabilizou a fonte do enlace UFF-CBPF por aproximadamente
1 h 40 min, incluindo perturbacoes manuais, sem trocar para fachadas ou outras
luzes quando a fonte desaparecia. Isso mostrou que a combinacao de selecao
manual, assinatura, continuidade espacial, matriz local e travas de seguranca
e adequada para o enlace longo.

Parametros atuais do tracker, obtidos pelo autotune com sucesso em 6/6 ensaios:

* `KpAz = 1.500`;
* `KpAlt = 1.440`;
* `KdAz = 0.180`;
* `KdAlt = 0.180`;
* `Trim = 1.200`;
* `Alpha = 0.650`;
* `Accel = 2.000`.

Esses valores sao ponto de partida, nao constantes universais. Mudancas de
mount, camera, optica ou enlace exigem nova validacao.

### Calibracao atual

O executavel principal e:

`python .\calibracoes\calibracao_continua.py --camera zwo --perfil robusto`

Tambem aceita `--camera ids` e `--perfil rapido`.

* `rapido`: quatro varreduras locais de `0.008 deg`;
* `robusto`: quatro varreduras para ajuste, quatro holdouts locais e quatro
  testes de `0.014 deg`;
* somente os dados locais entram na matriz do tracker;
* a amplitude maior verifica linearidade, mas nao altera o ajuste local;
* cada movimento parte da origem absoluta e o encerramento tenta restaura-la;
* matrizes ativas so mudam depois da validacao e confirmacao do operador.

A calibracao por pontos foi preservada apenas como
`calibracoes/legado/calibracao_estrela.py`.

### Desempenho de camera

No caminho ASCOM/Alpaca, o custo dominante foi aquisicao e transferencia, nao
o centro de massa. O processamento local em ROI de tamanho moderado ficou na
ordem de poucos milissegundos.

O backend `zwo_sdk` ja existe para a calibracao e usa video persistente, ganho,
exposicao e ROI nativos. Ainda precisa ser validado com a camera real antes de
ser colocado no tracker. O backend Alpaca deve continuar como fallback.

## Plano de estudo dos codigos

Objetivo: conseguir explicar, modificar e diagnosticar cada parte importante
sem depender do historico das implementacoes.

### Padrao de documentacao

Cada modulo importante deve informar no inicio:

* objetivo;
* entradas e saidas;
* unidades utilizadas;
* dependencias de hardware;
* efeitos sobre camera e mount;
* comportamento de seguranca.

Programas grandes devem ser divididos em blocos com titulos. Funcoes publicas
e matematicamente importantes devem ter docstrings curtas. Comentarios internos
devem explicar o motivo de uma decisao, nao repetir a sintaxe do Python.

Exemplo de comentario util:

```python
# Centraliza cada trajetoria separadamente para impedir que o drift entre
# varreduras seja interpretado como resposta angular do mount.
```

Evitar comentarios como `# calcula a mediana` antes de `np.median(...)`.

### Ordem recomendada

#### Dia 1 — Centro de massa e ilhas

Arquivo: `foco_multiplos/centro_massa.py`.

Estudar:

* normalizacao do frame;
* threshold;
* componentes conexos/ilhas;
* centro de massa ponderado;
* assinatura da fonte;
* continuidade espacial;
* ROI e deteccao de borda.

#### Dia 2 — Calibracao e algebra linear

Arquivo: `calibracoes/calibracao_continua_core.py`.

Estudar:

* vetor angular `[dAz, dAlt]`;
* vetor visual `[dx, dy]`;
* matriz `A` e inversa `A_inv`;
* ajuste por minimos quadrados;
* IRLS/Huber para outliers;
* condicionamento;
* holdout e validacao de linearidade;
* sincronizacao aproximada entre frame e posicao do mount.

#### Dia 3 — Movimento do mount

Arquivo: `controle/mount_control.py`.

Estudar:

* posicao absoluta e movimento relativo;
* wrap do azimute em `0/360 deg`;
* sinais fisicos dos eixos;
* tolerancia;
* controle PID/PD;
* limite de velocidade;
* parada e retorno seguro.

#### Dia 4 — Tracker

Arquivo: `controle/Tracker.py`.

Estudar:

* carregamento da matriz;
* conversao de erro em pixels para erro angular;
* zona de repouso e histerese;
* PD e trim lento;
* perda de sinal;
* salto de ilha e runaway;
* watchdog de posicao;
* telemetria CSV;
* encerramento e retorno.

#### Dia 5 — Cameras e integracao

Pasta: `controle/cameras/`.

Estudar:

* diferenca entre Alpaca e SDK nativo;
* exposicao, ganho e formato do frame;
* ROI no sensor;
* captura persistente;
* timeout, reconexao e frames descartados;
* convencao de orientacao da imagem.

### Exercicios sugeridos

1. Gerar uma imagem sintetica com uma fonte gaussiana e calcular seu CM.
2. Adicionar ruido de fundo e observar o efeito do threshold.
3. Colocar uma segunda fonte mais intensa e manter a identidade da primeira.
4. Simular uma fonte piscando e definir uma politica de perda de sinal.
5. Criar uma matriz `A`, gerar deslocamentos sinteticos e recupera-la.
6. Adicionar outliers e comparar minimos quadrados comum com ajuste robusto.
7. Aplicar `A_inv` manualmente a um erro em pixels e conferir sinais/unidades.
8. Criar um mount simulado e observar convergencia, overshoot e saturacao.
9. Escrever um teste unitario antes de mudar um threshold do tracker.
10. Explicar com as proprias palavras por que uma matriz local nao deve ser
    extrapolada por varios graus.

Ao estudar um modulo, registrar duvidas e pequenas explicacoes no proprio
codigo. Alteracoes de comportamento devem ser separadas de mudancas puramente
documentais para facilitar revisao e testes.

## Pendencias tecnicas

### Validar a ZWO pelo SDK

1. Instalar o SDK oficial e `zwoasi`.
2. Fechar ASIStudio e desconectar a camera do ASCOM.
3. Conferir orientacao, ROI, tipo do array, ganho e exposicao.
4. Comparar SDK e Alpaca com a mesma fonte e configuracao.
5. Medir Hz, latencia media/p95, jitter, frames perdidos e variancia do CM.
6. Rodar um ensaio longo antes de permitir tracking automatico pelo SDK.

So criar produtor-consumidor depois dessa medicao. Se for necessario, processar
apenas o frame mais recente e nunca manter uma fila crescente.

### Autotune com dois telescopios

O receptor deve executar o tracker. O emissor, controlado por `mount_agent`,
deve gerar perturbacoes padronizadas em Az, Alt e diagonais. Avaliar:

* RMS e erro maximo;
* tempo de retorno;
* overshoot e oscilacao;
* eventos de runaway/freio;
* saturacao de comando;
* perda de sinal ou saida da ROI.

O objetivo e rejeicao de perturbacao do experimento completo, nao apenas
otimizar rapidez no mesmo mount que corrige.

### Futuro: mapa de Jacobianas locais

Uma matriz `2 x 2` descreve apenas a vizinhanca onde foi calibrada. Para uma
regiao maior:

1. Definir uma grade limitada de posicoes absolutas.
2. Calibrar uma Jacobiana local em cada no.
3. Salvar posicao, `A`, `A_inv`, RMS, condicionamento e faixa validada.
4. Repetir alguns nos para medir reprodutibilidade.
5. Selecionar a matriz mais proxima ou interpolar apenas entre vizinhos validos.
6. Recusar extrapolacao quando nao houver um no confiavel.

Esse mapa pode alimentar um alinhamento grosso, mais lento e menos preciso,
antes de entregar o spot ao tracker fino. Ele ainda depende de a fonte estar no
sensor; se a fonte desaparecer completamente, sera necessaria uma busca segura
em grade ou espiral. A ideia fica registrada, mas nao sera implementada antes
de haver tempo de bancada para validacao.

### Futuro: tracker temporal robusto e oclusoes

O mount deve corrigir deriva lenta do centro medio do beacon, nao perseguir a
turbulencia rapida. Antes de alterar o controle, usar o caracterizador em
`Link UFF/caracterizacao_beacon/` para medir a perturbacao e comparar janelas
temporais. A estrategia candidata e hibrida:

1. Manter aquisicao rapida e validar a identidade em cada frame.
2. Acumular frames validos normalizados para formar a mancha media.
3. Manter os centroides individuais para rejeitar outliers e diagnosticar o
   que aconteceu dentro da janela.
4. Corrigir o mount apenas se a estimativa media permanecer fora da zona de
   repouso por mais de uma janela.
5. Reiniciar o acumulador depois de cada movimento e apos perda prolongada.

Casos que o tracker futuro precisa tratar explicitamente:

* oclusao temporaria por embarcacao ou outro objeto: velocidade zero, manter a
  ultima identidade/posicao e aguardar recuperacao por tempo limitado;
* reaparecimento: exigir varios frames coerentes antes de voltar a comandar;
* aumento ou reducao brusca do spot: ampliar temporariamente a tolerancia de
  forma sem aceitar uma fonte concorrente;
* perda alem do limite: permanecer parado ou retornar de forma segura conforme
  politica escolhida, nunca iniciar busca ampla automaticamente sem limites;
* salvar frame bruto e marcado no inicio da perda, recuperacao, salto, mudanca
  de tamanho/intensidade, borda da ROI e falha de captura;
* usar cooldown por tipo de evento para uma oclusao longa nao encher o disco.

Separar sempre a caracterizacao da perturbacao da dinamica do atuador. A PSD e
o tempo de correlacao da luz indicam o que seria desejavel corrigir; latencia,
resposta mecanica e estabilidade do mount limitam o que pode ser corrigido.

### Controle por potencia da fibra

Potencia e uma medida escalar e nao informa diretamente o sentido do erro.
Portanto, nao usar um PID simples sobre `potencia_alvo - potencia_medida`.

Estrategias adequadas:

* busca local discreta;
* subida de gradiente;
* extremum seeking;
* dither com deteccao de fase.

Fluxo futuro possivel:

1. Tracker mantem o spot visivel e aproximadamente estavel.
2. Pequenos movimentos estimam `dP/dAz` e `dP/dAlt`.
3. O receptor se move no sentido de aumento da potencia.
4. A posicao visual correspondente ao melhor acoplamento vira o novo alvo.
5. O emissor faz ajustes mais lentos pelo `mount_agent`.

## Checklist de seguranca para bancada

* Conferir cabos, fontes, folga mecanica e caminho optico.
* Verificar a posicao absoluta antes de calibrar ou trackear.
* Testar movimentos pequenos nos dois sinais de cada eixo.
* Manter parada fisica ou corte de energia acessivel.
* Usar `Ctrl+C` como parada de software, sem tratá-lo como protecao mecanica.
* Nao movimentar automaticamente sem imagem, matriz valida e watchdog ativo.
* Refazer a calibracao ao mudar camera, orientacao, optica ou montagem.
* Preservar CSV, resumo e matrizes associados a resultados apresentados.
