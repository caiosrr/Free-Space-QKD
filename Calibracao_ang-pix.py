import numpy as np
import time
import cv2

from Center_of_Mass import connect_camera, disconnect_camera, capture_frame, centro_massa
from PID_controll import move_axis_pid, ensure_connected, ensure_unparked, ensure_not_tracking

def calibrar_eixo(eixo_id: int, nome_eixo: str, exposure: float) -> tuple:
    """
    Move um eixo específico passo a passo para calcular a relação Pixels/Grau.
    Retorna a inclinação em X e Y (dx/dGrau, dy/dGrau).
    """
    step_deg = 0.1
    min_steps = 5
    max_steps = 10
    
    print(f"\n>>> Iniciando calibração do Eixo {eixo_id} ({nome_eixo}) <<<")
    
    # Captura o ponto zero
    frame = capture_frame(exposure, light=True)
    cm = centro_massa(frame)
    
    if cm is None:
        raise RuntimeError("Sinal perdido logo no início da calibração.")
        
    x_cm, y_cm, _, toca_borda = cm
    if toca_borda:
        print("AVISO: O laser já começou na borda. Centralize melhor antes de calibrar.")
    
    # Listas para guardar o histórico de movimentos válidos
    historico_deg = [0.0]
    historico_x = [x_cm]
    historico_y = [y_cm]
    
    deg_acumulado = 0.0
    passos_validos = 0
    reversoes = 0
    
    while passos_validos < max_steps:
        print(f"[{nome_eixo}] Movendo {step_deg:+.2f}°...")
        move_axis_pid(True, eixo_id, step_deg)
        time.sleep(0.5) # Pausa rápida para a montagem estabilizar e evitar arrasto na imagem
        
        deg_acumulado += step_deg
        
        frame = capture_frame(exposure, light=True)
        cm = centro_massa(frame)
        
        bateu_na_borda = False
        if cm is None:
            print("Sinal sumiu da câmera!")
            bateu_na_borda = True
        else:
            x_cm, y_cm, _, toca_borda = cm
            if toca_borda:
                print("Laser tocou a borda do sensor!")
                bateu_na_borda = True
                
        if bateu_na_borda:
            # Reverte a direção e joga fora esse último movimento que bagunçou os cálculos
            print("Descartando movimento inválido e revertendo direção...")
            step_deg = -step_deg 
            reversoes += 1
            
            # Desfaz o movimento físico para voltar para a zona segura
            move_axis_pid(True, eixo_id, step_deg)
            deg_acumulado += step_deg
            time.sleep(0.5)
            
            if reversoes >= 3:
                print("Máximo de 3 reversões atingido! Encerrando varredura neste eixo.")
                break
            
            # Se já temos pontos suficientes, encerra mais cedo. Se não, continua andando pro outro lado.
            if passos_validos >= min_steps:
                print(f"Já temos {passos_validos} pontos válidos. Encerrando eixo.")
                break
            else:
                print("Ainda não temos pontos suficientes, varrendo na direção oposta...")
                continue
                
        # Se o movimento foi limpo, registra na lista
        historico_deg.append(deg_acumulado)
        historico_x.append(x_cm)
        historico_y.append(y_cm)
        passos_validos += 1
        print(f"  -> CM válido registrado: X={x_cm:.1f}, Y={y_cm:.1f}")

    if passos_validos < min_steps:
        raise RuntimeError(f"Calibração falhou: Só conseguimos {passos_validos} passos válidos antes de perder o laser nos dois sentidos.")

    print(f"\nRetornando o Eixo {eixo_id} para o centro (movendo {-deg_acumulado:+.2f}°)...")
    move_axis_pid(True, eixo_id, -deg_acumulado)
    time.sleep(1.0) # Tempo extra para estabilizar o retorno ao centro

    # Ajuste de curva (Regressão Linear) de 1º grau para achar os coeficientes angulares
    # polyfit retorna [inclinacao, interseccao]
    coef_x = np.polyfit(historico_deg, historico_x, 1)
    coef_y = np.polyfit(historico_deg, historico_y, 1)
    
    dx_dgrau = coef_x[0]
    dy_dgrau = coef_y[0]
    
    print(f"\nResultado {nome_eixo}:")
    print(f"  dx/dGrau = {dx_dgrau:.2f} px/°")
    print(f"  dy/dGrau = {dy_dgrau:.2f} px/°")
    
    return dx_dgrau, dy_dgrau


def main():
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()
    connect_camera()
    
    exposure_seconds = 32e-6
    
    try:
        print("=== ROTINA DE CALIBRAÇÃO PIXEL -> GRAUS ===")
        print("Certifique-se de que o laser está visível e aproximadamente no centro.")
        input("Pressione ENTER para começar...")
        
        # 1. Calibra o Azimute (Eixo 0)
        dx_dAz, dy_dAz = calibrar_eixo(0, "Azimute", exposure_seconds)
        
        # 2. Calibra a Altitude (Eixo 1)
        # Importante: Como já tiramos o laser do centro calibrando o Az, 
        # ele vai calibrar a Altitude a partir de onde parou.
        dx_dAlt, dy_dAlt = calibrar_eixo(1, "Altitude", exposure_seconds)
        
        # Monta a matriz Jacobiana A (Graus para Pixels)
        # | dx/dAz   dx/dAlt |
        # | dy/dAz   dy/dAlt |
        A = np.array([
            [dx_dAz, dx_dAlt],
            [dy_dAz, dy_dAlt]
        ])
        
        print("\n=== MATRIZ A (Graus -> Pixels) ===")
        print(A)
        
        # Calcula a inversa A^-1 (Pixels para Graus)
        A_inv = np.linalg.inv(A)
        
        print("\n=== MATRIZ A_inv (Pixels -> Graus) ===")
        print(A_inv)
        
        # Salva o arquivo para o seu PID_controll usar
        np.save("calibracao_A_inv.npy", A_inv)
        print("\nCalibração concluída! Arquivo 'calibracao_A_inv.npy' salvo com sucesso.")
        
    except Exception as e:
        print(f"\nERRO DURANTE A CALIBRAÇÃO: {e}")
    finally:
        disconnect_camera()

if __name__ == "__main__":
    main()