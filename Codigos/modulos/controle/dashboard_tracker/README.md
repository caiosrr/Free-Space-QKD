# Painel local do tracker

O tracker inicia este painel automaticamente em `http://127.0.0.1:8765`.
Ele mostra a média temporal da ROI, um resumo estável do estado compartilhado e
um histórico de 120 segundos construído no navegador. A imagem e os números são
atualizados apenas uma vez por segundo; somente o gráfico corre continuamente.

O painel é deliberadamente somente leitura: não mede a ilha, não modifica a
calibração e não envia comandos ao mount. A janela OpenCV antiga permanece ativa
como redundância e continua aceitando `Q` e `Esc` para encerrar. O terminal
também continua aceitando `Ctrl+C`.

No painel, `Y` é cartesiano (positivo para cima). O processamento interno e o
CSV preservam a convenção de imagem (linha positiva para baixo).
