# Perfil IDS do Link UFF-CBPF

Esta pasta guarda a documentacao e os resultados do enlace. Os programas
executaveis foram centralizados em `../programas_principais/` e a configuracao
da IDS fica com os demais modulos internos.

## Configuracao

Edite `../modulos/configuracoes/camera_ids.py` para alterar:

```python
EXPOSURE_US = 7276.0
FRAME_RATE_FPS = 20.0
ANALOG_GAIN = 1.0
DIGITAL_GAIN = 1.0
ROTATE_IMAGE_180 = False
```

Feche o IDS peak Cockpit antes de executar Python, pois a camera pode estar em
uso exclusivo.

## Programas

A partir da pasta `Codigos`:

```powershell
python .\programas_principais\testar_camera_ids.py
python .\programas_principais\caracterizar_beacon_ids.py --minutes 10
python .\programas_principais\centro_de_massa.py
python .\programas_principais\calibracao.py
python .\programas_principais\tracker.py
```

Nos tres ultimos, escolha IDS quando o programa perguntar pela camera. Todos
podem ser abertos diretamente e executados pelo botao Play do VS Code.

## Resultados

Todos os artefatos IDS continuam isolados em `Link UFF/resultados/`:

- `aquisicao/`: teste isolado da camera;
- `caracterizacao_beacon/`: telemetria sem movimento do mount;
- `centro_de_massa/`: observacao e alinhamento;
- `calibracao/`: auditorias e metadados;
- `matrizes/`: matrizes lidas pelo alinhamento e tracker;
- `tracker/`: CSV, resumo e imagens de eventos.

O tracker IDS nao usa matrizes da ASI como fallback. Com
`ROTATE_IMAGE_180 = False`, procura matrizes com prefixo
`ids_raw_foco_temp_`; ao mudar a orientacao, calibre novamente.

## Operacao segura

O tracker seleciona manualmente uma ilha e usa a matriz da calibracao continua.
Ele calcula o erro sobre uma media temporal de `2 s`, para imediatamente em
perda de sinal e exige cinco frames coerentes para recuperar. Depois de `75 s`
sem sinal, encerra sem busca ou retorno cego.

A IDS usa ROI nativa e alinha tamanho/offset aos incrementos exigidos pela
U3-3680XCP-NIR. Falhas persistentes de captura encerram o programa para impedir
movimento sem imagem.

Calibracao e tracker podem mover o telescopio. Antes de iniciar, confirme folga
mecanica, comunicacao, matriz e acesso a uma parada fisica. `Q`, `Esc` e
`Ctrl+C` solicitam velocidade zero, mas essa protecao depende de Windows,
rede, ASCOM e driver ainda responderem.

Detalhes da caracterizacao temporal: consulte
`../programas_principais/GUIA_CARACTERIZACAO.md`.
