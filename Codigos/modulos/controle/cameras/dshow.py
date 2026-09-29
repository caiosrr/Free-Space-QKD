"""Camera qualquer pelo DirectShow do Windows, via OpenCV.

Existe para a camera do CBPF, uma The Imaging Source DMK 27AUR0135 (USB 3,
monocromatica, 1280 x 960, 8 bits), usada la pelo IC Capture. Cameras da
Imaging Source aparecem no Windows como dispositivos DirectShow, e o OpenCV fala
DirectShow sem SDK nenhum: e o caminho com menos coisa para instalar num PC que
nao e nosso.

O que o DirectShow NAO garante e o controle fino de exposicao e ganho: o OpenCV
os expoe de forma diferente para cada driver. Por isso o registrador usa a
janela de propriedades do PROPRIO driver (abrir_ajustes), a mesma do IC
Capture, e registra o que a camera entrega.

O IC Capture precisa estar FECHADO: ele segura a camera.
"""

from __future__ import annotations

import cv2
import numpy as np


class CameraDirectShow:
    def __init__(self, indice: int = 0, largura: int = 1280, altura: int = 960) -> None:
        self.indice = int(indice)
        self.largura = int(largura)
        self.altura = int(altura)
        self.cap: cv2.VideoCapture | None = None
        self.current_roi = None

    def connect(self) -> None:
        if self.cap is not None:
            return
        cap = cv2.VideoCapture(self.indice, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"Nenhuma camera DirectShow no indice {self.indice}. "
                "O IC Capture esta fechado? Liste com --listar-cameras."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.largura)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.altura)
        # Sempre o quadro mais novo: sem isto o driver entrega quadros velhos
        # da fila, e o horario gravado deixa de corresponder a imagem.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        ok, quadro = cap.read()
        if not ok or quadro is None:
            self.disconnect()
            raise RuntimeError("A camera abriu mas nao entregou quadro.")
        h, w = quadro.shape[:2]
        self.current_roi = (w, h, 0, 0)
        print(f"Camera DirectShow {self.indice}: {w}x{h}")

    def abrir_ajustes(self) -> None:
        """Janela de propriedades do proprio driver: exposicao, ganho etc."""
        if self.cap is not None:
            self.cap.set(cv2.CAP_PROP_SETTINGS, 1)

    def capture(self, exposure_seconds: float | None = None) -> np.ndarray:
        """Quadro em tons de cinza. A exposicao e a do driver; o argumento e
        aceito so para ter a mesma interface das outras cameras."""
        if self.cap is None:
            raise RuntimeError("Camera DirectShow nao conectada.")
        ok, quadro = self.cap.read()
        if not ok or quadro is None:
            raise RuntimeError("Falha ao ler quadro da camera DirectShow.")
        if quadro.ndim == 3:
            # Camera monocromatica chega com os tres canais iguais.
            quadro = quadro[:, :, 0] if np.array_equal(quadro[:, :, 0], quadro[:, :, 1]) \
                else cv2.cvtColor(quadro, cv2.COLOR_BGR2GRAY)
        return quadro

    def disconnect(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def listar_cameras(maximo: int = 6) -> list[tuple[int, int, int]]:
    """(indice, largura, altura) de cada camera DirectShow que abre."""
    achadas = []
    # Indices sem camera geram avisos do OpenCV que so poluem a saida.
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass
    for i in range(maximo):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, quadro = cap.read()
            if ok and quadro is not None:
                achadas.append((i, quadro.shape[1], quadro.shape[0]))
        cap.release()
    return achadas
