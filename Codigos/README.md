# Free-Space-QKD

Controle, calibracao e tracking para testes de apontamento com telescopios, camera Alpaca/ASCOM e power meter.

## Estrutura

- `controle/`: tracker, movimento do mount, agente remoto e memoria compartilhada do alvo.
- `controle/cameras/`: backends Alpaca/ASCOM, IDS e ZWO SDK.
- `controle/alvo_alinhamento.py`: biblioteca interna de coordenadas, ROI e assinatura do alvo; nao e executada diretamente.
- `controle/mount_control.py`: movimento principal do mount local.
- `calibracoes/calibracao_continua.py`: calibracao angular-pixel principal, com ZWO SDK e IDS.
- `foco_multiplos/`: fluxo para imagens com dois ou mais focos/reflexoes; tambem funciona para foco unico e pode virar o padrao.
- `calibracoes/autotune/`: autotunes, validacoes e buscas de parametros.
- `calibracoes/legado/calibracao_estrela.py`: calibracao antiga por pontos, mantida apenas como referencia.
- `otimizacao/`: scripts que usam power meter/camera como metrica para maximizar acoplamento.
- `ferramentas/`: scripts de bancada/diagnostico, como definir alvo da fibra e diagnosticar mounts.
- `resultados/matrizes/`: matrizes usadas pelos scripts.
- `resultados/json/`: resultados de execucao e logs em JSON.
- `artifact_paths.py`: helper central para salvar/carregar artefatos em `resultados/` sem espalhar caminhos fixos pelos scripts.
- `Anotaçoes/` e `notas.md`: notas de continuidade do experimento.

## Comandos Uteis

```powershell
python .\foco_multiplos\centro_massa.py
python .\calibracoes\calibracao_continua.py --camera zwo --perfil robusto
python .\controle\Tracker.py
python .\controle\mount_control.py
python .\controle\mount_agent_client.py
python .\ferramentas\definir_alvo_fibra.py
python .\ferramentas\diagnostico_mounts.py
python .\calibracoes\autotune\autotune_mount_control.py
python .\otimizacao\otimizar_acoplamento_pm100.py
```

## Configuracao da camera ASI

Os programas normais de foco multiplo e o tracker leem ganho e exposicao de
`config_camera_asi.py`. Os valores sao escritos na camera ASCOM ao conectar:

```python
GAIN = 1
EXPOSURE_US = 600.0
ALPACA_ADDRESS = "127.0.0.1:11111"
DEVICE_NUMBER = 0
```

Essa configuracao e usada por `foco_multiplos/centro_massa.py`, pela calibracao
continua ZWO e por `controle/Tracker.py`. O fluxo IDS/Link UFF continua usando
seu proprio `Link UFF/config_camera_ids.py`.

A captura ASI tenta automaticamente a transferencia binaria ImageBytes e volta
para JSON se o ASCOM Remote Server nao a oferecer. Depois de atualizar o
repositorio, instale a dependencia com `python -m pip install -r requirements.txt`.

## Calibracao continua

O executavel principal tem dois backends e dois perfis:

```powershell
python .\calibracoes\calibracao_continua.py --camera zwo --perfil robusto
python .\calibracoes\calibracao_continua.py --camera ids --perfil robusto
python .\calibracoes\calibracao_continua.py --camera zwo --perfil rapido
```

O perfil `rapido` usa quatro trajetorias de `0.008 deg`. O perfil `robusto`
repete as direcoes em outra ordem usando dados separados para validacao e
depois testa `0.014 deg`. A matriz do tracker continua sendo ajustada somente
na faixa local; o movimento maior serve para detectar nao linearidade. Cada
execucao salva CSV, matrizes candidatas e resumo em
`resultados/calibracao/continua/`. A matriz vigente so muda depois das
validacoes e de confirmacao explicita, com backup automatico.

Para a ZWO, instale o binding e o SDK oficial:

```powershell
python -m pip install -r requirements-zwo.txt
python .\calibracoes\calibracao_continua.py --camera zwo --perfil robusto --sdk-path "C:\caminho\ASICamera2.dll"
```

Feche ASIStudio e desconecte a camera do ASCOM antes de abri-la pelo SDK. O
mount continua no ASCOM; somente a aquisicao da camera usa USB/SDK direto.

ROI, raios, zona de repouso, duracao maxima, limites absolutos e frequencia do
CSV ficam centralizados em `config_tracker.py`. Cada sessao cria uma pasta em
`resultados/debug/sessoes/tracker_AAAA-MM-DD_HH-MM-SS` com `telemetria.csv`,
`resumo.json` e imagens limitadas dos eventos de perda/recuperacao. Ao atingir o
limite de tempo, tocar a borda da ROI ou se afastar mais que o limite absoluto,
o tracker para, retorna devagar a posicao inicial e encerra. A perda prolongada
e a excecao: sem referencia visual, ele encerra mantendo o mount parado.

O tracker usa uma soma temporal robusta de `2 s`: somente frames que continuam
pertencendo a ilha travada entram na imagem media, e o mount corrige o centro de
massa dessa media. Na oclusao, a velocidade vai imediatamente a zero. A luz
precisa reaparecer de forma coerente por cinco frames; apos `75 s` sem sinal, o
programa encerra mantendo o mount parado, sem realizar busca ou retorno cegos.
A exposicao automatica IDS esta disponivel em `config_tracker.py`, desligada por
padrao ate ser validada em observacao sem movimento.

## Observacoes

Arquivos de auditoria, imagens, videos e JSONs de resultado ficam ignorados pelo git para evitar commits muito grandes. As matrizes `.npy` pequenas ficam versionadas porque sao uteis para repetir testes com o tracker.
