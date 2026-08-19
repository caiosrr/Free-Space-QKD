# Free-Space-QKD

Controle, calibracao e tracking para testes de apontamento com telescopios, camera Alpaca/ASCOM e power meter.

## Estrutura

- `controle/`: codigo principal de conexao, movimento, tracking, agente remoto e utilitarios compartilhados.
- `controle/mount_control.py`: movimento principal do mount local; `mov_simultaneo.py` e apenas alias de compatibilidade.
- `calibracoes/Calibracao_ang-pix_dual_v3.py`: calibracao angular-pixel principal para um foco.
- `foco_multiplos/`: fluxo para imagens com dois ou mais focos/reflexoes; tambem funciona para foco unico e pode virar o padrao.
- `calibracoes/autotune/`: autotunes, validacoes e buscas de parametros.
- `calibracoes/legado/`: versoes antigas ou experimentais mantidas como referencia.
- `otimizacao/`: scripts que usam power meter/camera como metrica para maximizar acoplamento.
- `ferramentas/`: scripts de bancada/diagnostico, como definir alvo da fibra e diagnosticar mounts.
- `resultados/matrizes/`: matrizes usadas pelos scripts.
- `resultados/json/`: resultados de execucao e logs em JSON.
- `artifact_paths.py`: helper central para salvar/carregar artefatos em `resultados/` sem espalhar caminhos fixos pelos scripts.
- `Anotaçoes/` e `notas.md`: notas de continuidade do experimento.

## Comandos Uteis

```powershell
python .\foco_multiplos\Center_of_Mass_foco_temp.py
python .\foco_multiplos\calibracao_foco.py
python .\controle\Tracker.py
python .\controle\mount_control.py
python .\controle\mov_mount_remoto.py
python .\ferramentas\definir_alvo_fibra.py
python .\ferramentas\diagnostico_mounts.py
python .\calibracoes\autotune\autotune_mov_mount_remoto.py
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

Essa configuracao e usada por `Center_of_Mass_foco_temp.py`, pela calibracao de
foco multiplo e por `controle/Tracker.py`. O fluxo IDS/Link UFF continua usando
seu proprio `Link UFF/config_camera_ids.py`.

A captura ASI tenta automaticamente a transferencia binaria ImageBytes e volta
para JSON se o ASCOM Remote Server nao a oferecer. Depois de atualizar o
repositorio, instale a dependencia com `python -m pip install -r requirements.txt`.

Ao iniciar uma calibracao, `auditoria_foco_temp` e limpa e recebe uma unica
pasta `calibracao_AAAA-MM-DD_HH-MM-SS`. Ela inclui as imagens, a posicao inicial
do mount e `resumo_execucao.json` com os tempos medidos.

A calibracao pergunta se o ensaio e no link longo UFF-CBPF. Respondendo `s`,
ela usa cinco frames por medicao, mediana robusta e limites separados para drift
e jitter atmosfericos. O movimento fine passa de `0.010` para `0.015` grau para
melhorar a relacao sinal/drift. Respondendo `n` ou Enter, preserva o perfil de
laboratorio.

Quando houver predios ou varias regioes luminosas, escolha `1=escolher ilha +
ROI do tracker` na pergunta de medicao. O programa mostra as ilhas em amarelo:
clique na desejada e pressione Enter. A assinatura e a geometria dessa ilha
ficam congeladas. Para a ASI, a ROI padrao e `384 x 384` pixels e a calibracao
fine combina os raios `0.004`, `0.008` e `0.016` grau. Antes de mover o mount,
cinco frames validam se a ilha continua estavel. A matriz local e salva como
fine e coarse para o tracker nao misturar o resultado novo com uma coarse antiga.

ROI, raios, zona de repouso, duracao maxima, limites absolutos e frequencia do
CSV ficam centralizados em `config_tracker.py`. Cada sessao cria uma pasta em
`resultados/debug/sessoes/tracker_AAAA-MM-DD_HH-MM-SS` com `telemetria.csv`,
`resumo.json` e uma imagem se houver evento de seguranca. Ao atingir o limite de
tempo, perder o sinal por tempo excessivo, tocar a borda da ROI ou se afastar
mais que o limite absoluto, o tracker para, retorna devagar a posicao inicial e
encerra.

## Observacoes

Arquivos de auditoria, imagens, videos e JSONs de resultado ficam ignorados pelo git para evitar commits muito grandes. As matrizes `.npy` pequenas ficam versionadas porque sao uteis para repetir testes com o tracker.
