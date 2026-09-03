"""Janela de acompanhamento do tracker.

Este modulo so desenha informacoes; nao participa da medicao nem do controle.
"""

import cv2

from modulos.configuracoes.tracker import (
    HOLD_ENTER_RADIUS_PX,
    HOLD_EXIT_RADIUS_PX,
    TEMPORAL_RECOVERY_VALID_FRAMES,
)
from modulos.controle.tracker_dashboard import TrackerDashboard


def tracking_status(
    state: dict,
    *,
    spot_touches_border: bool,
    temporal_outlier: bool,
    instant_signal: bool,
    measurement_valid: bool,
) -> tuple[str, tuple[int, int, int]]:
    """Traduz o estado tecnico em uma mensagem curta para o operador."""
    if state["safety_stop_reason"]:
        return "PARADA DE SEGURANCA", (0, 0, 255)
    if spot_touches_border:
        return "ILHA NA BORDA", (0, 0, 255)
    if state.get("optical_quality_phase") == "anomalia":
        return "ANOMALIA OPTICA - MOUNT PARADO", (0, 0, 255)
    if state.get("optical_quality_phase") == "recuperando":
        return "LUZ NORMALIZANDO - MOUNT PARADO", (0, 165, 255)
    if state.get("optical_quality_phase") == "aquecendo":
        return "MEDINDO APARENCIA NORMAL", (0, 165, 255)
    if temporal_outlier:
        return "FRAME REJEITADO - MOUNT PARADO", (0, 0, 255)
    if not instant_signal:
        return "SEM SINAL - AGUARDANDO", (0, 0, 255)
    if not measurement_valid:
        return "FORMANDO MEDIA / CONFIRMANDO SINAL", (0, 165, 255)
    if state["preflight_active"]:
        return "AUTOTESTE - RECUPERANDO", (255, 200, 0)
    if state["brake_active"]:
        return "FREIO DE SEGURANCA", (0, 0, 255)
    if state.get("control_error_source") == "aguardando_vies":
        return "DERIVA POSSIVEL - CONFIRMANDO", (0, 165, 255)
    if state["hold_active"] and not state.get("slow_bias_ready", False):
        return "FORMANDO REFERENCIA LENTA", (0, 165, 255)
    if state["hold_active"]:
        return "CENTRALIZADO - REPOUSO", (0, 255, 0)
    return "RASTREANDO", (0, 255, 255)


class TrackerDisplay:
    """Renderiza o frame e os indicadores essenciais da sessao."""

    def __init__(self, roi_w, roi_h, target_x, target_y, display_h=1080):
        self.window_name = "Tracker continuo"
        self.display_h = int(display_h)
        self.display_w = max(1, int(round(roi_w * self.display_h / roi_h)))
        self.scale_x = self.display_w / roi_w
        self.scale_y = self.display_h / roi_h
        self.target_x = int(target_x * self.scale_x)
        self.target_y = int(target_y * self.scale_y)
        self.roi_w = int(roi_w)
        self.roi_h = int(roi_h)
        self.target_x_raw = float(target_x)
        self.target_y_raw = float(target_y)
        self.dashboard = None
        try:
            self.dashboard = TrackerDashboard()
            print(f"Painel web do tracker: {self.dashboard.url}")
            print("A janela OpenCV continua ativa como redundancia.\n")
        except Exception as exc:
            print(f"Aviso: painel web indisponivel ({exc}); usando janela OpenCV.")
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
        )

    def show(
        self,
        frame,
        *,
        x_cm,
        y_cm,
        dx,
        dy,
        state,
        status,
        status_color,
        elapsed_hours,
        session_hours,
    ):
        def ratio_text(value):
            return "--" if value is None else f"{value:.2f}x"

        radial_error = (dx * dx + dy * dy) ** 0.5
        if self.dashboard is not None:
            try:
                self.dashboard.update(
                    frame,
                    {
                        **state,
                        "status": status,
                        "elapsed_hours": elapsed_hours,
                        "session_hours": session_hours,
                        "roi_width_px": self.roi_w,
                        "roi_height_px": self.roi_h,
                        "target_x_px": self.target_x_raw,
                        "target_y_px": self.target_y_raw,
                        "x_cm_px": x_cm if state["has_signal"] else None,
                        "y_cm_px": y_cm if state["has_signal"] else None,
                        "dx_px": dx if state["has_signal"] else None,
                        # O painel usa Y cartesiano; processamento e CSV mantem
                        # a convencao de imagem, positiva para baixo.
                        "dy_up_px": -dy if state["has_signal"] else None,
                        "radial_error_px": (
                            radial_error if state["has_signal"] else None
                        ),
                        "hold_enter_radius_px": HOLD_ENTER_RADIUS_PX,
                        "hold_exit_radius_px": HOLD_EXIT_RADIUS_PX,
                    },
                )
            except Exception as exc:
                print(f"Aviso: painel web foi desativado ({exc}).")
                self.dashboard.close()
                self.dashboard = None

        image = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        image = cv2.resize(
            image,
            (self.display_w, self.display_h),
            interpolation=cv2.INTER_NEAREST,
        )
        x_cm = int(x_cm * self.scale_x)
        y_cm = int(y_cm * self.scale_y)
        cv2.line(image, (self.target_x, 0), (self.target_x, self.display_h), (255, 0, 0), 2)
        cv2.line(image, (0, self.target_y), (self.display_w, self.target_y), (0, 0, 255), 2)
        cv2.circle(image, (x_cm, y_cm), 8, status_color, -1)

        lines = [
            (
                f"Erro: {radial_error:.1f} px | "
                f"X={dx:+.1f} px | Y={dy:+.1f} px",
                (0, 255, 0),
            ),
            (
                "Detector: ilha travada | Calibracao: continua | "
                f"zona parada={HOLD_ENTER_RADIUS_PX:g}/{HOLD_EXIT_RADIUS_PX:g} px",
                (0, 255, 255),
            ),
            (
                f"Erro angular: Az={state['err_az_deg']:+.5f} deg | "
                f"Alt={state['err_alt_deg']:+.5f} deg",
                (255, 255, 255),
            ),
            (
                f"Velocidade enviada: Az={state['cmd_az_deg_s']:+.4f} deg/s | "
                f"Alt={state['cmd_alt_deg_s']:+.4f} deg/s",
                (255, 255, 255),
            ),
            (
                f"Loops: medicao={state['measurement_hz']:.1f} Hz | "
                f"controle={state['control_loop_hz']:.1f} Hz",
                (255, 200, 0),
            ),
            (
                f"Sessao: {elapsed_hours:.2f}/{session_hours:.2f} h | "
                f"deslocamento Az={state['offset_az_deg']:+.3f} deg | "
                f"Alt={state['offset_alt_deg']:+.3f} deg",
                (200, 200, 200),
            ),
            (
                f"Media: {state['temporal_frame_count']} frames / "
                f"{state['temporal_window_s']:.2f}s | recuperacao="
                f"{state['recovery_valid_frames']}/{TEMPORAL_RECOVERY_VALID_FRAMES} | "
                f"sem sinal={state['signal_lost_s']:.1f}s | "
                f"exp={state['exposure_us']:.0f} us",
                (180, 220, 255),
            ),
            (
                f"Qualidade: {state['optical_quality_phase']} | "
                f"int={ratio_text(state['optical_intensity_ratio'])} | "
                f"area={ratio_text(state['optical_area_ratio'])} | "
                f"tam={ratio_text(state['optical_width_ratio'])}/"
                f"{ratio_text(state['optical_height_ratio'])} | "
                f"estavel={state['optical_stable_s']:.1f}s",
                (160, 255, 180),
            ),
        ]
        for index, (text, color) in enumerate(lines):
            cv2.putText(
                image,
                text,
                (40, 60 + (index * 50)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.74,
                color,
                2,
                cv2.LINE_AA,
            )

        status_x = max(40, self.display_w - max(360, 22 * len(status)))
        cv2.putText(
            image,
            status,
            (status_x, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            status_color,
            3,
            cv2.LINE_AA,
        )
        cv2.imshow(self.window_name, image)

    def should_close(self) -> bool:
        key = cv2.waitKeyEx(1)
        return key in (ord("q"), ord("Q"), 27) or (
            cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1
        )

    def close(self) -> None:
        if self.dashboard is not None:
            self.dashboard.close()
        cv2.destroyAllWindows()

    @staticmethod
    def close_all() -> None:
        """Fecha janelas criadas antes de a interface ficar pronta."""
        cv2.destroyAllWindows()
