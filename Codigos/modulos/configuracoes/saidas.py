"""Onde cada etapa grava, para TODAS as cameras, num lugar so.

Existe por causa de um furo de seguranca encontrado em 2026-09-23. As pastas de
saida eram definidas apenas no perfil da IDS (``camera_ids.apply_environment``).
Com qualquer outra camera o tracker caia no padrao ``resultados/debug`` e
gravava a telemetria la, enquanto o vigia e a trava ``mount_em_uso`` olhavam
``Link UFF/resultados/tracker/sessoes``. Numa sessao com a ASI, portanto, o
vigia NUNCA armaria, e a trava diria que o mount estava livre com o tracker
rodando.

A regra agora: quem grava e quem vigia leem os caminhos DAQUI. Mudar a raiz
muda para todos juntos, e nao ha como um lado andar sem o outro.

O nome ``Link UFF`` ficou por compatibilidade com tudo que ja foi gravado e
analisado; ele vale tambem para a bancada da USP.
"""

import os
from pathlib import Path

CODIGOS_DIR = Path(__file__).resolve().parents[2]

RESULTS_DIR = CODIGOS_DIR / "Link UFF" / "resultados"
ACQUISITION_OUTPUT_DIR = RESULTS_DIR / "aquisicao"
CENTER_OF_MASS_OUTPUT_DIR = RESULTS_DIR / "centro_de_massa"
CALIBRATION_OUTPUT_DIR = RESULTS_DIR / "calibracao"
CALIBRATION_METADATA_DIR = CALIBRATION_OUTPUT_DIR / "metadados"
MATRICES_OUTPUT_DIR = RESULTS_DIR / "matrizes"
TRACKER_OUTPUT_DIR = RESULTS_DIR / "tracker"
BEACON_CHARACTERIZATION_OUTPUT_DIR = RESULTS_DIR / "caracterizacao_beacon"
SEM_CORRECAO_OUTPUT_DIR = RESULTS_DIR / "sem_correcao"
# Registrador do lado do CBPF: camera e power meter vendo o beacon da UFF.
CBPF_OUTPUT_DIR = RESULTS_DIR / "cbpf"

# O que os guardas de seguranca vigiam. Derivados das pastas acima, nunca
# escritos a mao em outro arquivo.
TRACKER_SESSOES_DIR = TRACKER_OUTPUT_DIR / "sessoes"
CALIBRACAO_RUNS_DIR = CALIBRATION_OUTPUT_DIR / "continua"


def aplicar_saidas() -> None:
    """Propaga as pastas para os modulos internos, que as leem do ambiente."""
    os.environ["QKD_CAMERA_OUTPUT_DIR"] = str(RESULTS_DIR)
    os.environ["QKD_CENTER_OF_MASS_OUTPUT_DIR"] = str(CENTER_OF_MASS_OUTPUT_DIR)
    os.environ["QKD_CALIBRATION_OUTPUT_DIR"] = str(CALIBRATION_OUTPUT_DIR)
    os.environ["QKD_CALIBRATION_METADATA_DIR"] = str(CALIBRATION_METADATA_DIR)
    os.environ["QKD_CALIBRATION_MATRIX_DIR"] = str(MATRICES_OUTPUT_DIR)
    os.environ["QKD_TRACKER_OUTPUT_DIR"] = str(TRACKER_OUTPUT_DIR)
    os.environ["QKD_BEACON_CHARACTERIZATION_OUTPUT_DIR"] = str(
        BEACON_CHARACTERIZATION_OUTPUT_DIR
    )
