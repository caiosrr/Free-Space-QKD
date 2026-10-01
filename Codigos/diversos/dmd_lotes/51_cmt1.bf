# Troca a tabela Degamma/CMT (27h) para 1 e le de volta (28h). Volatil: vale
# ate desligar o DMD. A original e a 0; o 50_cmt0.bf volta para ela. Se a leitura
# nao devolver 0x01, a tabela 1 nao existe na flash.
echo Degamma/CMT 1
w 36 27 01
delay 1F4
w 36 28
r 36 1
