# So leitura: o estado dos LEDs. A conversao de Cox e Drozdov (Applied Optics
# 60, 465, 2021) desliga os tres LEDs (w 36 52 00) e grava isso na flash, porque
# sem os LEDs o kit acha que estao queimados e nao liga.
echo LEDs habilitados (53h): bit0 R, bit1 G, bit2 B
w 36 53
r 36 1
echo Corrente pedida (55h): R, G, B em 2 bytes cada, LSB antes
w 36 55
r 36 6
echo Corrente maxima (5Dh)
w 36 5D
r 36 6
echo Medidas (5Eh): corrente R G B (mA x 2), tensao R G B (V x 1700), potencia R G B e total (W x 325)
w 36 5E
r 36 14
