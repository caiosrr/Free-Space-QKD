import csv
import inspect
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
from modulos.controle.tracker_estado import TrackerState
from modulos.controle.tracker_controle import FinePulseAxis
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
    ):
        return {
            "raw_total": raw_total,
            "area": area,
            "bbox_w": bbox_w,
            "bbox_h": bbox_h,
            "compactness": compactness,
            "similarity_primary": similarity,
        }

    def test_hold_zone_uses_three_frame_hysteresis(self):
        self.assertEqual(tracker_loop.HOLD_ENTER_RADIUS_PX, 1.0)
        self.assertEqual(tracker_loop.HOLD_EXIT_RADIUS_PX, 2.0)
        active, count = tracker_loop.atualizar_zona_de_reposo(False, 0, 0.9)
        self.assertTrue(active)
        active, count = tracker_loop.atualizar_zona_de_reposo(active, count, 2.1)
        self.assertTrue(active)
        active, count = tracker_loop.atualizar_zona_de_reposo(active, count, 2.1)
        self.assertTrue(active)
        active, count = tracker_loop.atualizar_zona_de_reposo(active, count, 2.1)
        self.assertFalse(active)
        self.assertEqual(count, 0)

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

    def test_optical_gate_rejects_expanded_dim_spot_then_waits_for_recovery(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
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
        self.assertEqual(rejected.phase, "anomalia")
        self.assertIn("intensidade_baixa", rejected.reasons)
        self.assertIn("area_expandida", rejected.reasons)

        self.assertFalse(gate.observe(0.3, normal).accepted)
        self.assertFalse(gate.observe(0.45, normal).accepted)
        recovered = gate.observe(0.51, normal)
        self.assertTrue(recovered.accepted)
        self.assertEqual(recovered.event, "anomalia_optica_recuperada")

    def test_optical_gate_waits_after_plain_signal_loss(self):
        normal = self._optical_candidate()
        gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
            min_baseline_frames=2,
        )
        gate.observe(0.0, normal)
        self.assertTrue(gate.observe(0.11, normal).accepted)
        self.assertFalse(gate.observe(0.2, None).accepted)
        self.assertFalse(gate.observe(0.3, normal).accepted)
        self.assertTrue(gate.observe(0.51, normal).accepted)

    def test_optical_gate_rejects_sudden_brightening_and_shrinking(self):
        normal = self._optical_candidate()
        bright_gate = tracker_qualidade.OpticalQualityGate(
            normal,
            initial_stable_s=0.1,
            recovery_stable_s=0.2,
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

    def test_shared_state_preserves_first_safety_reason(self):
        state = TrackerState()
        self.assertTrue(state.request_stop("primeiro_motivo"))
        self.assertFalse(state.request_stop("segundo_motivo"))
        self.assertEqual(state.snapshot()["safety_stop_reason"], "primeiro_motivo")

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
