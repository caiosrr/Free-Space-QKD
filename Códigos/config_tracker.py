"""Configuracao unica da calibracao local e do tracker continuo.

Os valores deste arquivo sao deliberadamente conservadores para sessoes longas.
Camera, ganho e exposicao continuam em ``config_camera_asi.py`` ou, para a IDS,
em ``Link UFF/config_camera_ids.py``.
"""

# ROI fixa ao redor da luz escolhida. Uma ROI maior facilita reencontrar um spot
# largo sem processar o sensor inteiro. A IDS mantem seu tamanho otimizado.
ASI_ROI_SIZE_PX = 384
IDS_ROI_SIZE_PX = 192

# A calibracao manual mede escalas menores e maiores para conferir a linearidade.
FINE_CALIBRATION_RADII_DEG = (0.004, 0.008, 0.016)

# Zona de repouso com histerese. Dentro de 4 px o mount para; ele so volta a
# corrigir depois de tres medidas consecutivas acima de 6 px.
HOLD_ENTER_RADIUS_PX = 4.0
HOLD_EXIT_RADIUS_PX = 6.0
HOLD_EXIT_CONFIRM_FRAMES = 3

# Uma medicao cortada pela borda nao pode comandar o mount. Tres ocorrencias
# seguidas encerram a sessao; perda completa do sinal tem uma espera maior.
BORDER_CONFIRM_FRAMES = 3
SIGNAL_LOSS_LIMIT_SECONDS = 10.0

# Limites da sessao longa.
MAX_SESSION_HOURS = 2.0
MAX_OFFSET_AZ_DEG = 5.0
MAX_OFFSET_ALT_DEG = 5.0
POSITION_WATCHDOG_HZ = 5.0
WATCHDOG_READ_FAILURES = 5

# Velocidade maxima durante o tracking. O limite menor reduz a distancia que o
# mount pode percorrer entre duas verificacoes do watchdog.
MAX_TRACKING_RATE_DEG_S = 0.10

# Ao atingir tempo/deslocamento, volta devagar para a posicao inicial e encerra.
RETURN_TO_START_ON_LIMIT = True
RETURN_MAX_RATE_DEG_S = 0.20
RETURN_TOLERANCE_DEG = 0.0005
RETURN_ATTEMPTS = 2

# Telemetria. Cinco linhas por segundo geram um historico detalhado sem produzir
# um arquivo excessivo durante varias horas.
CSV_LOG_HZ = 5.0
VARIANCE_WINDOW_SECONDS = 2.0
CSV_FLUSH_SECONDS = 1.0


def roi_size_for_backend(backend: str) -> int:
    return IDS_ROI_SIZE_PX if str(backend).lower() == "ids" else ASI_ROI_SIZE_PX


if ASI_ROI_SIZE_PX < 200 or IDS_ROI_SIZE_PX < 128:
    raise ValueError("A ROI do tracker ficou pequena demais para operacao segura.")
if not 0 < HOLD_ENTER_RADIUS_PX < HOLD_EXIT_RADIUS_PX:
    raise ValueError("A zona de repouso precisa satisfazer 0 < entrada < saida.")
if MAX_SESSION_HOURS <= 0:
    raise ValueError("MAX_SESSION_HOURS precisa ser positivo.")
if MAX_OFFSET_AZ_DEG <= 0 or MAX_OFFSET_ALT_DEG <= 0:
    raise ValueError("Os limites absolutos dos eixos precisam ser positivos.")
if POSITION_WATCHDOG_HZ <= 0 or CSV_LOG_HZ <= 0:
    raise ValueError("As frequencias de watchdog e CSV precisam ser positivas.")
