# Legado

Esta pasta nao participa da operacao normal do sistema.

- `calibracao_por_pontos.py`: calibracao antiga por deslocamentos discretos,
  preservada como referencia e para os testes de seguranca e selecao de ROI.

O controlador PID antigo foi removido porque nao era importado por nenhum
programa e foi substituido pelo controle atual em
`modulos/controle/mount_control.py`.
