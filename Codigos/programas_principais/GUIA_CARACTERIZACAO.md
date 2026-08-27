# Caracterizacao temporal do beacon IDS

Este programa mede a variacao da luz sem conectar ou movimentar o mount. Ele
usa a selecao manual e a trava de identidade do tracker para acompanhar apenas
o beacon escolhido.

Antes de executar, feche o IDS peak Cockpit. No terminal aberto na pasta
`Codigos`, ative o ambiente virtual e rode:

```powershell
python ".\programas_principais\caracterizar_beacon_ids.py" --minutes 10
```

Opcoes:

```powershell
# Ensaio de uma hora
python ".\programas_principais\caracterizar_beacon_ids.py" --minutes 60

# Executar ate Q, Esc ou Ctrl+C
python ".\programas_principais\caracterizar_beacon_ids.py" --minutes 0

# Usar ROI maior
python ".\programas_principais\caracterizar_beacon_ids.py" --minutes 30 --roi 640
```

Os parametros de exposicao, FPS e ganho continuam centralizados em
`modulos/configuracoes/camera_ids.py`.

Cada execucao cria uma pasta em
`Link UFF/resultados/caracterizacao_beacon/sessoes`. Ela contem:

- `telemetria.csv`: uma linha para cada frame ou tentativa de captura;
- `eventos.csv`: indice dos eventos e das imagens relacionadas;
- `eventos/`: frame bruto e frame marcado de perdas, recuperacoes, saltos e
  mudancas bruscas de intensidade ou tamanho;
- `media_todos_frames.npy/png`: media temporal de tudo que a camera recebeu;
- `media_frames_validos.npy/png`: media apenas quando o beacon foi reconhecido;
- `media_normalizada_frames_validos.npy/png`: media com peso semelhante para
  cada frame valido, reduzindo o dominio dos instantes mais luminosos;
- `metadados.json`: camera, ROI, alvo e limiares utilizados;
- `resumo.json`: duracao, taxa valida, estatisticas e contagem de eventos.

O CSV e fechado normalmente ao pressionar `Ctrl+C`, `Q` ou `Esc`. Durante uma
falha de camera, as linhas anteriores ja permanecem gravadas. Os eventos usam
um intervalo minimo entre imagens do mesmo tipo para uma oclusao longa nao
encher o disco.

## Protecao de espaco em disco

Uma sessao possui limites rigidos definidos no inicio do programa:

- ate `512 MB` para `telemetria.csv`;
- ate `200` conjuntos de imagens de eventos;
- ate `256 MB` somados nas imagens de eventos;
- encerramento seguro se o disco ficar com menos de `2 GB` livres.

Quando o limite de imagens e atingido, os eventos continuam registrados no CSV,
mas novos PNGs deixam de ser criados. Se a telemetria ou o espaco livre atingir
o limite, a aquisicao e encerrada normalmente e o resumo e as medias sao salvos.

Este primeiro programa mede a perturbacao em pixels. Depois de adquirir dados
reais, a analise offline podera calcular PSD, autocorrelacao e desempenho de
diferentes janelas temporais antes de alterar o tracker.
