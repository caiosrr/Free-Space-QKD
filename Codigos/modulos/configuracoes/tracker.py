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
# acorda por uma deriva lenta persistente acima de 2 px ou por um erro grande.
HOLD_ENTER_RADIUS_PX = 1.0
HOLD_EXIT_RADIUS_PX = 2.0

# A media de imagem de 2 s continua responsavel pela deteccao e pela seguranca.
# Para comandar o mount, uma mediana adicional separa vies persistente de
# oscilacoes atmosfericas aproximadamente simetricas.
SLOW_BIAS_WINDOW_SECONDS = 8.0
SLOW_BIAS_WARMUP_SECONDS = 4.0
SLOW_CORRECTION_PERSISTENCE_SECONDS = 1.5
FAST_CORRECTION_RADIUS_PX = 5.0

# AUTOTESTE TEMPORARIO: desloca a ilha depois de salvar o alvo e verifica se o
# tracker a recupera. A opcao continua desativada por padrao no prompt inicial.
PREFLIGHT_SHIFT_X_PX = 8.0
PREFLIGHT_SHIFT_Y_PX = 6.0
PREFLIGHT_MIN_CONFIRMED_ERROR_PX = 5.0
PREFLIGHT_RECOVERY_CONFIRM_FRAMES = 3
PREFLIGHT_RECOVERY_TIMEOUT_SECONDS = 60.0
PREFLIGHT_MAX_AXIS_STEP_DEG = 0.02
PREFLIGHT_MAX_RATE_DEG_S = 0.02

# Uma medicao cortada pela borda nunca comanda o mount. Para evitar que ruido
# fraco encerre uma sessao a 50 Hz, a parada definitiva exige que uma ilha
# compativel com a assinatura permaneça na borda por um intervalo real.
BORDER_CONFIRM_SECONDS = 1.0
BORDER_MIN_PEAK_RATIO = 0.25
BORDER_MIN_SIGNATURE_SIMILARITY = 0.35
# Este limite conta somente ausencia real da ilha travada. Aparencia turbulenta
# mantem o mount parado, mas possui um cronometro separado e nao encerra a sessao.
SIGNAL_LOSS_LIMIT_SECONDS = 75.0

# Estimador temporal robusto. Os frames aceitos pela trava de identidade sao
# normalizados e somados numa janela deslizante de dois segundos. O centro de
# massa da imagem media, e nao a oscilacao instantanea, alimenta o mount.
TEMPORAL_WINDOW_SECONDS = 2.0
TEMPORAL_WARMUP_SECONDS = 0.5
TEMPORAL_MIN_VALID_FRAMES = 4
TEMPORAL_RECOVERY_VALID_FRAMES = 5
TEMPORAL_RESET_AFTER_LOSS_SECONDS = 0.5
# Um frame rejeitado apenas pela aparencia pode ser ignorado por pouco tempo,
# mantendo a ultima media de 2 s. Ausencia, borda e salto espacial nao usam isso.
TEMPORAL_OPTICAL_HOLD_SECONDS = 0.45
TEMPORAL_APERTURE_RADIUS_PX = 48
TEMPORAL_INPUT_JUMP_PX = 24.0
TEMPORAL_MEAN_THRESHOLD_PERCENT = 0.20
# A media de 2 s tem atraso nominal proximo de 1 s. Reduzir os ganhos evita que
# o mount ultrapasse o alvo enquanto o erro medio ainda reflete o passado.
TEMPORAL_CONTROL_GAIN_SCALE = 0.35

# Autoexposicao conservadora. O ganho permanece fixo; a exposicao muda devagar
# usando apenas a ilha travada. O fundo da borda da ROI pode forcar uma reducao
# mesmo durante perda de sinal, evitando saturacao no amanhecer.
AUTO_EXPOSURE_ENABLED = True
AUTO_EXPOSURE_MIN_US = 1000.0
AUTO_EXPOSURE_MAX_US = 18000.0
AUTO_EXPOSURE_TARGET_LOW = 120.0
AUTO_EXPOSURE_TARGET_HIGH = 190.0
AUTO_EXPOSURE_TARGET_CENTER = 155.0
AUTO_EXPOSURE_UPDATE_SECONDS = 5.0
AUTO_EXPOSURE_HISTORY_SECONDS = 2.0
AUTO_EXPOSURE_MIN_SAMPLES = 8
AUTO_EXPOSURE_MAX_STEP_FRACTION = 0.10
AUTO_EXPOSURE_BACKGROUND_PERCENTILE = 99.0
AUTO_EXPOSURE_BACKGROUND_HIGH = 200.0
AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT = 160.0
AUTO_EXPOSURE_SATURATION_LEVEL = 250
AUTO_EXPOSURE_SATURATION_FRACTION = 0.002

# Trava de qualidade optica. Antes de entrar na media temporal, cada ilha e
# comparada com a mediana recente dos periodos estaveis. Mudancas graduais
# atualizam a referencia; saltos grandes de intensidade, area ou forma param o
# mount ate uma maioria consistente de frames voltar a ser compativel. Frames
# ruins isolados nao reiniciam toda a recuperacao durante turbulencia forte.
OPTICAL_BASELINE_WINDOW_SECONDS = 5.0
OPTICAL_INITIAL_STABLE_SECONDS = 2.0
# Uma deformacao so vira anomalia quando ocupa uma parcela relevante desta
# janela curta. Pontos ruins isolados sao descartados sem iniciar recuperacao.
OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS = 1.0
OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS = 0.30
OPTICAL_ANOMALY_ENTRY_BAD_FRACTION = 0.40
OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES = 3
OPTICAL_RECOVERY_STABLE_SECONDS = 2.0
OPTICAL_RECOVERY_WINDOW_SECONDS = 3.0
OPTICAL_RECOVERY_ACCEPTED_FRACTION = 0.80
OPTICAL_RECOVERY_MIN_SAMPLES = 8
OPTICAL_RECOVERY_POSITION_P90_PX = 8.0
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
# Imagens de eventos sao amostradas para cobrir a sessao toda sem lotar o disco.
# O frame terminal possui uma reserva separada e ignora estes dois limites.
TRACKER_EVENT_IMAGE_LIMIT = 200
TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS = 30.0


def roi_size_for_backend(backend: str) -> int:
    return IDS_ROI_SIZE_PX if str(backend).lower() == "ids" else ASI_ROI_SIZE_PX


if ASI_ROI_SIZE_PX < 200 or IDS_ROI_SIZE_PX < 128:
    raise ValueError("A ROI do tracker ficou pequena demais para operacao segura.")
if not 0 < HOLD_ENTER_RADIUS_PX < HOLD_EXIT_RADIUS_PX:
    raise ValueError("A zona de repouso precisa satisfazer 0 < entrada < saida.")
if not (
    HOLD_EXIT_RADIUS_PX < FAST_CORRECTION_RADIUS_PX
    and 0 < SLOW_BIAS_WARMUP_SECONDS <= SLOW_BIAS_WINDOW_SECONDS
    and SLOW_CORRECTION_PERSISTENCE_SECONDS > 0
):
    raise ValueError("Os tempos e raios do controle em duas escalas sao invalidos.")
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
if (
    BORDER_CONFIRM_SECONDS <= 0
    or not 0 < BORDER_MIN_PEAK_RATIO <= 1
    or not 0 < BORDER_MIN_SIGNATURE_SIMILARITY <= 1
):
    raise ValueError("A confirmacao de borda precisa de limites positivos.")
if POSITION_WATCHDOG_HZ <= 0 or CSV_LOG_HZ <= 0:
    raise ValueError("As frequencias de watchdog e CSV precisam ser positivas.")
if not 0 < TEMPORAL_WARMUP_SECONDS <= TEMPORAL_WINDOW_SECONDS:
    raise ValueError("O aquecimento temporal deve caber na janela temporal.")
if not 0 < TEMPORAL_OPTICAL_HOLD_SECONDS <= TEMPORAL_WINDOW_SECONDS:
    raise ValueError("A retencao optica deve caber na janela temporal.")
if TEMPORAL_MIN_VALID_FRAMES < 2 or TEMPORAL_RECOVERY_VALID_FRAMES < 2:
    raise ValueError("A media e a recuperacao precisam de mais de um frame.")
if not 0 < TEMPORAL_CONTROL_GAIN_SCALE <= 1:
    raise ValueError("A escala de ganho temporal deve estar em (0, 1].")
if not (
    0 < AUTO_EXPOSURE_MIN_US < AUTO_EXPOSURE_MAX_US
    and 0 < AUTO_EXPOSURE_TARGET_LOW
    < AUTO_EXPOSURE_TARGET_CENTER
    < AUTO_EXPOSURE_TARGET_HIGH
    < 255
    and AUTO_EXPOSURE_UPDATE_SECONDS >= AUTO_EXPOSURE_HISTORY_SECONDS > 0
    and AUTO_EXPOSURE_MIN_SAMPLES >= 2
    and 0 < AUTO_EXPOSURE_MAX_STEP_FRACTION < 1
    and 0 < AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT
    < AUTO_EXPOSURE_BACKGROUND_HIGH
    < 255
    and 0 < AUTO_EXPOSURE_SATURATION_LEVEL <= 255
    and 0 < AUTO_EXPOSURE_SATURATION_FRACTION < 1
):
    raise ValueError("Os limites da autoexposicao sao invalidos.")
if not 0 < OPTICAL_INITIAL_STABLE_SECONDS <= OPTICAL_BASELINE_WINDOW_SECONDS:
    raise ValueError("O aquecimento optico precisa caber na janela de referencia.")
if not (
    0 < OPTICAL_ANOMALY_ENTRY_MIN_COVERAGE_SECONDS
    <= OPTICAL_ANOMALY_ENTRY_WINDOW_SECONDS
    and 0 < OPTICAL_ANOMALY_ENTRY_BAD_FRACTION <= 1.0
    and OPTICAL_ANOMALY_ENTRY_MIN_BAD_FRAMES >= 1
):
    raise ValueError("Os limites de confirmacao da anomalia sao invalidos.")
if not (
    TEMPORAL_WINDOW_SECONDS <= OPTICAL_RECOVERY_STABLE_SECONDS
    <= OPTICAL_RECOVERY_WINDOW_SECONDS
    and 0.5 < OPTICAL_RECOVERY_ACCEPTED_FRACTION <= 1.0
    and OPTICAL_RECOVERY_MIN_SAMPLES >= 3
    and OPTICAL_RECOVERY_POSITION_P90_PX > 0
):
    raise ValueError("Os limites do consenso de recuperacao sao invalidos.")
for low, high in (
    (OPTICAL_INTENSITY_RATIO_LOW, OPTICAL_INTENSITY_RATIO_HIGH),
    (OPTICAL_AREA_RATIO_LOW, OPTICAL_AREA_RATIO_HIGH),
    (OPTICAL_LINEAR_SIZE_RATIO_LOW, OPTICAL_LINEAR_SIZE_RATIO_HIGH),
    (OPTICAL_COMPACTNESS_RATIO_LOW, OPTICAL_COMPACTNESS_RATIO_HIGH),
):
    if not 0 < low < 1 < high:
        raise ValueError("Cada faixa de qualidade optica deve envolver a razao 1.")
