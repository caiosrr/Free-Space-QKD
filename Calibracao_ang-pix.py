import numpy as np
import time

from Center_of_Mass import connect_camera, disconnect_camera, capture_frame, centro_massa
from PID_controll import ensure_connected, ensure_unparked, ensure_not_tracking
from mov_simultaneo import move_axes_pid_2d

def calibracao_2d_simultanea(exposure: float, mount: bool) -> np.ndarray:
    """
    Move o telescópio em um padrão 2D com ajuste automático de escala e MEMÓRIA.
    Se bater na borda com >= 8 pontos, salva os dados, reduz o passo e continua.
    Encerra com sucesso assim que atingir 10 pontos válidos.
    """
    passos_tentativa = [0.1, 0.05, 0.025, 0.01]
    
    print("\n>>> Iniciando Dança de Calibração 2D Simultânea (Adaptativa) <<<")
    
    frame_init = capture_frame(exposure, light=True)
    cm_init = centro_massa(frame_init)
    if cm_init is None:
        raise RuntimeError("Laser não detectado no início da calibração.")

    # Variáveis "Globais" da calibração para acumular dados entre tentativas
    registros_az_global = []
    registros_alt_global = []
    registros_x_global = []
    registros_y_global = []

    for step in passos_tentativa:
        print(f"\n" + "="*45)
        print(f"--- TENTANDO CALIBRAÇÃO COM PASSO DE {step}° ---")
        print("="*45)
        
        # O seu array completo com 19 pontos
        alvos_deg = [
            (0.0, 0.0),      # Ponto 0
            (step, 0.0),     # Ponto 1
            (step, step),    # Ponto 2
            (0.0, step),     # Ponto 3
            (-step, step),   # Ponto 4
            (-step, 0.0),    # Ponto 5
            (-step, -step),  # Ponto 6
            (0.0, -step),    # Ponto 7
            (step, -step),   # Ponto 8
            (step*2, 0.0),   # Ponto 9
            (0.0, step*2),   # Ponto 10
            (-step*2, 0.0),  # Ponto 11
            (0.0, -step*2),  # Ponto 12
            (step*3, 0.0),   # Ponto 13
            (0.0, step*3),   # Ponto 14
            (-step*3, 0.0),  # Ponto 15
            (0.0, -step*3),  # Ponto 16
            (step*3, step*3),# Ponto 17
            (0.0, 0.0)       # Ponto 18
        ]
        
        # Variáveis exclusivas deste passo
        registros_az_step = []
        registros_alt_step = []
        registros_x_step = []
        registros_y_step = []
        
        az_atual_offset = 0.0
        alt_atual_offset = 0.0
        
        bateu_na_borda = False
        total_passos = len(alvos_deg) - 1
        
        for i, (alvo_az, alvo_alt) in enumerate(alvos_deg):
            delta_az = alvo_az - az_atual_offset
            delta_alt = alvo_alt - alt_atual_offset
            
            if abs(delta_az) > 0.0001 or abs(delta_alt) > 0.0001:
                print(f"\n[Passo {i}/{total_passos}] Movendo para offset ({alvo_az:+.3f}°, {alvo_alt:+.3f}°)...")
                move_axes_pid_2d(mount, delta_az, delta_alt)
                time.sleep(0.5) 
                
                az_atual_offset = alvo_az
                alt_atual_offset = alvo_alt
            else:
                print(f"\n[Passo {i}/{total_passos}] Gravando ponto...")

            frame = capture_frame(exposure, light=True)
            cm = centro_massa(frame)
            
            if cm is None:
                print("  -> Sinal sumiu! O passo jogou o laser para fora do sensor.")
                bateu_na_borda = True
                break
                
            x_cm, y_cm, _, toca_borda = cm
            if toca_borda:
                print("  -> Laser tocou a borda! A escala bateu no limite da câmera.")
                bateu_na_borda = True
                break
                
            registros_az_step.append(az_atual_offset)
            registros_alt_step.append(alt_atual_offset)
            registros_x_step.append(x_cm)
            registros_y_step.append(y_cm)
            print(f"  -> CM Válido: X={x_cm:.1f}, Y={y_cm:.1f}")
            
            # Checagem Imediata: Se no meio do desenho batermos a meta de 10 dados TOTAIS, podemos encerrar cedo!
            if len(registros_x_global) + len(registros_x_step) >= 10:
                print(f"\n✅ Atingimos o mínimo de 10 dados acumulados durante o desenho!")
                bateu_na_borda = False # Finge que não bateu para forçar o sucesso
                break

        # =========================================================
        # LÓGICA DE DECISÃO APÓS O FIM DO DESENHO OU BATIDA NA BORDA
        # =========================================================
        total_pontos_acumulados = len(registros_x_global) + len(registros_x_step)
        
        if bateu_na_borda:
            if total_pontos_acumulados >= 10:
                print(f"⚠️ Batemos na borda, mas já temos {total_pontos_acumulados} pontos válidos guardados. É o suficiente!")
                # Aceita os dados do step atual
                registros_az_global.extend(registros_az_step)
                registros_alt_global.extend(registros_alt_step)
                registros_x_global.extend(registros_x_step)
                registros_y_global.extend(registros_y_step)
                break # Quebra o loop de steps e vai calcular a matriz
                
            elif total_pontos_acumulados >= 8:
                print(f"⚠️ Borda atingida. Temos {total_pontos_acumulados} pontos totais. Guardando dados e reduzindo o passo para completar...")
                registros_az_global.extend(registros_az_step)
                registros_alt_global.extend(registros_alt_step)
                registros_x_global.extend(registros_x_step)
                registros_y_global.extend(registros_y_step)
                
                # Devolve para o centro antes de iniciar o novo step
                if abs(az_atual_offset) > 0.0001 or abs(alt_atual_offset) > 0.0001:
                    print(f"Retornando ao centro (movendo {-az_atual_offset:+.3f}°, {-alt_atual_offset:+.3f}°)...")
                    move_axes_pid_2d(mount, -az_atual_offset, -alt_atual_offset)
                    time.sleep(1.0)
                continue # Vai para o próximo passo (menor)
                
            else:
                print(f"⚠️ Borda atingida e só temos {total_pontos_acumulados} pontos. Mínimo para guardar é 8. Descartando tentativa...")
                if abs(az_atual_offset) > 0.0001 or abs(alt_atual_offset) > 0.0001:
                    print(f"Retornando ao centro (movendo {-az_atual_offset:+.3f}°, {-alt_atual_offset:+.3f}°)...")
                    move_axes_pid_2d(mount, -az_atual_offset, -alt_atual_offset)
                    time.sleep(1.0)
                continue # Vai para o próximo passo sem guardar os dados deste step

        else:
            # Terminou a grade inteira perfeitamente ou quebrou por atingir 10 dados
            registros_az_global.extend(registros_az_step)
            registros_alt_global.extend(registros_alt_step)
            registros_x_global.extend(registros_x_step)
            registros_y_global.extend(registros_y_step)
            
            if total_pontos_acumulados >= 10:
                break # Sucesso absoluto, vai calcular a matriz
            else:
                # Caso raríssimo: fez o desenho todo e não deu 10 pontos. Volta e reduz.
                if abs(az_atual_offset) > 0.0001 or abs(alt_atual_offset) > 0.0001:
                    move_axes_pid_2d(mount, -az_atual_offset, -alt_atual_offset)
                    time.sleep(1.0)
                continue

    # =========================================================
    # CÁLCULO FINAL DA MATRIZ JACOBIANA
    # =========================================================
    num_pontos_finais = len(registros_x_global)
    if num_pontos_finais >= 10:
        print(f"\nMatemática: Ajustando plano 2D com {num_pontos_finais} pontos via Mínimos Quadrados...")
        
        M = np.column_stack((registros_az_global, registros_alt_global, np.ones(num_pontos_finais)))
        vetor_X = np.array(registros_x_global)
        vetor_Y = np.array(registros_y_global)
        
        coefs_X, _, _, _ = np.linalg.lstsq(M, vetor_X, rcond=None)
        dx_dAz, dx_dAlt, _ = coefs_X
        
        coefs_Y, _, _, _ = np.linalg.lstsq(M, vetor_Y, rcond=None)
        dy_dAz, dy_dAlt, _ = coefs_Y
        
        A = np.array([
            [dx_dAz, dx_dAlt],
            [dy_dAz, dy_dAlt]
        ])
        
        print("\nResultados da Derivação Parcial (Matriz Jacobiana):")
        print(f"  dx/dAz  = {dx_dAz:+.2f} px/°  |  dx/dAlt = {dx_dAlt:+.2f} px/°")
        print(f"  dy/dAz  = {dy_dAz:+.2f} px/°  |  dy/dAlt = {dy_dAlt:+.2f} px/°")
        
        # Garante que o telescópio voltou para o centro antes de devolver a matriz
        if abs(az_atual_offset) > 0.0001 or abs(alt_atual_offset) > 0.0001:
            print(f"\nFinalizando: Retornando ao centro inicial (movendo {-az_atual_offset:+.3f}°, {-alt_atual_offset:+.3f}°)...")
            move_axes_pid_2d(mount, -az_atual_offset, -alt_atual_offset)
            time.sleep(1.0)
            
        return A
        
    else:
        # Se tentou todas as escalas (até 0.01) e o total_pontos não chegou a 10
        raise RuntimeError(f"Falha total: O laser ficou saindo da tela. Total de pontos válidos: {num_pontos_finais} (Exigido: 10).")