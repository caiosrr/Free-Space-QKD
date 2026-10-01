# So leitura: Look em uso e o cabecalho da sequencia ativa (26h): quanto do
# quadro cada cor ocupa (vermelho, verde, azul, em centesimos de %, LSB antes).
echo Look em uso (23h)
w 36 23
r 36 6
echo Cabecalho da sequencia (26h): R, G, B do Look; quadro max e min; idem da sequencia
w 36 26
r 36 1E
