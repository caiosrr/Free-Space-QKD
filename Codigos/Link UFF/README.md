# Perfil IDS do Link UFF-CBPF

Esta pasta guarda a documentacao e os resultados do enlace. Os programas
executaveis foram centralizados em `../programas_principais/` e a configuracao
da IDS fica com os demais modulos internos.

## Configuracao

Os valores em uso ficam em `../modulos/configuracoes/camera_ids.py`. Abra o
arquivo para ver ou alterar exposicao, FPS, ganhos e orientacao. Este README
nao repete os numeros de proposito: um valor copiado aqui envelhece sem aviso e
passa a contradizer o codigo.

Feche o IDS peak Cockpit antes de executar Python, pois a camera pode estar em
uso exclusivo.

## Programas

A partir da pasta `Codigos`:

```powershell
python .\programas_principais\testar_camera_ids.py
python .\programas_principais\caracterizar_beacon_ids.py --minutes 10
python .\programas_principais\centro_de_massa.py --camera ids
python .\programas_principais\calibracao.py --camera ids --perfil robusto
python .\programas_principais\tracker.py --camera ids --horas 0.5
```

Nos tres ultimos, `--camera ids` substitui a pergunta inicial. Todos tambem
podem ser abertos e executados pelo botao Play do VS Code, respondendo aos
prompts.

## Resultados

Todos os artefatos IDS continuam isolados em `Link UFF/resultados/`:

- `aquisicao/`: teste isolado da camera;
- `caracterizacao_beacon/`: telemetria sem movimento do mount;
- `centro_de_massa/`: observacao e alinhamento;
- `calibracao/`: auditorias e metadados;
- `matrizes/`: matrizes lidas pelo alinhamento e tracker;
- `tracker/`: CSV, resumo e imagens de eventos.

O tracker IDS nao usa matrizes da ASI como fallback. O prefixo procurado
depende de `ROTATE_IMAGE_180`: com `False`, procura `ids_raw_foco_temp_`; com
`True`, `ids_foco_temp_`. Ao mudar a orientacao, calibre novamente.

## Operacao segura

O tracker seleciona manualmente uma ilha e usa a matriz da calibracao continua.
Ele calcula o erro sobre uma media temporal, para imediatamente em perda de
sinal e exige varios frames coerentes para recuperar. Os tempos de media, de
recuperacao e o limite de ausencia estao em
`../modulos/configuracoes/tracker.py`; depois desse limite, encerra sem busca
ou retorno cego.

A IDS usa ROI nativa e alinha tamanho/offset aos incrementos exigidos pela
U3-3680XCP-NIR. Falhas persistentes de captura encerram o programa para impedir
movimento sem imagem.

Calibracao e tracker podem mover o telescopio. Antes de iniciar, confirme folga
mecanica, comunicacao, matriz e acesso a uma parada fisica. `Q`, `Esc` e
`Ctrl+C` solicitam velocidade zero, mas essa protecao depende de Windows,
rede, ASCOM e driver ainda responderem.

Detalhes da caracterizacao temporal: consulte
`../programas_principais/GUIA_CARACTERIZACAO.md`.
