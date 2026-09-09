"""Painel web local do tracker.

O servidor recebe somente copias do frame e do estado ja calculado. Ele nao
participa da deteccao, nao altera a calibracao e nunca envia comandos ao mount.
"""

from __future__ import annotations

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import webbrowser

import cv2
import numpy as np


ASSET_DIR = Path(__file__).with_name("dashboard_tracker")
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class TrackerDashboard:
    """Publica a visualizacao ao vivo em um servidor restrito ao computador."""

    # Janela do historico, igual a do grafico do painel: clicar no grafico so
    # faz sentido se o frame daquele instante ainda existir.
    HISTORY_SECONDS = 120.0

    def __init__(
        self,
        *,
        frame_hz: float = 4.0,
        open_browser: bool = True,
        history_seconds: float = HISTORY_SECONDS,
    ):
        self._lock = threading.Lock()
        self._state: dict = {"connected": False}
        self._frame_jpeg: bytes | None = None
        self._last_frame_encode = 0.0
        self._frame_interval_s = 1.0 / max(float(frame_hz), 0.5)
        # Anel de frames ja codificados. O custo e so memoria: o JPEG do quadro
        # ao vivo ja e produzido de qualquer jeito, e guardar os mesmos bytes
        # nao gasta CPU nenhuma. Uma ROI de 256x256 em q82 da ~11 kB, entao
        # 120 s a 4 Hz custam cerca de 5 MB. E a unica forma de responder
        # "o que aconteceu ali?" depois que o grafico mostra a descontinuidade.
        self._history: deque = deque(maxlen=max(1, int(history_seconds * frame_hz)))
        self._closed = False

        handler = self._make_handler()
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", 8765), handler)
        except OSError:
            # Evita derrubar o tracker quando outra sessao deixou a porta ocupada.
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}/"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="tracker-dashboard",
            daemon=True,
        )
        self._thread.start()

        if open_browser:
            threading.Thread(
                target=self._open_browser_after_start,
                name="tracker-dashboard-browser",
                daemon=True,
            ).start()

    def _open_browser_after_start(self) -> None:
        # Da tempo para a janela OpenCV ser criada antes de o navegador assumir
        # o foco. A janela antiga continua disponivel como redundancia operacional.
        time.sleep(1.0)
        if not self._closed:
            webbrowser.open(self.url, new=1)

    def _make_handler(self):
        dashboard = self

        class DashboardHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - nome da classe base
                path = self.path.split("?", 1)[0]
                if path == "/api/state":
                    with dashboard._lock:
                        body = json.dumps(
                            dashboard._state,
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8", cache=False)
                    return
                if path == "/api/historico":
                    body = json.dumps(dashboard._janela_do_historico()).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8", cache=False)
                    return
                if path == "/api/historico.jpg":
                    consulta = self.path.split("?", 1)[1] if "?" in self.path else ""
                    alvo = None
                    for parte in consulta.split("&"):
                        if parte.startswith("t="):
                            try:
                                alvo = float(parte[2:])
                            except ValueError:
                                alvo = None
                    if alvo is None:
                        self.send_error(400, "falta t=<unix>")
                        return
                    quando, body = dashboard._frame_no_instante(alvo)
                    if body is None:
                        self.send_response(204)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store, max-age=0")
                    self.send_header("X-Frame-Unix-S", f"{quando:.3f}")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/frame.jpg":
                    with dashboard._lock:
                        body = dashboard._frame_jpeg
                    if body is None:
                        self.send_response(204)
                        self.end_headers()
                    else:
                        self._send(body, "image/jpeg", cache=False)
                    return

                filename = "index.html" if path in ("", "/") else path.lstrip("/")
                if filename not in {"index.html", "styles.css", "app.js"}:
                    self.send_error(404)
                    return
                asset = ASSET_DIR / filename
                try:
                    body = asset.read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                self._send(body, CONTENT_TYPES[asset.suffix])

            def _send(self, body: bytes, content_type: str, *, cache: bool = True) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Cache-Control",
                    "public, max-age=60" if cache else "no-store, max-age=0",
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args) -> None:
                return

        return DashboardHandler

    def update(self, frame: np.ndarray, state: dict) -> None:
        """Atualiza o snapshot JSON e, em baixa taxa, a imagem mostrada."""
        now = time.perf_counter()
        encoded: bytes | None = None
        if now - self._last_frame_encode >= self._frame_interval_s:
            success, buffer = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 82],
            )
            if success:
                encoded = buffer.tobytes()
                self._last_frame_encode = now

        clean_state = self._json_safe(state)
        clean_state["connected"] = True
        clean_state["updated_unix_s"] = time.time()
        with self._lock:
            self._state = clean_state
            if encoded is not None:
                self._frame_jpeg = encoded
                self._history.append((clean_state["updated_unix_s"], encoded))

    def _frame_no_instante(self, alvo_unix: float):
        """Frame gravado mais proximo de ``alvo_unix``. ``(None, None)`` se vazio."""
        with self._lock:
            if not self._history:
                return None, None
            melhor = min(self._history, key=lambda item: abs(item[0] - alvo_unix))
        return melhor[0], melhor[1]

    def _janela_do_historico(self) -> dict:
        with self._lock:
            if not self._history:
                return {"vazio": True}
            return {
                "vazio": False,
                "inicio_unix_s": self._history[0][0],
                "fim_unix_s": self._history[-1][0],
                "frames": len(self._history),
            }

    @classmethod
    def _json_safe(cls, value):
        """Converte escalares NumPy e valores nao finitos para JSON valido."""
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
