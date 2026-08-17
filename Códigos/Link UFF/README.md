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
