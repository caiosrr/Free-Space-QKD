# So leitura: o estado do controlador, para registrar antes e depois dos testes.
echo Fonte de entrada (06h): 0=HDMI 1=gerador 2=splash
w 36 06
r 36 1
echo Formato da entrada externa (08h): 43=RGB888 51=YCbCr888
w 36 08
r 36 1
echo Look, sequencia e quadro (23h)
w 36 23
r 36 6
echo Degamma/CMT (28h)
w 36 28
r 36 1
echo CCA select (2Ah) e CCA ligado (87h)
w 36 2A
r 36 1
w 36 87
r 36 1
echo Controle dos LEDs (51h): 0=manual 1=CAIC
w 36 51
r 36 1
echo LABB (81h)
w 36 81
r 36 3
echo CAIC (85h)
w 36 85
r 36 3
echo Tamanho da entrada externa (2Fh)
w 36 2F
r 36 4
echo Temperatura (D6h)
w 36 D6
r 36 2
