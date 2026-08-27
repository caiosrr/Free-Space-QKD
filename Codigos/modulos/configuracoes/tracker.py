"""Configuracao unica da calibracao local e do tracker continuo.

Os valores deste arquivo sao deliberadamente conservadores para sessoes longas.
Camera, ganho e exposicao ficam em ``configuracoes/camera_asi.py`` ou
``configuracoes/camera_ids.py``. Os drivers ficam em ``controle/cameras``.
"""

# ROI fixa ao redor da luz escolhida. Uma ROI maior facilita reencontrar um spot
# largo sem processar o sensor inteiro. A IDS mantem seu tamanho otimizado.
ASI_ROI_SIZE_PX = 384
IDS_ROI_SIZE_PX = 256

# Uma ilha nao pode saltar mais que isso entre dois frames do tracker. Como a
# ancora acompanha a luz gradualmente, este limite nao reduz a area util da ROI;
# ele apenas impede trocar de repente para uma parede ou outra luz distante.
TRACKER_MAX_SPOT_JUMP_PX = 45.0

# A calibracao manual mede escalas menores e maiores para conferir a linearidade.
FINE_CALIBRATION_RADII_DEG = (0.004, 0.008, 0.016)

# Zona de repouso com histerese. A malha busca erro menor ou igual a 1 px e so
# acorda novamente apos tres medias consecutivas acima de 2 px.
HOLD_ENTER_RADIUS_PX = 1.0
HOLD_EXIT_RADIUS_PX = 2.0
HOLD_EXIT_CONFIRM_FRAMES = 3

# AUTOTESTE TEMPORARIO: desloca a ilha depois de salvar o alvo e verifica se o
# tracker a recupera. A opcao continua desativada por padrao no prompt inicial.
PREFLIGHT_SHIFT_X_PX = 8.0
PREFLIGHT_SHIFT_Y_PX = 6.0
PREFLIGHT_MIN_CONFIRMED_ERROR_PX = 5.0
PREFLIGHT_RECOVERY_CONFIRM_FRAMES = 3
PREFLIGHT_RECOVERY_TIMEOUT_SECONDS = 60.0
PREFLIGHT_MAX_AXIS_STEP_DEG = 0.02
PREFLIGHT_MAX_RATE_DEG_S = 0.02

# Uma medicao cortada pela borda nao pode comandar o mount. Tres ocorrencias
# seguidas encerram a sessao; na perda completa, o mount para e aguarda a mesma
# luz reaparecer. O encerramento so ocorre apos 75 s sem recuperacao confirmada.
BORDER_CONFIRM_FRAMES = 3
SIGNAL_LOSS_LIMIT_SECONDS = 75.0

# Estimador temporal robusto. Os frames aceitos pela trava de identidade sao
# normalizados e somados numa janela deslizante de dois segundos. O centro de
# massa da imagem media, e nao a oscilacao instantanea, alimenta o mount.
TEMPORAL_WINDOW_SECONDS = 2.0
TEMPORAL_WARMUP_SECONDS = 0.5
TEMPORAL_MIN_VALID_FRAMES = 4
TEMPORAL_RECOVERY_VALID_FRAMES = 5
TEMPORAL_RESET_AFTER_LOSS_SECONDS = 0.5
TEMPORAL_APERTURE_RADIUS_PX = 48
TEMPORAL_INPUT_JUMP_PX = 24.0
TEMPORAL_MEAN_THRESHOLD_PERCENT = 0.20
# A media de 2 s tem atraso nominal proximo de 1 s. Reduzir os ganhos evita que
# o mount ultrapasse o alvo enquanto o erro medio ainda reflete o passado.
TEMPORAL_CONTROL_GAIN_SCALE = 0.35

# Trava de qualidade optica. Antes de entrar na media temporal, cada ilha e
# comparada com a mediana recente dos periodos estaveis. Mudancas graduais
# atualizam a referencia; saltos grandes de intensidade, area ou forma param o
# mount ate a aparencia normal permanecer estavel por alguns segundos.
OPTICAL_BASELINE_WINDOW_SECONDS = 5.0
OPTICAL_INITIAL_STABLE_SECONDS = 2.0
OPTICAL_RECOVERY_STABLE_SECONDS = 3.0
OPTICAL_MIN_BASELINE_FRAMES = 5
OPTICAL_INTENSITY_RATIO_LOW = 0.35
OPTICAL_INTENSITY_RATIO_HIGH = 2.80
OPTICAL_AREA_RATIO_LOW = 0.45
OPTICAL_AREA_RATIO_HIGH = 2.20
OPTICAL_LINEAR_SIZE_RATIO_LOW = 0.55
OPTICAL_LINEAR_SIZE_RATIO_HIGH = 1.80
OPTICAL_COMPACTNESS_RATIO_LOW = 0.45
OPTICAL_COMPACTNESS_RATIO_HIGH = 2.20
OPTICAL_MIN_SIGNATURE_SIMILARITY = 0.25

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
# Um PNG pequeno no inicio e na recuperacao das oclusoes, com teto por sessao.
TRACKER_EVENT_IMAGE_LIMIT = 100


def roi_size_for_backend(backend: str) -> int:
    return IDS_ROI_SIZE_PX if str(backend).lower() == "ids" else ASI_ROI_SIZE_PX


if ASI_ROI_SIZE_PX < 200 or IDS_ROI_SIZE_PX < 128:
    raise ValueError("A ROI do tracker ficou pequena demais para operacao segura.")
if not 0 < HOLD_ENTER_RADIUS_PX < HOLD_EXIT_RADIUS_PX:
    raise ValueError("A zona de repouso precisa satisfazer 0 < entrada < saida.")
if (
    PREFLIGHT_MIN_CONFIRMED_ERROR_PX <= HOLD_EXIT_RADIUS_PX
    or (PREFLIGHT_SHIFT_X_PX**2 + PREFLIGHT_SHIFT_Y_PX**2) ** 0.5
    <= PREFLIGHT_MIN_CONFIRMED_ERROR_PX
):
    raise ValueError("O autoteste precisa sair claramente da zona de repouso.")
if (
    PREFLIGHT_RECOVERY_CONFIRM_FRAMES < 1
    or PREFLIGHT_RECOVERY_TIMEOUT_SECONDS <= 0
    or PREFLIGHT_MAX_AXIS_STEP_DEG <= 0
    or PREFLIGHT_MAX_RATE_DEG_S <= 0
):
    raise ValueError("Os limites do autoteste precisam ser positivos.")
if MAX_SESSION_HOURS <= 0:
    raise ValueError("MAX_SESSION_HOURS precisa ser positivo.")
if MAX_OFFSET_AZ_DEG <= 0 or MAX_OFFSET_ALT_DEG <= 0:
    raise ValueError("Os limites absolutos dos eixos precisam ser positivos.")
if POSITION_WATCHDOG_HZ <= 0 or CSV_LOG_HZ <= 0:
    raise ValueError("As frequencias de watchdog e CSV precisam ser positivas.")
if not 0 < TEMPORAL_WARMUP_SECONDS <= TEMPORAL_WINDOW_SECONDS:
    raise ValueError("O aquecimento temporal deve caber na janela temporal.")
if TEMPORAL_MIN_VALID_FRAMES < 2 or TEMPORAL_RECOVERY_VALID_FRAMES < 2:
    raise ValueError("A media e a recuperacao precisam de mais de um frame.")
if not 0 < TEMPORAL_CONTROL_GAIN_SCALE <= 1:
    raise ValueError("A escala de ganho temporal deve estar em (0, 1].")
if not 0 < OPTICAL_INITIAL_STABLE_SECONDS <= OPTICAL_BASELINE_WINDOW_SECONDS:
    raise ValueError("O aquecimento optico precisa caber na janela de referencia.")
if OPTICAL_RECOVERY_STABLE_SECONDS < TEMPORAL_WINDOW_SECONDS:
    raise ValueError("A recuperacao optica nao pode ser menor que a media temporal.")
for low, high in (
    (OPTICAL_INTENSITY_RATIO_LOW, OPTICAL_INTENSITY_RATIO_HIGH),
    (OPTICAL_AREA_RATIO_LOW, OPTICAL_AREA_RATIO_HIGH),
    (OPTICAL_LINEAR_SIZE_RATIO_LOW, OPTICAL_LINEAR_SIZE_RATIO_HIGH),
    (OPTICAL_COMPACTNESS_RATIO_LOW, OPTICAL_COMPACTNESS_RATIO_HIGH),
):
    if not 0 < low < 1 < high:
        raise ValueError("Cada faixa de qualidade optica deve envolver a razao 1.")
