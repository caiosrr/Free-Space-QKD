# Diz ao controlador que a entrada HDMI chega em YCbCr 4:4:4 (51h), com a matriz
# padrao BT.601 (09h, conjunto 0), e reaplica a fonte HDMI. So faz sentido com a
# placa de video mandando YCbCr 4:4:4. O 21_entrada_rgb.bf volta ao original.
echo Entrada em YCbCr 4:4:4
w 36 07 51
w 36 09 00 00
delay 64
w 36 05 00
delay 1F4
w 36 08
r 36 1
