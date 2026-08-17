# Teste da camera IDS U3-3680XCP

## Configuracao da imagem

Altere os parametros em `Link UFF\config_camera_ids.py`. No inicio do arquivo
ficam reunidos os valores usados por todos os programas IDS:

```python
EXPOSURE_US = 7276.0       # 7.276 ms
FRAME_RATE_FPS = 20.0
ANALOG_GAIN = 1.0
DIGITAL_GAIN = 1.0
```

Depois de salvar o arquivo, teste novamente a aquisicao. Nao e necessario
editar separadamente centro de massa, calibracao ou tracker. As opcoes de linha
de comando do `teste_camera_ids.py` ainda podem sobrescrever temporariamente os
valores apenas naquele teste.

Todos os artefatos da IDS ficam isolados em `Link UFF\resultados`:

- `aquisicao`: imagem do teste isolado da camera;
- `centro_de_massa`: frames marcados e ultimo frame;
- `calibracao`: auditorias e metadados da calibracao;
- `matrizes`: matrizes produzidas pela calibracao e lidas pelo centro de massa e tracker;
- `tracker`: frame de teste da ROI.

Quando os executaveis IDS desta pasta sao usados, centro de massa e tracker
procuram as matrizes exclusivamente em `Link UFF\resultados\matrizes`. Eles nao
usam como fallback uma calibracao da ASI ou de outro arranjo.

Este teste acessa somente a camera pelo IDS peak. Ele nao conecta nem movimenta
o mount.

Antes de executar, feche o IDS peak Cockpit, pois ele pode manter a camera em
uso exclusivo. No PowerShell, a partir da pasta `Codigos`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python ".\Link UFF\teste_camera_ids.py"
```

O programa captura 50 frames por padrao, mostra a taxa medida e salva o ultimo
frame em `Link UFF\resultados\aquisicao\teste_ids.png`. Os valores padrao copiam o teste
feito no IDS peak Cockpit:

- exposicao: `7276 us` (`7.276 ms`);
- frame rate: `20 fps`;
- ganho analogico: `1`;
- ganho digital: `1`;
- exposicao e ganho automaticos desligados.

Opcoes uteis:

```powershell
python ".\Link UFF\teste_camera_ids.py" --frames 200 --exposure-us 100
python ".\Link UFF\teste_camera_ids.py" --fps 10 --analog-gain 2 --digital-gain 1
python ".\Link UFF\teste_camera_ids.py" --list-only
```

`--exposure-us` usa microssegundos. O programa limita automaticamente o valor
ao intervalo aceito pela camera e informa o valor realmente aplicado.

Em algumas variantes NIR, `ExposureAuto` e `GainAuto` nao sao expostos como
parametros gravaveis. Nesse caso o teste informa isso e continua, pois exposicao
e ganhos sao escritos diretamente nos respectivos parametros manuais.

## Centro de massa e calibracao com a IDS

Os executaveis abaixo reutilizam a logica de `foco_multiplos` e o mesmo
`controle/mount_control.py`, trocando apenas a aquisicao Alpaca pelo IDS peak.
Feche o IDS peak Cockpit antes de executar.

Centro de massa/centralizacao:

```powershell
python ".\Link UFF\Center_of_Mass_foco_temp_IDS.py"
```

Calibracao angular-pixel:

```powershell
python ".\Link UFF\Calibracao_ang_pix_foco_temp_IDS.py"
```

Tracker continuo (somente depois de calibrar com a IDS):

```powershell
python ".\Link UFF\Tracker_IDS.py"
```

Ambos usam por padrao `7276 us`, `20 fps`, ganho analogico `1`, ganho digital
`1`, sensor completo e `Mono8`. A calibracao movimenta o mount e deve ser feita
com o spot visivel, folga mecanica disponivel e possibilidade de interromper com
`Ctrl+C`.

Os programas originais continuam usando a camera Alpaca/ASI. O backend IDS so e
selecionado pelos executaveis desta pasta.

O tracker IDS usa ROI nativa de `192 x 192` pixels. Esse tamanho respeita os
incrementos de largura da U3-3680XCP-NIR. As posicoes da ROI tambem sao
alinhadas aos passos de hardware (`OffsetX=8`, `OffsetY=2`). Antes do primeiro
uso, gere as matrizes com a calibracao IDS e teste com velocidade/erro pequenos,
mantendo `q` ou `Ctrl+C` prontos para interromper.

Por seguranca, a calibracao IDS salva matrizes separadas com prefixo
`ids_foco_temp_`. O tracker IDS se recusa a iniciar se essas matrizes ainda nao
existirem, em vez de usar acidentalmente as matrizes antigas da ASI.

Ao receber `Ctrl+C` ou sair normalmente, centro de massa, calibracao e tracker
tentam enviar velocidade zero ate duas vezes e de forma independente para cada eixo.
Essa protecao depende de o Windows, a rede, o ASCOM Remote e o driver ainda
estarem respondendo; ela nao substitui parada fisica, limite mecanico ou corte de
energia acessivel ao operador.

A calibracao tambem registra a posicao absoluta inicial do mount em
`posicao_inicial_mount.json`, dentro da auditoria da execucao. Ao terminar, ser
interrompida com `Ctrl+C` ou encontrar um erro, ela tenta retornar a essa posicao
e verifica o erro final com a mesma tolerancia de `0.0005 deg` usada pelo controle.
Por seguranca, um retorno maior que `0.25 deg` em qualquer
eixo e recusado; nesse caso o operador deve conferir a situacao antes de mover.
Um segundo `Ctrl+C` durante o retorno interrompe o retorno e manda parar os eixos.
