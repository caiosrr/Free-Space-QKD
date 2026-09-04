import csv
import inspect
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import modulos.controle.Tracker as tracker
import modulos.controle.mount_control as mount_control
import modulos.controle.tracker_aquisicao as tracker_aquisicao
import modulos.controle.tracker_autoteste as tracker_autoteste
import modulos.controle.tracker_camera as tracker_camera
import modulos.controle.tracker_loop as tracker_loop
import modulos.controle.tracker_qualidade as tracker_qualidade
import modulos.controle.tracker_seguranca as tracker_seguranca
import modulos.controle.tracker_telemetria as tracker_telemetria
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_exposicao import (
    AutoExposureController,
    border_background_metrics,
)
from modulos.controle.tracker_controle import (
    DirectionalErrorEstimator,
    FinePulseAxis,
    SlowBiasEstimator,
    SlowCorrectionGate,
)
from modulos.controle.tracker_medicao import TemporalFrameEstimator
from modulos.controle.tracker_telemetria import TrackerCsvLogger
from modulos.visao import detector_ilhas
import diversos.legado.calibracao_por_pontos as calibracao


class TrackerSafetyTests(unittest.TestCase):
    @staticmethod
    def _spot_frame(x_px, y_px, size=64):
        yy, xx = np.indices((size, size), dtype=np.float32)
        spot = np.exp(-((xx - x_px) ** 2 + (yy - y_px) ** 2) / (2.0 * 2.5**2))
        return np.clip(spot * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def _optical_candidate(
        raw_total=1000.0,
        area=100.0,
        bbox_w=12.0,
        bbox_h=10.0,
        compactness=0.7,
        similarity=0.9,
        x_cm=32.0,
        y_cm=32.0,
    ):
        return {
            "raw_total": raw_total,
            "area": area,
            "bbox_w": bbox_w,
            "bbox_h": bbox_h,
            "compactness": compactness,
            "similarity_primary": similarity,
            "x_cm": x_cm,
            "y_cm": y_cm,
        }

    @staticmethod
    def _feed_exposure(controller, frame, peak, trusted=True):
        decision = None
        for timestamp in (0.0, 0.5, 1.0, 1.5, 2.0):
            decision = controller.observe(
                timestamp,
                frame,
                target_peak=peak,
                trusted_target=trusted,
            )
        return decision

    def test_auto_exposure_ignores_central_beacon_in_background(self):
        frame = np.full((64, 64), 10, dtype=np.uint8)
        frame[28:36, 28:36] = 255
        background, saturated = border_background_metrics(frame)
        self.assertEqual(background, 10.0)
        self.assertEqual(saturated, 0.0)

    def test_auto_exposure_changes_slowly_toward_target(self):
        frame = np.full((32, 32), 10, dtype=np.uint8)
        low = AutoExposureController(
            10000,
            update_seconds=2.0,
            history_seconds=2.0,
            min_samples=3,
            started_at=0.0,
        )
        decision = self._feed_exposure(low, frame, 100.0)
        self.assertTrue(decision.changed)
        self.assertEqual(decision.exposure_us, 11000.0)
        self.assertEqual(decision.reason, "aumento_sinal_baixo")

        high = AutoExposureController(
            10000,
            update_seconds=2.0,
            history_seconds=2.0,
            min_samples=3,
            started_at=0.0,
        )
        decision = self._feed_exposure(high, frame, 220.0)
        self.assertTrue(decision.changed)
        self.assertEqual(decision.exposure_us, 9000.0)
        self.assertEqual(decision.reason, "reducao_sinal_alto")

    def test_auto_exposure_freezes_without_target_but_protects_background(self):
        dark = np.full((32, 32), 10, dtype=np.uint8)
        controller = AutoExposureController(
            10000,
            update_seconds=2.0,
            history_seconds=2.0,
            min_samples=3,
            started_at=0.0,
        )
        decision = self._feed_exposure(controller, dark, 50.0, trusted=False)
        self.assertFalse(decision.changed)
        self.assertEqual(decision.exposure_us, 10000.0)
        self.assertEqual(decision.reason, "congelada_sem_alvo_confiavel")

        bright = np.full((32, 32), 210, dtype=np.uint8)
        safety = AutoExposureController(
            10000,
            update_seconds=5.0,
            history_seconds=2.0,
            min_samples=3,
            started_at=0.0,
        )
        for timestamp in (0.0, 0.5, 1.0):
            decision = safety.observe(
                timestamp,
                bright,
                target_peak=None,
                trusted_target=False,
            )
        self.assertTrue(decision.changed)
        self.assertEqual(decision.exposure_us, 9000.0)
        self.assertEqual(decision.reason, "reducao_fundo_saturando")

    def test_slow_bias_estimator_uses_a_robust_time_window(self):
        estimator = SlowBiasEstimator(window_s=4.0, warmup_s=2.0, min_samples=3)
        self.assertFalse(estimator.observe(0.0, 2.0, 0.0).ready)
        self.assertFalse(estimator.observe(1.0, 20.0, 0.0).ready)
        estimate = estimator.observe(2.0, 2.2, 0.0)
        self.assertTrue(estimate.ready)
        self.assertAlmostEqual(estimate.dx_px, 2.2)
        self.assertAlmostEqual(estimate.span_s, 2.0)

        estimate = estimator.observe(5.0, 1.8, 0.0)
        self.assertEqual(estimate.span_s, 4.0)
        self.assertAlmostEqual(estimate.dx_px, 2.2)

    def test_directional_error_rejects_oscillation_and_accepts_displacement(self):
        oscillating = DirectionalErrorEstimator(
            radius_threshold_px=5.0,
            window_s=3.0,
            confirm_s=2.0,
            min_large_fraction=0.7,
            min_direction_coherence=0.8,
            min_samples=5,
        )
        estimate = None
        for index in range(13):
            dx = 6.0 if index % 2 == 0 else -6.0
            estimate = oscillating.observe(index * 0.2, dx, 0.0)
        self.assertIsNotNone(estimate)
        self.assertFalse(estimate.ready)
        self.assertLess(estimate.direction_coherence, 0.2)

        displaced = DirectionalErrorEstimator(
            radius_threshold_px=5.0,
            window_s=3.0,
            confirm_s=2.0,
            min_large_fraction=0.7,
            min_direction_coherence=0.8,
            min_samples=5,
        )
        for index in range(11):
            estimate = displaced.observe(index * 0.2, 6.0, 1.0)
        self.assertTrue(estimate.ready)
        self.assertGreater(estimate.direction_coherence, 0.99)
        self.assertAlmostEqual(estimate.dx_px, 6.0)
        self.assertAlmostEqual(estimate.dy_px, 1.0)

    def test_slow_gate_requires_time_and_confirms_large_error(self):
        gate = SlowCorrectionGate(
            enter_radius_px=1.0,
            exit_radius_px=2.0,
            persistence_s=1.5,
            fast_radius_px=5.0,
        )
        decision = gate.update(
            0.0, fast_radius_px=2.3, slow_radius_px=2.2, slow_ready=True
        )
        self.assertTrue(decision.hold_active)
        self.assertEqual(decision.source, "aguardando_vies")
        self.assertTrue(
            gate.update(
                1.4, fast_radius_px=2.4, slow_radius_px=2.2, slow_ready=True
            ).hold_active
        )
        decision = gate.update(
            1.5, fast_radius_px=2.4, slow_radius_px=2.2, slow_ready=True
        )
        self.assertFalse(decision.hold_active)
        self.assertEqual(decision.source, "vies_lento")

        self.assertFalse(
            gate.update(
                2.0, fast_radius_px=1.4, slow_radius_px=1.2, slow_ready=True
            ).hold_active
        )
        self.assertTrue(
            gate.update(
                2.1, fast_radius_px=1.0, slow_radius_px=0.9, slow_ready=True
            ).hold_active
        )
        decision = gate.update(
            3.0,
            fast_radius_px=5.1,
            fast_confirmed=False,
            fast_persistence_s=1.0,
            slow_ready=False,
        )
        self.assertTrue(decision.hold_active)
        self.assertEqual(decision.source, "aguardando_erro_grande")
        decision = gate.update(
            4.1,
            fast_radius_px=5.1,
            fast_confirmed=True,
            fast_persistence_s=2.1,
            slow_ready=False,
        )
        self.assertFalse(decision.hold_active)
        self.assertEqual(decision.source, "erro_grande_persistente")

    def test_fine_pulse_uses_minimum_rate_then_waits_for_settling(self):
        pulse = FinePulseAxis(
            1.0,
            correction_fraction=0.5,
            min_pulse_s=0.1,
            max_pulse_s=0.2,
            settle_s=0.5,
        )
        self.assertEqual(pulse.command(0.0, 1.0), 1.0)
        self.assertEqual(pulse.command(0.1, 1.0), 1.0)
        self.assertEqual(pulse.command(0.21, 1.0), 0.0)
        self.assertEqual(pulse.command(0.69, 1.0), 0.0)
        self.assertEqual(pulse.command(0.71, -1.0), -1.0)

    def test_preflight_mount_delta_uses_calibration(self):
        matrix = np.array([[0.001, 0.0], [0.0, 0.002]])
        delta_az, delta_alt = tracker_autoteste.calcular_deslocamento_mount(matrix)
        self.assertAlmostEqual(delta_az, 0.008)
        self.assertAlmostEqual(delta_alt, 0.012)

    def test_preflight_recovery_requires_displacement_then_three_frames(self):
        checker = tracker_autoteste.VerificadorRecuperacao()
        self.assertFalse(checker.observar(True, 1.0, 0.0))
        self.assertFalse(checker.observar(True, 6.0, 0.0))
        self.assertFalse(checker.observar(True, 1.0, 0.0))
        self.assertFalse(checker.observar(True, 1.0, 0.0))
        self.assertTrue(checker.observar(True, 1.0, 0.0))

    def test_preflight_rejects_excessive_angular_step(self):
        with self.assertRaises(ValueError):
            tracker_autoteste.calcular_deslocamento_mount(np.eye(2))

    def test_optical_gate_requires_persistent_change_then_waits_for_recovery(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            recovery_window_s=0.5,
            recovery_accepted_fraction=0.75,
            recovery_min_samples=3,
            min_baseline_frames=2,
        )
        self.assertFalse(gate.observe(0.0, normal).accepted)
        self.assertTrue(gate.observe(0.11, normal).accepted)

        expanded = self._optical_candidate(
            raw_total=300.0,
            area=400.0,
            bbox_w=36.0,
            bbox_h=30.0,
            compactness=0.25,
        )
        rejected = gate.observe(0.2, expanded)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.phase, "normal")
        self.assertTrue(rejected.transient_rejection)
        self.assertTrue(rejected.control_allowed)
        self.assertIn("intensidade_baixa", rejected.reasons)
        self.assertIn("area_expandida", rejected.reasons)

        self.assertFalse(gate.observe(0.35, expanded).accepted)
        persistent = gate.observe(0.51, expanded)
        self.assertEqual(persistent.phase, "anomalia")
        self.assertFalse(persistent.control_allowed)

        self.assertFalse(gate.observe(0.60, normal).accepted)
        self.assertFalse(gate.observe(0.72, normal).accepted)
        recovered = gate.observe(0.82, normal)
        self.assertTrue(recovered.accepted)
        self.assertEqual(recovered.event, "anomalia_optica_recuperada")

    def test_optical_gate_discards_isolated_deformation_without_recovery(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        self.assertTrue(gate.observe(0.11, normal).accepted)

        deformed = self._optical_candidate(area=300.0, bbox_w=30.0)
        rejected = gate.observe(0.2, deformed)
        self.assertFalse(rejected.accepted)
        self.assertTrue(rejected.transient_rejection)
        self.assertTrue(rejected.control_allowed)
        self.assertEqual(rejected.phase, "normal")
        self.assertEqual(rejected.event, "")
        self.assertGreater(rejected.anomaly_fraction, 0.0)

        recovered = gate.observe(0.3, normal)
        self.assertTrue(recovered.accepted)
        self.assertFalse(recovered.transient_rejection)
        self.assertEqual(recovered.phase, "normal")

    def test_optical_gate_waits_after_plain_signal_loss(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            recovery_window_s=0.5,
            recovery_accepted_fraction=0.75,
            recovery_min_samples=3,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        self.assertTrue(gate.observe(0.11, normal).accepted)
        self.assertFalse(gate.observe(0.2, None).accepted)
        self.assertFalse(gate.observe(0.3, normal).accepted)
        self.assertFalse(gate.observe(0.4, normal).accepted)
        self.assertTrue(gate.observe(0.51, normal).accepted)

    def test_optical_gate_rejects_sudden_brightening_and_shrinking(self):
        normal = self._optical_candidate()
        bright_gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            recovery_window_s=0.5,
            recovery_accepted_fraction=0.75,
            recovery_min_samples=3,
            min_baseline_frames=2,
        )
        bright_gate.observe(0.0, normal)
        bright_gate.observe(0.11, normal)
        bright = bright_gate.observe(
            0.2,
            self._optical_candidate(raw_total=3200.0),
        )
        self.assertFalse(bright.accepted)
        self.assertIn("intensidade_alta", bright.reasons)

        small_gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            recovery_window_s=0.5,
            recovery_accepted_fraction=0.75,
            recovery_min_samples=3,
            min_baseline_frames=2,
        )
        small_gate.observe(0.0, normal)
        small_gate.observe(0.11, normal)
        shrunk = small_gate.observe(
            0.2,
            self._optical_candidate(area=35.0, bbox_w=5.0, bbox_h=4.0),
        )
        self.assertFalse(shrunk.accepted)
        self.assertIn("area_reduzida", shrunk.reasons)

    def test_optical_gate_tracks_gradual_intensity_change(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            baseline_window_s=1.0,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        self.assertTrue(gate.observe(0.11, normal).accepted)
        for index in range(1, 21):
            gradual = self._optical_candidate(raw_total=1000.0 + (50.0 * index))
            decision = gate.observe(0.11 + (0.1 * index), gradual)
            self.assertTrue(decision.accepted)
            self.assertEqual(decision.phase, "normal")

    def test_optical_recovery_uses_consensus_instead_of_perfect_sequence(self):
        normal = self._optical_candidate()
        dim = self._optical_candidate(raw_total=250.0)
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=2.0,
            recovery_window_s=3.0,
            recovery_accepted_fraction=0.8,
            recovery_min_samples=8,
            anomaly_entry_min_coverage_s=0.01,
            anomaly_entry_min_bad_frames=1,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        self.assertTrue(gate.observe(0.11, normal).accepted)
        self.assertFalse(gate.observe(0.2, dim).accepted)

        recovered = None
        for index in range(1, 31):
            # Um frame extremo a cada cinco ainda representa 80% compativeis.
            candidate = dim if index % 5 == 0 else normal
            recovered = gate.observe(0.2 + (index * 0.1), candidate)
            if recovered.accepted:
                break
        self.assertIsNotNone(recovered)
        self.assertTrue(recovered.accepted)
        self.assertGreaterEqual(recovered.recovery_fraction, 0.8)
        self.assertEqual(recovered.event, "anomalia_optica_recuperada")

    def test_optical_recovery_rejects_positionally_unstable_candidate(self):
        normal = self._optical_candidate()
        dim = self._optical_candidate(raw_total=250.0)
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.5,
            recovery_window_s=1.0,
            recovery_accepted_fraction=0.8,
            recovery_min_samples=5,
            recovery_position_p90_px=5.0,
            anomaly_entry_min_coverage_s=0.01,
            anomaly_entry_min_bad_frames=1,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        gate.observe(0.11, normal)
        gate.observe(0.2, dim)
        decision = None
        for index in range(1, 12):
            position = 20.0 if index % 2 else 44.0
            candidate = self._optical_candidate(x_cm=position, y_cm=32.0)
            decision = gate.observe(0.2 + index * 0.1, candidate)
        self.assertIsNotNone(decision)
        self.assertFalse(decision.accepted)
        self.assertGreater(decision.position_spread_px, 5.0)

    def test_present_unstable_target_does_not_increment_absence_timer(self):
        timers = tracker_aquisicao.TemporizadoresDisponibilidade()
        timers.observe(0.0, target_present=True, measurement_valid=False)
        unstable = timers.observe(
            80.0,
            target_present=True,
            measurement_valid=False,
        )
        self.assertEqual(unstable.absent_seconds, 0.0)
        self.assertEqual(unstable.unstable_seconds, 80.0)

        timers.observe(81.0, target_present=False, measurement_valid=False)
        absent = timers.observe(
            156.0,
            target_present=False,
            measurement_valid=False,
        )
        self.assertEqual(absent.absent_seconds, 75.0)
        self.assertEqual(absent.unstable_seconds, 0.0)

    def test_shared_state_preserves_first_safety_reason(self):
        state = TrackerState()
        self.assertTrue(state.request_stop("primeiro_motivo"))
        self.assertFalse(state.request_stop("segundo_motivo"))
        self.assertEqual(state.snapshot()["safety_stop_reason"], "primeiro_motivo")

    def test_weak_or_dissimilar_border_candidate_is_not_confirmed(self):
        signature = {"primary": {"raw_peak": 90.0}}
        weak = {
            "toca_borda": True,
            "raw_peak": 7.0,
            "similarity_primary": 0.9,
        }
        dissimilar = {
            "toca_borda": True,
            "raw_peak": 80.0,
            "similarity_primary": 0.2,
        }
        plausible = {
            "toca_borda": True,
            "raw_peak": 80.0,
            "similarity_primary": 0.8,
        }
        self.assertFalse(
            tracker_aquisicao.candidato_borda_compativel(weak, signature)
        )
        self.assertFalse(
            tracker_aquisicao.candidato_borda_compativel(dissimilar, signature)
        )
        self.assertTrue(
            tracker_aquisicao.candidato_borda_compativel(plausible, signature)
        )

    def test_border_confirmation_uses_elapsed_time_and_resets(self):
        guard = tracker_aquisicao.ConfirmacaoBorda(confirm_seconds=1.0)
        self.assertEqual(guard.observe(10.0, True), 0.0)
        self.assertAlmostEqual(guard.observe(10.8, True), 0.8)
        self.assertEqual(guard.observe(10.9, False), 0.0)
        self.assertEqual(guard.observe(11.0, True), 0.0)
        self.assertAlmostEqual(guard.observe(12.0, True), 1.0)

    def test_acquisition_returns_when_display_is_closed(self):
        class FakeLogger:
            def __init__(self):
                self.rows = []

            def write(self, *args, **kwargs):
                self.rows.append((args, kwargs))

            def save_event_frame(self, *args, **kwargs):
                return None

        class FakeDisplay:
            def show(self, *args, **kwargs):
                return None

            def should_close(self):
                return True

        state = TrackerState()
        logger = FakeLogger()
        with (
            patch.object(
                tracker_aquisicao,
                "capture_frame",
                return_value=np.zeros((64, 64), dtype=np.uint8),
            ),
            patch.object(tracker_aquisicao, "medir_laser", return_value=(32.0, 32.0)),
            patch.object(
                tracker_aquisicao.foco,
                "get_focus_debug",
                return_value={"selected": {"raw_peak": 100.0}},
            ),
        ):
            result = tracker_aquisicao.executar_aquisicao(
                state,
                logger,
                FakeDisplay(),
                target_x=32.0,
                target_y=32.0,
                session_started=time.perf_counter(),
                session_hours=1.0,
            )

        self.assertEqual(result.motivo, "encerrado_pelo_usuario")
        self.assertEqual(len(logger.rows), 1)
        self.assertEqual(state.snapshot()["measurement_seq"], 1)

    def test_azimuth_offset_handles_zero_degree_wrap(self):
        offset_az, offset_alt = tracker_seguranca.deslocamentos_desde_inicio(
            359.0,
            10.0,
            4.0,
            9.5,
        )
        self.assertAlmostEqual(offset_az, 5.0)
        self.assertAlmostEqual(offset_alt, -0.5)

    def test_csv_writes_variance_and_forced_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = time.perf_counter()
            logger = TrackerCsvLogger(Path(tmp), started, 359.0, 10.0, 2.0)
            state = TrackerState(
                has_signal=True,
                dx_filt_px=1.0,
                dy_filt_px=2.0,
            ).snapshot()
            logger.write(
                started + 0.01,
                state_values=state,
                status="RASTREANDO",
                x_cm=10.0,
                y_cm=20.0,
                target_x=9.0,
                target_y=18.0,
                dx=1.0,
                dy=2.0,
            )
            logger.write(
                started + 0.02,
                state_values=state,
                status="PARADA",
                x_cm=11.0,
                y_cm=19.0,
                target_x=9.0,
                target_y=18.0,
                dx=2.0,
                dy=1.0,
                event="teste",
            )
            logger.close(reason="teste")

            with logger.csv_path.open(encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["evento_seguranca"], "teste")
            self.assertTrue(logger.summary_path.exists())
            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertIn("target_present_percent_logged", summary)
            self.assertIn("max_target_absent_seconds", summary)
            self.assertIn("max_optical_unstable_seconds", summary)

    def test_terminal_event_frame_bypasses_routine_limits(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            tracker_telemetria, "TRACKER_EVENT_IMAGE_LIMIT", 1
        ), patch.object(
            tracker_telemetria,
            "TRACKER_EVENT_IMAGE_MIN_INTERVAL_SECONDS",
            3600.0,
        ):
            logger = TrackerCsvLogger(Path(tmp), time.perf_counter(), 0.0, 0.0, 2.0)
            frame = np.zeros((32, 32), dtype=np.uint8)
            first = logger.save_event_frame(frame, "anomalia")
            suppressed = logger.save_event_frame(frame, "outra_anomalia")
            terminal = logger.save_event_frame(
                frame,
                "ilha_tocou_a_borda_da_roi",
                critical=True,
            )
            logger.close(reason="teste")

            self.assertIsNotNone(first)
            self.assertIsNone(suppressed)
            self.assertIsNotNone(terminal)
            self.assertTrue(Path(terminal).exists())
            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["event_images_saved"], 1)
            self.assertEqual(summary["event_images_suppressed"], 1)
            self.assertIn("evento_terminal_", summary["terminal_event_frame"])

    def test_multiscale_fit_uses_every_radius(self):
        expected = np.array([[2000.0, 2500.0], [2600.0, -2000.0]])
        records = []
        for radius in (0.004, 0.008, 0.016):
            for label, sign_az, sign_alt in calibracao.DIRECTIONS:
                delta_az = radius * sign_az
                delta_alt = radius * sign_alt
                pixels = expected @ np.array([delta_az, delta_alt])
                records.append(
                    calibracao.RegistroDual(
                        "fine",
                        f"{label}@{radius}",
                        radius,
                        delta_az,
                        delta_alt,
                        0, 0, 1, 1,
                        0, 0, 1, 1,
                        pixels[0], pixels[1], 1, 1,
                        pixels[0], pixels[1],
                        0, 1, 0.5,
                    )
                )

        selected, rejected, excluded = calibracao._prepare_fit_records(records, "fine")
        self.assertEqual(len(selected), 24)
        self.assertFalse(rejected)
        self.assertFalse(excluded)
        fitted = calibracao._fit_robusto_sem_intercepto(selected, "fine")
        self.assertTrue(np.allclose(fitted["A"], expected))

    def test_return_pid_accepts_a_separate_speed_limit(self):
        parameters = inspect.signature(mount_control.move_axes_pid_2d).parameters
        self.assertIn("max_velocity_deg_s", parameters)

    def test_return_to_start_is_limited_and_verified(self):
        positions = iter([(1.0, 2.0), (0.0, 0.0), (0.0, 0.0)])
        movements = []

        def fake_move(mount, delta_az, delta_alt, max_velocity_deg_s=None):
            movements.append((mount, delta_az, delta_alt, max_velocity_deg_s))

        with (
            patch.object(
                tracker_seguranca,
                "read_altaz",
                side_effect=lambda: next(positions),
            ),
            patch.object(
                tracker_seguranca,
                "move_axes_pid_2d",
                side_effect=fake_move,
            ),
            patch.object(tracker_seguranca, "stop_axes_safely", return_value=True),
        ):
            result = tracker_seguranca.retornar_posicao_inicial(0.0, 0.0)

        self.assertTrue(result["success"])
        self.assertEqual(len(movements), 1)
        self.assertEqual(
            movements[0][3],
            tracker_seguranca.RETURN_MAX_RATE_DEG_S,
        )

    def test_signal_loss_never_triggers_blind_return(self):
        self.assertFalse(
            tracker_seguranca.motivo_permite_retorno(
                "sinal_perdido_por_tempo_excessivo"
            )
        )
        self.assertTrue(
            tracker_seguranca.motivo_permite_retorno("tempo_maximo_da_sessao")
        )

    def test_control_loop_has_one_matrix_and_no_operation_mode(self):
        parameters = inspect.signature(tracker_loop.executar_loop_controle).parameters
        self.assertEqual(list(parameters), ["state", "A_inv"])

    def test_ids_roi_rounding_recalculates_local_target(self):
        local_x, local_y = tracker_camera._target_local_in_actual_roi(
            2592,
            1944,
            1175.57,
            963.16,
            (252, 256, 1096, 866),
            "direct",
        )
        self.assertAlmostEqual(local_x, 79.57)
        self.assertAlmostEqual(local_y, 97.16)

    def test_runtime_target_selection_does_not_replace_calibration_target(self):
        selection = {
            "x_px": 320.0,
            "y_px": 240.0,
            "signature": {"version": 2, "primary": {"raw_peak": 1}},
        }
        with (
            patch.object(tracker_camera, "reset_camera_roi"),
            patch.object(
                tracker_camera,
                "capture_frame",
                return_value=np.zeros((480, 640), dtype=np.uint8),
            ),
            patch.object(
                detector_ilhas,
                "escolher_ilha_manualmente",
                return_value=selection,
            ),
            patch.object(detector_ilhas, "reset_focus_lock"),
            patch.object(detector_ilhas, "set_focus_mode"),
        ):
            target = tracker.escolher_referencia_tracker()

        self.assertEqual(target.source, "selecao_manual_tracker")
        self.assertEqual((target.x_px, target.y_px), (320.0, 240.0))

    def test_measurement_always_uses_locked_island_detector(self):
        with patch.object(
            detector_ilhas,
            "centro_massa",
            return_value=(12.5, 18.0, 99.0),
        ) as detector:
            result = tracker.medir_laser(np.zeros((32, 32), dtype=np.uint8))
        self.assertEqual(result, (12.5, 18.0))
        detector.assert_called_once()

    def test_temporal_estimator_averages_valid_frames_and_rejects_jump(self):
        estimator = TemporalFrameEstimator(
            window_seconds=2.0,
            warmup_seconds=0.1,
            min_frames=3,
            aperture_radius_px=16,
            max_input_jump_px=10.0,
        )
        for timestamp, x_px, y_px in (
            (0.0, 31.0, 32.0),
            (0.1, 33.0, 31.0),
            (0.2, 32.0, 33.0),
        ):
            accepted = estimator.add(
                timestamp,
                self._spot_frame(x_px, y_px),
                x_px,
                y_px,
            )
            self.assertTrue(accepted)

        estimate = estimator.estimate(0.2)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate["x_px"], 32.0, delta=0.6)
        self.assertAlmostEqual(estimate["y_px"], 32.0, delta=0.6)

        accepted = estimator.add(0.3, self._spot_frame(55, 55), 55, 55)
        self.assertFalse(accepted)
        self.assertEqual(estimator.frame_count, 3)
        self.assertEqual(estimator.rejected_inputs, 1)

    def test_temporal_estimator_window_is_time_based(self):
        estimator = TemporalFrameEstimator(
            window_seconds=1.0,
            warmup_seconds=0.0,
            min_frames=2,
            aperture_radius_px=12,
            max_input_jump_px=10.0,
        )
        for timestamp in (0.0, 0.5, 1.5):
            estimator.add(timestamp, self._spot_frame(32, 32), 32, 32)
        self.assertEqual(estimator.frame_count, 2)
        self.assertAlmostEqual(estimator.window_span_s, 1.0)

if __name__ == "__main__":
    unittest.main()
