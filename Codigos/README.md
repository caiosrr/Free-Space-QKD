# Free-Space-QKD

Software de aquisicao, calibracao, alinhamento e tracking para o enlace optico
em espaco livre.

## Onde comecar

Abra `programas_principais/`. Essa pasta contem somente os cinco programas
usados na operacao e um guia curto com a ordem recomendada.

```powershell
python .\programas_principais\testar_camera_ids.py
python .\programas_principais\caracterizar_beacon_ids.py --minutes 10
python .\programas_principais\centro_de_massa.py --camera ids
python .\programas_principais\calibracao.py --camera ids --perfil robusto
python .\programas_principais\tracker.py --camera ids --horas 0.5
```

Sem argumentos, todos perguntam o que precisam, entao o botao Play do VS Code
continua funcionando. Com argumentos, rodam sem prompt, o que permite relancar
uma sessao por script depois de uma queda de conexao remota.

## Estrutura

- `programas_principais/`: os cinco iniciadores usados no laboratorio.
- `modulos/`: implementacao interna, cameras, configuracoes e seguranca.
- `diversos/`: ferramentas, otimizacao, estudos, testes e legado.
- `resultados/`: artefatos locais da ASI e execucoes gerais.
- `Link UFF/`: documentacao e resultados do enlace UFF-CBPF.

Arquivos internos nao precisam ser abertos para executar o experimento.

## Configuracao

Os valores de camera ficam em arquivos Python, nao neste README, para nao
existirem dois numeros diferentes para a mesma coisa:

| Assunto | Arquivo |
|---|---|
| Camera ASI (ganho, exposicao) | `modulos/configuracoes/camera_asi.py` |
| Camera IDS (exposicao, FPS, ganhos, orientacao) | `modulos/configuracoes/camera_ids.py` |
| Endereco do ASCOM Remote/Alpaca | `modulos/configuracoes/alpaca.py` |
| Ganhos, zonas e limites do tracker | `modulos/configuracoes/tracker.py` |

Camera e mount usam o mesmo endereco Alpaca. Se um dos parametros de camera nao
for aplicado por algum caminho de importacao, o valor de fallback e o proprio
valor desses arquivos: nao existe mais um literal escondido diferente deles.

A calibracao usa ZWO SDK para a ASI e IDS peak para a IDS. O tracker usa
ASI/ASCOM ou IDS peak. Ganho e exposicao permanecem fixos durante cada sessao,
exceto pela autoexposicao da IDS, controlada em `configuracoes/tracker.py`.

## Resultados

O modulo `modulos/artefatos.py` centraliza os caminhos gerais. Os iniciadores
IDS aplicam o perfil `modulos/configuracoes/camera_ids.py`, portanto continuam
lendo e gravando exclusivamente em `Link UFF/resultados/`. Essa separacao evita
usar por engano uma matriz produzida por outra camera ou montagem.

Imagens, CSVs, auditorias e videos sao ignorados pelo Git. Matrizes pequenas
podem ser versionadas quando forem necessarias para reproduzir um teste.

## Desenvolvimento

Execute os testes a partir de `Codigos`:

```powershell
python -m unittest discover -s diversos/testes
```

Antes de mover ou renomear um modulo interno, procure suas importacoes com
`rg`. Os programas principais devem permanecer curtos: eles escolhem o perfil
de hardware e chamam a implementacao correspondente.
