# Especulativo: liga o CAIC (controle automatico dos LEDs), que tambem aplica um
# ganho digital a imagem. Pode ou nao empurrar o verde e o azul de volta a 255.
# O 31_caic_desligado.bf volta ao original (corrente manual).
echo CAIC ligado
w 36 50 01
delay 1F4
w 36 51
r 36 1
