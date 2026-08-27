# Free-Space-QKD

Software de aquisicao, calibracao, alinhamento e tracking para o enlace optico
em espaco livre.

## Onde comecar

Abra `programas_principais/`. Essa pasta contem somente os cinco programas
usados na operacao e um guia curto com a ordem recomendada.

```powershell
python .\programas_principais\testar_camera_ids.py
python .\programas_principais\caracterizar_beacon_ids.py --minutes 10
python .\programas_principais\centro_de_massa.py
python .\programas_principais\calibracao.py
python .\programas_principais\tracker.py
```

Todos tambem podem ser executados pelo botao Play do VS Code.

## Estrutura

- `programas_principais/`: os cinco iniciadores usados no laboratorio.
- `modulos/`: implementacao interna, cameras, configuracoes e seguranca.
- `diversos/`: ferramentas, otimizacao, estudos, testes e legado.
- `resultados/`: artefatos locais da ASI e execucoes gerais.
- `Link UFF/`: documentacao e resultados do enlace UFF-CBPF.

Arquivos internos nao precisam ser abertos para executar o experimento.

## Configuracao das cameras

ASI/ASCOM:

```text
modulos/configuracoes/camera_asi.py
```

IDS:

```text
modulos/configuracoes/camera_ids.py
```

A calibracao usa ZWO SDK para a ASI e IDS peak para a IDS. O tracker usa
ASI/ASCOM ou IDS peak. Ganho e exposicao permanecem fixos durante cada sessao.

## Resultados

O modulo `modulos/artefatos.py` centraliza os caminhos gerais. Os iniciadores
IDS aplicam o perfil `modulos/configuracoes/camera_ids.py`, portanto continuam lendo e
gravando exclusivamente em `Link UFF/resultados/`. Essa separacao evita usar
por engano uma matriz produzida por outra camera ou montagem.

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
