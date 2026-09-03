# Painel local do tracker

O tracker inicia este painel automaticamente em `http://127.0.0.1:8765`.
Ele mostra o frame real da ROI, o estado compartilhado do controle e um histórico
de 120 segundos construído no navegador.

O painel é deliberadamente somente leitura: não mede a ilha, não modifica a
calibração e não envia comandos ao mount. A janela OpenCV antiga permanece ativa
como redundância e continua aceitando `Q` e `Esc` para encerrar. O terminal
também continua aceitando `Ctrl+C`.

No painel, `Y` é cartesiano (positivo para cima). O processamento interno e o
CSV preservam a convenção de imagem (linha positiva para baixo).
