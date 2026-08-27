# Modulos de controle

O tracker e iniciado por `programas_principais/tracker.py`. O arquivo
`Tracker.py` mostra o fluxo completo da sessao sem misturar os detalhes dos
algoritmos.

## Roteiro do tracker

1. `tracker_camera.py` conecta a camera, permite escolher a ilha e aplica a ROI.
2. `tracker_aquisicao.py` valida cada frame e forma a media temporal do beacon.
3. `tracker_estado.py` compartilha a medida com as outras threads.
4. `tracker_loop.py` converte o erro em pixels em velocidade de Az/Alt.
5. `tracker_seguranca.py` vigia tempo, deslocamento e retorno seguro.
6. `tracker_telemetria.py` grava CSV, resumo e imagens dos eventos.
7. `tracker_interface.py` desenha a janela sem interferir no controle.

## Arquivos auxiliares

- `tracker_controle.py`: matematica do controlador PD e do trim lento.
- `mount_control.py`: unica camada que envia comandos ao mount.
- `alvo_alinhamento.py`: estrutura e persistencia do alvo selecionado.
- `cameras/`: backends ASCOM/Alpaca, IDS peak e ZWO SDK.

Para estudar ou alterar o comportamento, comece por `Tracker.py` e siga apenas
o modulo correspondente ao assunto. Ganhos e limites de seguranca permanecem
centralizados em `modulos/configuracoes/tracker.py`.
