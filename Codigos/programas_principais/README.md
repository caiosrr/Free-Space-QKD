# Programas principais

Esta e a pasta para abrir no VS Code quando for executar o experimento. Os
arquivos daqui sao iniciadores; a implementacao fica nas pastas internas.

| Programa | Funcao | Move o mount? |
|---|---|---|
| `testar_camera_ids.py` | Testa aquisicao, ganho, exposicao e FPS da IDS | Nao |
| `caracterizar_beacon_ids.py` | Mede posicao, forma e intensidade do beacon ao longo do tempo | Nao |
| `centro_de_massa.py` | Seleciona a ilha e abre observacao/alinhamento de baixa frequencia | Depende da opcao escolhida |
| `calibracao.py` | Gera a matriz angular-pixel com ZWO SDK ou IDS | Sim |
| `tracker.py` | Executa o tracker principal com ASI/ASCOM ou IDS | Sim |

## Ordem recomendada

1. Testar a camera.
2. Caracterizar ou observar o beacon sem movimento.
3. Executar a calibracao.
4. Executar o tracker.

O tracker pergunta se deve executar um autoteste temporario de recuperacao.
Responder Enter ou `n` ignora o teste; `s` aplica um pequeno deslocamento antes
da sessao e exige que a mesma ilha volte ao centro.

Durante a sessao, mudancas bruscas no tamanho, intensidade ou forma da ilha sao
tratadas como anomalia optica: esses frames nao entram na media e o mount fica
parado ate a luz permanecer normal por alguns segundos.

Para usar o botao Play, abra o arquivo desejado. `calibracao.py`,
`centro_de_massa.py` e `tracker.py` perguntam qual camera usar.

Antes de qualquer programa que mova o telescopio, confirme folga mecanica,
matriz correta, comunicacao com o mount e acesso a uma parada fisica. `Q`,
`Esc` e `Ctrl+C` solicitam parada por software, mas nao substituem o limite
mecanico ou o corte de energia.

Os parametros da ASI ficam em `../modulos/configuracoes/camera_asi.py`. Os da IDS ficam
em `../modulos/configuracoes/camera_ids.py`. Resultados IDS continuam em
`../Link UFF/resultados/`.

## Auditoria da calibracao continua

Cada sessao salva em `varreduras/` os CSVs de frames individuais e bins
angulares, inclusive os bins rejeitados. Os frames ja adquiridos tambem sao
salvos quando a captura e interrompida, depois de solicitar a parada dos eixos.

`raw_centroid_spread_px` inclui o movimento durante cada bin. Ja
`centroid_spread_px` desconta uma tendencia linear robusta da varredura inteira
antes de medir a dispersao; nao e uma medida exclusiva de turbulencia.
`bin_duration_s` e `trend_x_px_s`/`trend_y_px_s` permitem auditar esse desconto.
As imagens e posicoes usadas na matriz nao sao corrigidas por essa tendencia.
Os limites residuais continuam 5 px (mediana) e 10 px (P90); as validacoes
de ida/volta e os testes independentes continuam necessarios.
