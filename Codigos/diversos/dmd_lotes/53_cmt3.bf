# Troca a tabela Degamma/CMT (27h) para 3 e le de volta (28h). Volatil: vale
# ate desligar o DMD. A original e a 0; o 50_cmt0.bf volta para ela. Se a leitura
# nao devolver 0x03, a tabela 3 nao existe na flash.
echo Degamma/CMT 3
w 36 27 03
delay 1F4
w 36 28
r 36 1
