# Teste da camera IDS U3-3680XCP

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
frame em `Link UFF\resultados\teste_ids.png`. Os valores padrao copiam o teste
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
