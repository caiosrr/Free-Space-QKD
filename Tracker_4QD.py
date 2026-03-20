import itertools
import time
import requests
import numpy as np
import cv2

# Mantém as importações originais de conexão e segurança
from PID_controll import (
    ensure_connected,
    ensure_unparked,
    ensure_not_tracking,
)

# IMPORTAÇÃO CHAVE: Puxando o seu movimento diagonal simultâneo
from mov_simultaneo import move_axes_pid_2d

# ==== Configurações Alpaca ====
BASE_URL = "http://127.0.0.1:11111/api/v1/camera/0"
CLIENT_ID = 1
_transaction_ids = itertools.count(1)

# ==== Parâmetros do Tracker ====
TOLERANCIA_PX = 2.0  # tolerância padrão em pixels para encerrar a correção
WINDOW_SIZE = 200     # CONFIRMADO: Janela de rastreamento de 200x200 pixels

def call(method: str, command: str, timeout: float = 5.0, **extra_args):
    """Executa uma chamada à API Alpaca."""
    params = {
        "ClientID": CLIENT_ID,
        "ClientTransactionID": next(_transaction_ids),
    }
    params.update(extra_args.pop("params", {}))
    resp = requests.request(
        method,
        f"{BASE_URL}/{command}",
        params=params,
        timeout=timeout,
        **extra_args,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("ErrorNumber", 0):
        raise RuntimeError(f"{command}: {payload.get('ErrorMessage')}")
    return payload.get("Value")


def connect_camera() -> None:
    print("Conectando à câmera...")
    call("PUT", "connected", data={"Connected": True})

def disconnect_camera() -> None:
    print("Desconectando da câmera...")
    call("PUT", "connected", data={"Connected": False})

def start_exposure(duration_seconds: float, light: bool = True) -> None:
    call("PUT", "startexposure", data={"Duration": duration_seconds, "Light": light})

def wait_until_image_ready(poll_interval: float = 0.05, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = bool(call("GET", "imageready"))
        if ready:
            return
        time.sleep(poll_interval)
    raise TimeoutError("Tempo limite esperando ImageReady = True")


def fetch_image_array() -> np.ndarray:
    payload = call("GET", "imagearray")
    return np.asarray(payload)


def capture_frame(exposure_seconds: float) -> np.ndarray:
    """Captura e normaliza o frame básico."""
    start_exposure(exposure_seconds, light=True)
    wait_until_image_ready()
    frame = fetch_image_array()
    frame = frame.astype(np.float32)
    
    # Normalização robusta baseada no desvio padrão para remover o pedestal de ruído
    pedestal = np.median(frame) + 0.5 * np.std(frame)
    max_val = frame.max()
    
    # Se a imagem for toda preta (sem sinal), retorna array zerado
    if max_val <= pedestal:
        return np.zeros_like(frame, dtype=np.uint8)
        
    norm = np.clip((frame - pedestal) / (max_val - pedestal + 1e-6), 0, 1)
    norm = (norm * 255).astype(np.uint8)
    
    # Corrige orientação: frame original está rotacionado 180°
    norm = np.rot90(norm, 2)
    return norm


def calcular_cm_corrigido(frame_window: np.ndarray, threshold_percent: float = 0.5):
    """
    CORREÇÃO: Calcula o CM usando um limiar dinâmico baseado no pico de luz.
    Isso mata o rastro/borrão e crava no núcleo do laser.
    """
    # Se for RGB, converte para cinza
    if frame_window.ndim == 3:
        frame_gray = frame_window.mean(axis=2)
    else:
        frame_gray = frame_window.copy() # Cópia para não alterar a imagem original
        
    # Acha o pixel mais brilhante da janela
    max_val = frame_gray.max()
    
    # Se a janela estiver preta, não tem CM
    if max_val == 0:
        return None
        
    # === A MÁGICA: Limiar Dinâmico ===
    # Só considera pixels que tenham >= 50% da intensidade do pico
    dynamic_threshold = max_val * threshold_percent
    frame_gray[frame_gray < dynamic_threshold] = 0

    total_intensidade = frame_gray.sum()
    
    # Se depois de aplicar o limiar a intensidade sumir, não tem laser real
    if total_intensidade == 0:
        return None

    h, w = frame_gray.shape
    y = np.arange(h)
    x = np.arange(w)
    X, Y = np.meshgrid(x, y)

    # Cálculo do Centro de Massa ponderado pela intensidade
    x_cm = (X * frame_gray).sum() / total_intensidade
    y_cm = (Y * frame_gray).sum() / total_intensidade
    
    return x_cm, y_cm


def main() -> None:
    # 1. Garante segurança do telescópio
    ensure_connected()
    ensure_unparked()
    ensure_not_tracking()

    # 2. Conecta Câmera
    connect_camera()

    # 3. Carrega a Matriz de Calibração (Pixels -> Graus)
    try:
        A_inv = np.load("calibracao_A_inv.npy")
        if A_inv.shape != (2, 2):
            raise ValueError("Matriz A_inv com shape inválido.")
    except Exception as e:
        print(f"\nERRO: Não foi possível carregar a calibração: {e}")
        disconnect_camera()
        return

    try:
        # Exposição curtíssima para congelar a turbulência
        exposure_seconds = 32e-6
        usar_mount = True

        # Posição central fixa da câmera (referencial do tracker)
        frame_base = capture_frame(exposure_seconds)
        h, w = frame_base.shape[:2]
        cx, cy = (w - 1) / 2, (h - 1) / 2
        
        # Define a Janela de Rastreamento (200x200) centralizada no sensor
        y1, y2 = int(cy - WINDOW_SIZE/2), int(cy + WINDOW_SIZE/2)
        x1, x2 = int(cx - WINDOW_SIZE/2), int(cx + WINDOW_SIZE/2)
        
        print("\n=== INICIANDO TRACKER QUÂNTICO SIMULTÂNEO ===")
        print(f"Janela de Rastreamento ativada: {WINDOW_SIZE}x{WINDOW_SIZE} pixels")
        print("Pressione 'q' na janela de vídeo para encerrar de forma limpa.\n")

        # --- PREPARA TELA CHEIA ---
        win_name = "Tracker 2D Simultaneo - IC Quantica"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        iteracao = 0
        while True:
            # Captura o frame completo
            frame_full = capture_frame(exposure_seconds)
            
            # Recorta a Janela de interesse (ROI)
            frame_window = frame_full[y1:y2, x1:x2]
            
            # Calcula o CM CORRIGIDO dentro da janela
            cm = calcular_cm_corrigido(frame_window)
            
            if cm is None:
                print("⚠️ Sinal perdido na janela de rastreamento! Interrompendo malha de controle.", end="\r")
                move_axes_pid_2d(usar_mount, 0.0, 0.0) # Zera motores
                time.sleep(0.1)
                continue
                
            # Coordenadas do CM locais (na janela)
            x_cm_local, y_cm_local = cm
            
            # Converte para coordenadas globais (no sensor)
            x_cm_global = x_cm_local + x1
            y_cm_global = y_cm_local + y1
            
            # Calcula os erros dx/dy reais (cm - centro)
            dx = x_cm_global - cx
            dy = y_cm_global - cy
            
            # Atualiza a interface visual antes de bloquear o código com os movimentos do motor
            # --- Interface Visual: ZOOM NO NÚCLEO (Janela de Rastreamento) ---
            
            # Para evitar que o Tracker fique pesado, vamos recortar apenas uma Borda de Exibição ao redor da janela verde
            # Margem para que o retângulo verde ocupe aprox. 80% da tela visível
            margem_zoom = int(WINDOW_SIZE * 0.125) 
            zoom_y1 = max(0, y1 - margem_zoom)
            zoom_y2 = min(h, y2 + margem_zoom)
            zoom_x1 = max(0, x1 - margem_zoom)
            zoom_x2 = min(w, x2 + margem_zoom)

            # Recorta exatamente o quadrado do zoom no frame original
            frame_zoom_gray = frame_full[zoom_y1:zoom_y2, zoom_x1:zoom_x2]
            
            # Converte cor SÓ no recorte pequeno (muito leve)
            frame_display = cv2.cvtColor(frame_zoom_gray, cv2.COLOR_GRAY2BGR)
            
            # Coordenadas relativas ao novo recorte para desenhar por cima
            # O eixo 0,0 agora é o canto superior esquerdo do nosso corte de zoom.
            cx_z = int(cx - zoom_x1)
            cy_z = int(cy - zoom_y1)
            x1_z = int(x1 - zoom_x1)
            y1_z = int(y1 - zoom_y1)
            x2_z = int(x2 - zoom_x1)
            y2_z = int(y2 - zoom_y1)
            x_cm_z = int(x_cm_global - zoom_x1)
            y_cm_z = int(y_cm_global - zoom_y1)
            
            # --- Ajusta para o tamanho Fullscreen/Monitor Grande ---
            # Dá um upscale nesse quadradinho usando formato de pixel puro (como jogos 8 bits)
            # Dessa forma não fica borrado e preenche a tela
            zoom_h, zoom_w = frame_display.shape[:2]
            target_h = 1080  # Altura padrão resolucao pra esticar
            scale_upscale = target_h / zoom_h
            
            frame_display_large = cv2.resize(frame_display, (int(zoom_w * scale_upscale), int(zoom_h * scale_upscale)), interpolation=cv2.INTER_NEAREST)

            # --- Aplica os desenhos nas coordenadas já multiplicadas ---
            cx_L = int(cx_z * scale_upscale)
            cy_L = int(cy_z * scale_upscale)
            x1_L = int(x1_z * scale_upscale)
            y1_L = int(y1_z * scale_upscale)
            x2_L = int(x2_z * scale_upscale)
            y2_L = int(y2_z * scale_upscale)
            x_cm_L = int(x_cm_z * scale_upscale)
            y_cm_L = int(y_cm_z * scale_upscale)
            
            # Desenha a Janela Verde
            cv2.rectangle(frame_display_large, (x1_L, y1_L), (x2_L, y2_L), (0, 255, 0), 2)
            
            # Desenha Cruz no Centro do Tracker (Azul e Vermelha)
            cv2.line(frame_display_large, (cx_L, 0), (cx_L, int(zoom_h * scale_upscale)), (255, 0, 0), 2) # Azul (Vertical)
            cv2.line(frame_display_large, (0, cy_L), (int(zoom_w * scale_upscale), cy_L), (0, 0, 255), 2) # Vermelha (Horizontal)
            
            # Etiquetas dos Eixos (Letras X e Y)
            W = int(zoom_w * scale_upscale)
            H = int(zoom_h * scale_upscale)
            cv2.putText(frame_display_large, "X", (W - 60, cy_L - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
            cv2.putText(frame_display_large, "Y", (cx_L + 20, H - 40), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)
            
            # Desenha o Ponto do Laser (Centro de Massa)
            cv2.circle(frame_display_large, (x_cm_L, y_cm_L), 8, (0, 255, 255), -1)

            # Mostra dados na tela
            info_text = f"dx={dx:+.1f} px, dy={dy:+.1f} px"
            cv2.putText(frame_display_large, info_text, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
            
            # Atualiza na janela Fullscreen
            cv2.imshow(win_name, frame_display_large)
            
            # Escuta a tecla 'q' para sair
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            # Se já estiver no centro, não precisa mover
            if abs(dx) <= TOLERANCIA_PX and abs(dy) <= TOLERANCIA_PX:
                move_axes_pid_2d(usar_mount, 0.0, 0.0)
            else:
                # --- O CÁLCULO DA CORREÇÃO ---
                # vetor_px = [-dx, -dy] -> O deslocamento que queremos fazer o CM percorrer
                vec_px = np.array([-dx, -dy])
                # Multiplica pela matriz inversa para converter Pixels para Graus
                dAz_deg, dAlt_deg = A_inv @ vec_px
                
                # DISPARO DIAGONAL SIMULTÂNEO (threads no mov_simultaneo.py)
                move_axes_pid_2d(usar_mount, dAz_deg, dAlt_deg)

            iteracao += 1

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário (Ctrl+C). Encerrando...")
    except Exception as e:
        print(f"\nErro inesperado no Tracker: {e}")
    finally:
        # Segurança: Para tudo e fecha a câmera
        move_axes_pid_2d(usar_mount, 0.0, 0.0)
        disconnect_camera()
        cv2.destroyAllWindows()
        print("Controle encerrado de forma limpa.")


if __name__ == "__main__":
    main()