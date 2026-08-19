<h1 align="center">Notas IC</h1>

## Retorno das ferias - plano de retomada

Data da anotacao: 2026-07-10.

Contexto: o setup esta usando dois computadores e dois telescopios. O notebook controla o telescopio emissor via `mount_agent`; o Alien e o PC do laboratorio e controla o telescopio receptor e a camera. A comunicacao entre eles esta sendo feita por HTTP, sem depender de conexao Alpaca direta entre PCs para os dois mounts.

### Antes de retomar testes longos

1. Fazer `git pull` no notebook e no Alien.
2. Conferir `git status` nos dois PCs antes de rodar qualquer coisa.
3. Verificar se o ambiente Python/`.venv` esta ativo e com as dependencias instaladas.
4. Conferir cabos USB, fontes dos telescopios, camera e laser antes de energizar.
5. Ligar o laser somente depois de checar tampas, caminho optico e seguranca.
6. Rodar um teste simples de movimento em cada mount antes do tracker.

### Estado bom antes das ferias

O tracker melhorou bastante depois do autotune com dois telescopios. O melhor conjunto encontrado foi:

* `KpAz = 1.500`
* `KpAlt = 1.440`
* `KdAz = 0.180`
* `KdAlt = 0.180`
* `Trim = 1.200`
* `Alpha = 0.650`
* `Accel = 2.000`

Esse conjunto teve `sucessos=6/6` no autotune e deve ser usado como ponto de partida tanto no `Tracker.py` quanto no `autotune_pid_tracker.py`.

### Pendencias principais

* Investigar e corrigir o comportamento em que movimentos bruscos deixam o tracker "em orbita" por um tempo antes de estabilizar e trazer o spot de volta para o centro.
* Rodar mais um autotune do tracker com os parametros acima como ponto inicial, procurando melhorias menores ao redor desse conjunto.
* Antes de tentar maximizar o acoplamento na fibra, testar bem o alinhamento por camera com os dois telescopios.
* Testar o alinhamento com dois telescopios usando o notebook e o Alien:
  * notebook: telescopio emissor, rodando `mount_agent`;
  * Alien: telescopio receptor, camera, tracker e autotune.
* Confirmar que o `mount_agent_client.py` consegue mover o telescopio emissor por angulo e que o retorno para a posicao inicial funciona.
* Depois de realinhar manualmente os telescopios, refazer a calibracao das matrizes antes de confiar no tracker/autotune.
* Centralizar o spot com `foco_multiplos/Center_of_Mass_foco_temp.py` antes de rodar tracker/autotune.
* Verificar se o tracker esta usando as matrizes corretas para o modo dual:
  * `foco_temp_A_inv_fine.npy`;
  * `foco_temp_A_inv_coarse.npy`.

### Cuidados tecnicos ao voltar

* Se o spot comecar perto do centro e a centralizacao automatica tentar joga-lo para fora, parar e verificar matriz/sinal antes de continuar.
* O laser provavelmente nao precisa de novo ajuste, mas conferir se potencia, foco e posicao inicial parecem consistentes depois das ferias.
* Se houver perda de camera via Alpaca/driver, reiniciar a camera antes de insistir em calibracao longa.
* Se o tracker tiver muitos `runaway events`, nao ir direto para otimizacao por potencia; primeiro melhorar estabilidade na camera.
* Registrar o alvo de camera associado ao melhor acoplamento quando a fibra comecar a acoplar bem.

## Ideia principal: autotune do tracker com dois telescopios

Data da anotacao: 2026-04-30.

O objetivo futuro e criar um autotune mais realista para o tracker. O laser que chega no telescopio principal vem de um segundo telescopio. A ideia e conectar os dois telescopios ao computador:

* Telescopio 1: sistema controlado pelo tracker. Ele usa a camera para manter o laser centralizado no sensor e, pelo prototipo mecanico atual, isso tambem deve manter o foco no encaixe da fibra.
* Telescopio 2: gerador de perturbacoes. O autotune deve mover esse telescopio para deslocar o feixe de entrada enquanto o telescopio 1 tenta acompanhar.

Essa abordagem deve testar rejeicao de perturbacao do sistema real, em vez de testar apenas uma perturbacao artificial aplicada no mesmo telescopio que esta corrigindo.

## Estrutura sugerida

Criar um novo arquivo, por exemplo:

`autotune_tracker_duplo_telescopio.py`

Esse arquivo deve:

* Rodar o tracker controlando apenas o telescopio 1.
* Mover apenas o telescopio 2 para criar perturbacoes padronizadas.
* Testar varios conjuntos de parametros do tracker.
* Medir o erro na camera durante cada ensaio.
* Gerar um ranking dos parametros.

## Parametros mais importantes para tunar

O tracker atual e mais um controle `PD + trim lento` do que um PID classico. Para o autotune, testar primeiro:

* `KP_AZ`
* `KP_ALT`
* `KD_AZ`
* `KD_ALT`
* `CMD_ACCEL_LIMIT`
* `MEASUREMENT_ALPHA`

O trim deve ser ajustado depois. Ele serve mais para erro persistente pequeno perto do centro, nao para perseguir perturbacao rapida.

## Ensaio padrao sugerido

Para cada conjunto de parametros:

1. Centralizar o laser com o tracker.
2. Esperar estabilizar dentro da tolerancia.
3. Aplicar perturbacoes pequenas no telescopio 2:
   * `az+`
   * `az-`
   * `alt+`
   * `alt-`
   * diagonais pequenas
4. Voltar o telescopio 2 para a posicao inicial apos cada perturbacao.
5. Repetir com rampas lentas, simulando o feixe andando continuamente.

Comecar com perturbacoes pequenas, idealmente gerando algo como `10-40 px` de deslocamento na camera. Depois aumentar se a malha estiver estavel.

## Metricas para ranquear

Nao escolher simplesmente o ganho mais rapido. Para acoplamento em fibra, estabilidade perto do centro e mais importante.

Metricas sugeridas:

* RMS do erro em pixels.
* Erro maximo em pixels.
* Tempo para voltar para dentro de `2 px`.
* Numero de brakes/runaway events.
* Tempo em saturacao de comando.
* Oscilacao perto do centro.
* Perda de sinal.
* Se o laser saiu do ROI da camera.

O melhor conjunto deve ser o que centraliza rapido sem ficar nervoso, sem movimento circular e sem depender de muitos brakes.

## Estado atual relevante

O `Tracker.py` ja foi ajustado para perguntar:

`Modo do laser (1=foco unico, 2=dupla reflexao)`

No modo `1`, ele usa as matrizes normais:

* `A_inv_fine.npy`
* `A_inv_coarse.npy`

No modo `2`, ele usa as matrizes temporarias da calibracao com dois focos:

* `foco_temp_A_inv_fine.npy`
* `foco_temp_A_inv_coarse.npy`

O tracker sempre usa o mount real; a pergunta de simulador foi removida.

Tambem foi adicionado um freio para movimento manual brusco: se o spot salta muito entre frames, o controle zera por um instante antes de tentar recentralizar.

## Observacoes sobre desempenho

Na ultima medicao do tracker:

* Camera ficou por volta de `10-13 Hz`.
* `cap` ficou perto de `70-80 ms`.
* `CM` ficou perto de `0.2 ms`.
* `UI` ficou perto de `13 ms`.

Conclusao: o gargalo principal e captura/transferencia da camera via Alpaca, nao o calculo do centro de massa.

Foi testado reduzir `WINDOW_SIZE` de `200` para `160`, o que melhorou a taxa, mas a preferencia atual e manter `200 px` por dar mais margem quando o laser se move. Se necessario em testes futuros, reduzir o ROI pode ser uma opcao.

## Futuro: aquisicao direta da ASI pelo SDK da ZWO

Data da anotacao: 2026-08-18.

O caminho ASCOM/Alpaca ja usa as principais otimizacoes disponiveis sem trocar
de backend:

* sessao HTTP persistente;
* transferencia binaria `ImageBytes`, com JSON apenas como fallback;
* ROI configurada diretamente na camera;
* tela e controle em frequencias separadas.

Mesmo assim, cada frame ASCOM continua seguindo aproximadamente:

`StartExposure -> consultas ImageReady -> download ImageBytes -> proximo frame`

Isso exige varias requisicoes HTTP e nao oferece o mesmo fluxo continuo de um
SDK nativo. A exposicao curta nao e necessariamente o maior custo; driver,
leitura do sensor, consultas e transferencia podem dominar o intervalo.

### Proposta

Criar no futuro um backend `zwo_sdk`, semelhante ao backend direto da IDS:

`camera ASI -> SDK ZWO/USB -> frames continuos -> tracker`

O mount continuaria usando ASCOM/Alpaca. Somente a captura da camera deixaria o
ASCOM Remote Server. O tracker, centro de massa, matrizes, watchdog e CSV devem
continuar compartilhados entre os backends.

Manter o backend atual `alpaca` como fallback. A selecao ideal deve ficar em uma
configuracao simples, por exemplo:

`CAMERA_BACKEND = "alpaca"` ou `CAMERA_BACKEND = "zwo_sdk"`

Nao remover o caminho ASCOM enquanto o SDK nao passar por testes longos.

### Por que priorizar o SDK

* Permite aquisicao continua, sem iniciar uma exposicao HTTP para cada frame.
* Pode reduzir latencia e variacao entre frames.
* Mantem controle direto de ROI, ganho, exposicao e formato RAW.
* E a opcao mais promissora para aumentar os Hz sem diminuir demais a ROI.

DirectShow/WDM pode ser rapido, mas nao e a primeira escolha porque normalmente
nao oferece RAW16. Como qualidade e prioridade, testar primeiro RAW8 e RAW16
pelo SDK oficial da ZWO.

### Antes de implementar

1. Confirmar o modelo exato da ASI e instalar driver/SDK oficial compativel.
2. Confirmar se existe wrapper Python confiavel ou integrar a DLL por `ctypes`.
3. Fechar/desconectar a camera no ASCOM antes de abrir pelo SDK; dois programas
   nao devem disputar o mesmo dispositivo USB.
4. Registrar no modo ASCOM, para referencia:
   * ROI usada;
   * Hz de medicao;
   * tempo medio e maximo de captura/transferencia;
   * exposicao, ganho e profundidade de bits;
   * variancia do CM e frames perdidos.

### Ensaio comparativo no laboratorio

Usar exatamente a mesma luz, exposicao, ganho e ROI nos dois backends e medir:

* Hz medio, minimo e percentis de latencia;
* jitter do intervalo entre frames;
* frames incompletos/perdidos;
* uso de CPU;
* intensidade, forma e variancia do centro de massa;
* resposta do tracker a perturbacoes iguais;
* estabilidade durante pelo menos uma hora.

Verificar com cuidado:

* orientacao e eventual transposicao da imagem;
* coordenadas `StartX/StartY` da ROI;
* tipo do array (`uint8` ou `uint16`);
* pedestal/normalizacao;
* se a assinatura da ilha continua compativel;
* se as matrizes precisam ser refeitas. Se orientacao, escala ou ROI mudarem,
  refazer a calibracao antes de permitir movimento automatico.

### Paralelizacao

Uma fila produtor-consumidor tambem pode ser testada:

* thread 1 captura continuamente;
* thread 2 processa apenas o frame mais recente, descartando atraso acumulado;
* thread 3 mantem o controle do mount;
* watchdog e CSV continuam independentes.

Nao permitir fila crescente de frames: para tracking importa a imagem mais
recente, nao processar imagens antigas. Essa paralelizacao deve ser feita depois
do backend SDK funcionar, pois o CM em ROI moderada custa poucos milissegundos e
o ganho de pipeline no ASCOM tende a ser pequeno.

### Estado atual para o proximo teste

O tracker ASI passou a usar ROI `384 x 384`, mostra Hz reais e grava telemetria
CSV. Antes de migrar para o SDK, usar esses dados como baseline. O teste local
mediu aproximadamente `3.6 ms/frame` para o processamento do CM em ROI 384;
portanto, a primeira investigacao deve continuar sendo captura/transferencia.

## Ideia importante para o futuro

O autotune de dois telescopios deve ser tratado como um teste de rejeicao de perturbacao do experimento completo:

`telescopio 2 move o feixe -> telescopio 1 corrige com o tracker -> camera mede erro residual`

Isso deve produzir parametros mais uteis para acoplamento na fibra do que o autotune antigo.

## Controle futuro usando potencia da fibra

Data da anotacao: 2026-07-08.

A potencia medida no powermeter pode ser usada para otimizar e manter o acoplamento na fibra, mas nao funciona como um PID direto simples.

No tracker da camera, o erro tem direcao:

`erro_px = posicao_alvo - posicao_laser`

Esse erro diz para qual lado mover o mount. Se o laser esta a direita do alvo, o controle sabe que precisa mover no sentido oposto.

No powermeter, a potencia e uma medida escalar:

`erro_potencia = potencia_alvo - potencia_medida`

Esse valor diz que o acoplamento esta ruim ou bom, mas nao diz se o melhor movimento e `+Az`, `-Az`, `+Alt`, `-Alt` ou uma diagonal. Portanto, um PID direto usando apenas `potencia_alvo - potencia_medida` ficaria cego para a direcao.

### Estrategia mais correta

Usar a potencia para estimar a inclinacao local da superficie de acoplamento:

1. Medir a potencia atual `P0`.
2. Testar um pequeno movimento `+Az` e medir `P(+Az)`.
3. Testar `-Az` e medir `P(-Az)`.
4. Estimar `dP/dAz`.
5. Repetir para `+Alt` e `-Alt`, estimando `dP/dAlt`.
6. Mover na direcao em que a potencia aumenta.

Isso e mais parecido com:

* hill climbing;
* gradient ascent;
* extremum seeking control;
* lock-in com dither.

### Versao discreta atual

O script `otimizacao/otimizar_receptor_local_pm100.py` faz uma busca local discreta:

* mede a potencia atual;
* testa vizinhos em Az/Alt;
* volta para a posicao inicial de cada teste;
* escolhe o vizinho de maior potencia;
* aceita esse movimento;
* repete com passos menores.

Essa versao e lenta, mas segura e reversivel para bancada.

### Possivel versao futura continua

Criar uma malha de dither:

* aplicar uma pequena oscilacao em Az e/ou Alt;
* medir se a potencia oscila em fase ou contra-fase com o dither;
* usar isso para descobrir o sinal do gradiente;
* mover lentamente o mount no sentido que aumenta a potencia;
* reduzir o passo perto do pico.

Essa abordagem poderia manter o acoplamento no pico mesmo se o feixe derivar lentamente.

### Estrategia com dois telescopios

Quando os dois mounts estiverem disponiveis:

* receptor: ajuste fino e rapido, usando camera/tracker e powermeter;
* emissor: ajuste grosso/lento, procurando colocar o feixe dentro da regiao de captura do receptor;
* depois que o receptor achar o pico de potencia, salvar a posicao do spot na camera como novo alvo de alinhamento;
* o tracker deve manter o spot nesse alvo salvo, nao necessariamente no centro geometrico da camera.

Uma rotina promissora:

1. Usar camera/tracker para manter o spot no alvo salvo.
2. Fazer busca por potencia no receptor.
3. Salvar o pico encontrado como `alvo_alinhamento_camera.json`.
4. Usar o emissor para melhorar a potencia global.
5. Refazer ajuste fino no receptor.
6. Repetir ate a melhora ficar pequena.
