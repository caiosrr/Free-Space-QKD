# Estudos dos codigos do projeto

Esta pasta separa o aprendizado do sistema experimental. Os exercicios usam
dados sinteticos e **nao conectam camera nem mount**.

## Roteiro

1. Centro de massa de uma imagem.
2. Threshold e rejeicao do fundo.
3. Ilhas (componentes conexos) e escolha do beacon.
4. ROI, bordas e continuidade temporal.
5. Calibracao linear: movimento angular -> deslocamento em pixels.
6. Minimos quadrados, ruido, outliers e ajuste robusto.
7. Aplicacao da matriz inversa no tracker.

O estudo sera feito em duas etapas para cada assunto:

1. implementar uma versao pequena a partir da definicao matematica;
2. localizar e explicar as decisoes adicionais do codigo experimental.

## Estudo 01 — centro de massa e threshold

Arquivo: `01_centro_de_massa.py`

Execute pelo botao **Play** do VS Code ou pelo terminal, a partir da raiz do
repositorio:

```powershell
& ".\Codigos\.venv\Scripts\python.exe" ".\Codigos\estudos\01_centro_de_massa.py"
```

Em uma imagem, a intensidade de cada pixel funciona como uma massa. Para um
pixel de coordenadas `(x, y)` e intensidade `I(x, y)`:

```text
x_cm = soma(x * I) / soma(I)
y_cm = soma(y * I) / soma(I)
```

Lembre que, em um array `frame[y, x]`, `y` e a linha e `x` e a coluna.

### Tarefas

1. Complete `centro_de_massa_loops` usando apenas lacos `for`.
2. Complete `aplicar_limiar_relativo`.
3. Complete `centro_de_massa_numpy` usando operacoes vetorizadas.
4. Execute o arquivo depois de cada etapa. O proprio programa verifica casos
   conhecidos e informa quais etapas passaram.
5. Explique com suas palavras por que o centro pode ter coordenadas decimais,
   mesmo que cada pixel tenha coordenadas inteiras.

Nao comece copiando `modulos/visao/detector_ilhas.py`. A intencao e reconstruir
o nucleo do algoritmo e somente depois comparar as duas implementacoes.

## Relacao com o codigo experimental

Depois deste primeiro estudo, compare sua solucao com estas funcoes:

- `modulos/visao/detector_ilhas.py::_as_gray_float`;
- `modulos/visao/detector_ilhas.py::_centro_massa_padrao`.

A versao experimental acrescenta conversao para tons de cinza, `float32`,
threshold relativo, deteccao de borda e protecoes para sinal vazio. Depois ela
vai alem: separa ilhas, mede assinaturas e preserva a identidade do beacon.
