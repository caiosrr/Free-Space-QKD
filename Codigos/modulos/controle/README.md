# Modulos de controle

O tracker e iniciado por `programas_principais/tracker.py`. O arquivo
`Tracker.py` mostra o fluxo completo da sessao sem misturar os detalhes dos
algoritmos.

## Roteiro do tracker

1. `tracker_camera.py` conecta a camera, permite escolher a ilha e aplica a ROI.
2. `tracker_qualidade.py` rejeita mudancas opticas bruscas da mesma ilha.
3. `tracker_aquisicao.py` forma a media temporal apenas com frames confiaveis.
4. `tracker_estado.py` compartilha a medida com as outras threads.
5. `tracker_loop.py` converte o erro em pixels em velocidade de Az/Alt.
6. `tracker_seguranca.py` vigia tempo, deslocamento e retorno seguro.
7. `tracker_telemetria.py` grava CSV, resumo e imagens dos eventos.
8. `tracker_interface.py` desenha a janela sem interferir no controle.

## Qualidade optica

A referencia de intensidade integrada, area e forma e a mediana movel dos
ultimos 5 s aceitos. Uma mudanca gradual acompanha essa referencia. Um salto
grande de intensidade, area, largura, altura, compacidade ou assinatura nao
entra na media temporal e mantem o mount parado.

Depois de uma anomalia ou perda de sinal, a aparencia precisa permanecer normal
por 3 s. So entao a media de 2 s e reconstruida e o controle volta a receber
medidas. O CSV registra as razoes relativas e o motivo; o inicio e a recuperacao
tambem geram imagens de evento.

## Autoteste temporario

Antes da sessao, o tracker oferece um autoteste opcional, desativado por
padrao. Depois que o operador trava a ilha, `tracker_autoteste.py` desloca o
mount o equivalente a cerca de 10 px sem alterar o alvo salvo. A sessao normal
so continua quando a malha confirma o deslocamento e recupera a zona de 1,5 px;
falha ou timeout param o mount e acionam o retorno seguro.

Esse modulo e temporario. Para remove-lo depois dos testes, retire o prompt e
as chamadas em `Tracker.py`, os campos `preflight_*` de `tracker_estado.py` e as
constantes `PREFLIGHT_*` da configuracao.

## Arquivos auxiliares

- `tracker_controle.py`: matematica do controlador PD e do trim lento.
- `mount_control.py`: unica camada que envia comandos ao mount.
- `alvo_alinhamento.py`: estrutura e persistencia do alvo selecionado.
- `cameras/`: backends ASCOM/Alpaca, IDS peak e ZWO SDK.

Para estudar ou alterar o comportamento, comece por `Tracker.py` e siga apenas
o modulo correspondente ao assunto. Ganhos e limites de seguranca permanecem
centralizados em `modulos/configuracoes/tracker.py`.
