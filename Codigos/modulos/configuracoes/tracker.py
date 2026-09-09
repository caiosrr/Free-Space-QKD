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
# Experimento pareado da zona de repouso. A sombra NAO consegue decidir se vale
# apertar HOLD_ENTER_RADIUS_PX: ela mede o erro que sobra, mas o efeito de uma
# correcao que nao aconteceu nao e observavel. So um teste real responde, e ele
# precisa ser pareado, porque a turbulencia muda ao longo da noite e comparar
# duas sessoes diferentes compara o ceu, nao o parametro.
#
# Ligado, o raio de entrada alterna entre HOLD_ENTER_RADIUS_PX e o alternativo
# a cada bloco, dentro da MESMA sessao. A analise depois separa a telemetria
# pela coluna zona_parada_raio_px e compara a distribuicao de distancia_px nos
# dois regimes. Custo estimado com os numeros de 2026-09-09: o erro de controle
# passa de 0,6 px em 69% do tempo contra 43% acima de 1,0 px, entao o regime
# apertado deve fazer ~1,6x mais correcoes e levar o tempo morto de ~5% para
# ~8% da sessao. Desligado por padrao: mexe em controle de verdade.
HOLD_RADIUS_AB_TEST_ENABLED = False
HOLD_RADIUS_AB_ALTERNATE_PX = 0.6
HOLD_RADIUS_AB_BLOCK_SECONDS = 600.0
HOLD_EXIT_RADIUS_PX = 2.0

# A media de imagem de 2 s continua responsavel pela deteccao e pela seguranca.
# Para comandar o mount, uma mediana adicional separa vies persistente de
# oscilacoes atmosfericas aproximadamente simetricas.
SLOW_BIAS_WINDOW_SECONDS = 8.0
SLOW_BIAS_WARMUP_SECONDS = 4.0
SLOW_CORRECTION_PERSISTENCE_SECONDS = 1.5
FAST_CORRECTION_RADIUS_PX = 5.0
# Um erro acima do raio rapido nao comanda mais o mount por uma unica media.
# Ele precisa persistir e manter direcao coerente nesta janela curta.
FAST_ERROR_WINDOW_SECONDS = 3.0
FAST_ERROR_CONFIRM_SECONDS = 2.0
FAST_ERROR_MIN_LARGE_FRACTION = 0.70
FAST_ERROR_MIN_DIRECTION_COHERENCE = 0.80
FAST_ERROR_MIN_SAMPLES = 8

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
#
# O enlace UFF-CBPF atravessa a baia de Guanabara e embarcacoes cortam o feixe
# com frequencia, tipicamente por 1 a 2 minutos. Um limite curto transforma um
# navio passando em fim de sessao. Esperar nao tem custo nenhum: sem sinal
# valido o mount ja esta parado, a assinatura da ilha travada e a posicao alvo
# continuam guardadas e a exposicao fica congelada, entao o tracker volta a
# medir com exatamente as mesmas caracteristicas de antes. O unico custo e
# tempo de sessao. 10 min dao 5x de margem sobre a ocultacao tipica e ainda
# cabem no orcamento de uma perda por falta de exposicao (150 s de espera,
# ~35 s de rampa e ~2 s para reconstruir a media temporal).
SIGNAL_LOSS_LIMIT_SECONDS = 600.0

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

# Autoexposicao orientada a contraste. O ganho permanece fixo e a camera usa a
# menor exposicao que ainda fornece CNR confortavel para a ilha travada. Isso
# preserva FPS e deixa a integracao para a media temporal de varios frames.
AUTO_EXPOSURE_ENABLED = True
# Depois de perder o alvo com a cena ESCURA, a exposicao pode simplesmente
# estar baixa demais. Passado este tempo, ela volta a subir em degraus, em vez
# de ficar congelada esperando um alvo que nao aparece justamente por falta de
# exposicao. Foi esse impasse que encerrou a sessao de 2026-09-06 as 04:06.
AUTO_EXPOSURE_LOSS_SEARCH_SECONDS = 8.0
# A busca e deliberadamente mais agressiva que o controle normal. Os 10% a cada
# 5 s existem para nao perturbar uma medicao em curso; com o alvo perdido nao ha
# medicao para perturbar e o mount ja esta parado. No ritmo normal a exposicao
# so dobrava em 64 s, contra os 75 s do limite de perda: chegava tarde demais.
AUTO_EXPOSURE_LOSS_SEARCH_STEP_FRACTION = 0.35
AUTO_EXPOSURE_LOSS_SEARCH_INTERVAL_SECONDS = 3.0
# Espera antes da busca quando o beacon sumiu SAUDAVEL, isto e, quando a perda
# parece ocultacao e nao falta de exposicao. Uma embarcacao na baia tira o feixe
# inteiro de um sinal em plena forma; falta de exposicao produz desvanecimento,
# com o contraste raspando o limite por minutos antes de a ilha sumir. Sao
# diagnosticos opostos e pedem respostas opostas: na ocultacao a resposta certa
# e congelar tudo e esperar, porque o feixe volta como estava e o ceu nao muda
# em dois minutos (hoje o fundo subiu 7 -> 40 contagens em 3 h, ~0,2/min).
# 150 s cobrem a ocultacao tipica com folga.
AUTO_EXPOSURE_LOSS_SEARCH_OCCLUSION_SECONDS = 150.0
# CNR abaixo do qual a perda e lida como DESVANECIMENTO e a busca comeca cedo.
# Medido nas duas sessoes reais, no ultimo alvo confiavel antes da perda:
#   06/09 (faltava exposicao mesmo): CNR mediano 7,5-8,0, p10 7,0
#   09/09 (ocultacao, beacon intacto): CNR mediano 16,9, p10 15,3
# Separacao limpa e sem sobreposicao. O nivel absoluto (pico - fundo local) NAO
# serve: deu 6,2-8,7 contra 4,1-8,0 nas mesmas janelas. O valor fica acima de
# AUTO_EXPOSURE_CNR_LOW de proposito, para o caso duvidoso cair no lado da
# espera: errar para ocultacao custa 150 s de um orcamento de 600 s, errar para
# desvanecimento dispara uma rampa na cena errada.
AUTO_EXPOSURE_LOSS_FADING_CNR = 10.0
# Fundo maximo que a BUSCA pode produzir, em contagens. Mais baixo que o limite
# do controle normal (210) de proposito: la existe um alvo medido e sabe-se o
# que se esta fazendo; aqui a busca e cega e precisa preservar a margem em que
# o alvo ainda apareceria. A rampa e multiplicativa e leva o fundo junto com o
# sinal: em 2026-09-09 ela subiu de 764 para 7584 us em 23 s e arrastou o fundo
# de 23 para 255 contagens. A cena saturou, o alvo perdeu contraste em qualquer
# ponto do quadro, e a reducao ficou travada porque so roda com alvo confiavel.
# O tracker morreu dentro do buraco que a propria busca cavou. Com este limite
# a mesma rampa teria parado perto de 3000 us, com o fundo em ~90 contagens e a
# cena ainda legivel. O criterio e sobre o fundo PREVISTO do proximo degrau: o
# fundo de agora ja e resultado do degrau anterior e sempre chega tarde.
AUTO_EXPOSURE_LOSS_SEARCH_BACKGROUND_LIMIT = 120.0
# Tempo no teto da busca antes de DESFAZER a rampa. A busca e uma hipotese com
# prazo: refutada, a exposicao volta ao valor de onde partiu, porque uma cena
# estourada impede qualquer reaquisicao, inclusive a do operador olhando as
# imagens de evento.
AUTO_EXPOSURE_LOSS_SEARCH_RETURN_SECONDS = 20.0
# O piso e uma trava de seguranca, nao um alvo de projeto: o controlador so
# desce ate o CNR sair da faixa e para sozinho. Com 1000 us ele ja encostava no
# piso a noite, de ceu escuro, e ao amanhecer -- quando o fundo sobe e a
# exposicao precisa CAIR -- ficaria sem para onde ir, encurtando justamente a
# janela que se quer caracterizar.
AUTO_EXPOSURE_MIN_US = 200.0
AUTO_EXPOSURE_MAX_US = 18000.0
AUTO_EXPOSURE_CNR_LOW = 8.0
AUTO_EXPOSURE_CNR_HIGH = 16.0
# Piso ABSOLUTO de sinal, em contagens acima do fundo local. O CNR e uma medida
# de CONTRASTE e para de ser confiavel quando o sinal se aproxima da
# quantizacao: num sensor de 8 bits, um corte de 5% na exposicao pode nao mudar
# nenhum inteiro lido, o CNR aparenta nao ter caido e o controlador corta de
# novo. Foi o que se viu em 2026-09-09: a exposicao desceu de 788 para 426 us
# com o CNR parado entre 16 e 17, sem sinal de que estivesse piorando.
AUTO_EXPOSURE_MIN_TARGET_LEVEL = 30.0
AUTO_EXPOSURE_MIN_TRUSTED_FRACTION = 0.95
AUTO_EXPOSURE_UPDATE_SECONDS = 5.0
AUTO_EXPOSURE_HISTORY_SECONDS = 2.0
AUTO_EXPOSURE_MIN_SAMPLES = 8
AUTO_EXPOSURE_MAX_STEP_FRACTION = 0.10
AUTO_EXPOSURE_REDUCTION_STEP_FRACTION = 0.05
AUTO_EXPOSURE_BACKGROUND_PERCENTILE = 99.0
AUTO_EXPOSURE_BACKGROUND_HIGH = 235.0
AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT = 210.0
AUTO_EXPOSURE_SATURATION_LEVEL = 250
AUTO_EXPOSURE_SATURATION_FRACTION = 0.002
AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS = 3.0
AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS = 0.5
AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS = 5.0

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
# Ausencias abaixo disso nao viram evento. Na sessao de 2026-09-06 foram 453
# episodios, mediana de 0,32 s e apenas 4 acima de 5 s: 1518 eventos suprimidos
# afogaram os poucos que importavam.
EVENT_MIN_ABSENCE_SECONDS = 1.0

# ===== MODO SOMBRA DO VIES LENTO =====
# Observa uma deriva de janela longa e registra quando uma zona de repouso mais
# apertada teria mandado corrigir. NAO comanda nada: existe para decidir com
# dado se vale apertar HOLD_ENTER_RADIUS_PX, sem arriscar oscilacao numa sessao
# real. Nas sessoes de 04/09 e 06/09 sobrou um vies parado de 0,60 e 0,47 px,
# que custa 23% e 16% do erro mediano e nunca e corrigido porque fica abaixo
# dos 2,0 px que acordam o controle.
SHADOW_BIAS_WINDOW_SECONDS = 60.0
SHADOW_BIAS_WARMUP_SECONDS = 20.0
SHADOW_BIAS_TRIGGER_PX = 0.6
TRACKER_EVENT_IMAGE_LIMIT = 200
TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS = 30.0


def roi_size_for_backend(backend: str) -> int:
    return IDS_ROI_SIZE_PX if str(backend).lower() == "ids" else ASI_ROI_SIZE_PX


if ASI_ROI_SIZE_PX < 200 or IDS_ROI_SIZE_PX < 128:
    raise ValueError("A ROI do tracker ficou pequena demais para operacao segura.")
if not 0 < HOLD_RADIUS_AB_ALTERNATE_PX < HOLD_EXIT_RADIUS_PX:
    raise ValueError("O raio alternativo do A/B precisa caber na zona de repouso.")
if HOLD_RADIUS_AB_BLOCK_SECONDS <= 0:
    raise ValueError("O bloco do A/B da zona de repouso precisa ser positivo.")
if not 0 < HOLD_ENTER_RADIUS_PX < HOLD_EXIT_RADIUS_PX:
    raise ValueError("A zona de repouso precisa satisfazer 0 < entrada < saida.")
if not (
    HOLD_EXIT_RADIUS_PX < FAST_CORRECTION_RADIUS_PX
    and 0 < SLOW_BIAS_WARMUP_SECONDS <= SLOW_BIAS_WINDOW_SECONDS
    and SLOW_CORRECTION_PERSISTENCE_SECONDS > 0
):
    raise ValueError("Os tempos e raios do controle em duas escalas sao invalidos.")
if not (
    0 < FAST_ERROR_CONFIRM_SECONDS <= FAST_ERROR_WINDOW_SECONDS
    and 0.5 < FAST_ERROR_MIN_LARGE_FRACTION <= 1.0
    and 0.5 < FAST_ERROR_MIN_DIRECTION_COHERENCE <= 1.0
    and FAST_ERROR_MIN_SAMPLES >= 3
):
    raise ValueError("A confirmacao vetorial do erro grande e invalida.")
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
    and AUTO_EXPOSURE_LOSS_SEARCH_SECONDS > 0
    and 0 < SHADOW_BIAS_WARMUP_SECONDS <= SHADOW_BIAS_WINDOW_SECONDS
    and SHADOW_BIAS_TRIGGER_PX > 0
    and 0 < AUTO_EXPOSURE_LOSS_SEARCH_STEP_FRACTION < 1
    and AUTO_EXPOSURE_LOSS_SEARCH_INTERVAL_SECONDS > 0
    and AUTO_EXPOSURE_LOSS_SEARCH_OCCLUSION_SECONDS >= AUTO_EXPOSURE_LOSS_SEARCH_SECONDS
    and AUTO_EXPOSURE_CNR_LOW <= AUTO_EXPOSURE_LOSS_FADING_CNR < AUTO_EXPOSURE_CNR_HIGH
    and 0 < AUTO_EXPOSURE_LOSS_SEARCH_BACKGROUND_LIMIT
    <= AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT
    and AUTO_EXPOSURE_LOSS_SEARCH_RETURN_SECONDS > 0
    and 0 < AUTO_EXPOSURE_CNR_LOW < AUTO_EXPOSURE_CNR_HIGH
    and AUTO_EXPOSURE_MIN_TARGET_LEVEL > 0
    and 0.5 < AUTO_EXPOSURE_MIN_TRUSTED_FRACTION <= 1.0
    and AUTO_EXPOSURE_UPDATE_SECONDS >= AUTO_EXPOSURE_HISTORY_SECONDS > 0
    and AUTO_EXPOSURE_MIN_SAMPLES >= 2
    and 0 < AUTO_EXPOSURE_MAX_STEP_FRACTION < 1
    and 0 < AUTO_EXPOSURE_REDUCTION_STEP_FRACTION
    <= AUTO_EXPOSURE_MAX_STEP_FRACTION
    and 0 < AUTO_EXPOSURE_BACKGROUND_INCREASE_LIMIT
    < AUTO_EXPOSURE_BACKGROUND_HIGH
    < 255
    and 0 < AUTO_EXPOSURE_SATURATION_LEVEL <= 255
    and 0 < AUTO_EXPOSURE_SATURATION_FRACTION < 1
    and AUTO_EXPOSURE_ROLLBACK_WINDOW_SECONDS
    > AUTO_EXPOSURE_ROLLBACK_LOSS_SECONDS
    > 0
    and AUTO_EXPOSURE_SAFETY_UPDATE_SECONDS > 0
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
