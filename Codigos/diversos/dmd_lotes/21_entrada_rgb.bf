# Volta a entrada HDMI para RGB888 (43h), o original, e reaplica a fonte HDMI.
echo Entrada em RGB888
w 36 07 43
delay 64
w 36 05 00
delay 1F4
w 36 08
r 36 1
