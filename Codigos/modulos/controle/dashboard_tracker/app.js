(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = {
    frame: $("live-frame"), overlay: $("beacon-overlay"), trend: $("trend-canvas"),
    sessionTime: $("session-time"), systemState: $("system-state"), stateFlag: $("state-flag"),
    stateLabel: $("state-label"), roiSize: $("roi-size"), windowTime: $("window-time"),
    frameCount: $("frame-count"), cameraRate: $("camera-rate"), coordinates: $("coordinates"),
    windowDetail: $("window-detail"), calibration: $("calibration"), radialError: $("radial-error"),
    errorMarker: $("error-marker"), holdEnter: $("hold-enter"), holdExit: $("hold-exit"),
    errorXBar: $("error-x-bar"), errorYBar: $("error-y-bar"), errorXValue: $("error-x-value"),
    errorYValue: $("error-y-value"), angularError: $("angular-error"), actuation: $("actuation"),
    mountCommand: $("mount-command"), mountOffset: $("mount-offset"), qualityLabel: $("quality-label"),
    intensityMeter: $("intensity-meter"), intensityValue: $("intensity-value"), areaMeter: $("area-meter"),
    areaValue: $("area-value"), validMeter: $("valid-meter"), validValue: $("valid-value"),
    eventTime: $("event-time"), eventText: $("event-text"), controlRate: $("control-rate"),
    exposure: $("exposure"), signalLost: $("signal-lost"),
  };
  const overlayCtx = ui.overlay.getContext("2d");
  const trendCtx = ui.trend.getContext("2d");
  const history = [];
  let acceptedSamples = 0;
  let totalSamples = 0;
  let lastStatus = "";
  let latest = null;

  function fmt(value, digits = 2) {
    return value == null ? "—" : Number(value).toFixed(digits).replace(".", ",");
  }
  function signed(value, digits = 2) {
    if (value == null) return "—";
    return `${value >= 0 ? "+" : "−"}${Math.abs(Number(value)).toFixed(digits).replace(".", ",")}`;
  }
  function duration(seconds) {
    const value = Math.max(0, Math.round(seconds || 0));
    return `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor((value % 3600) / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }
  function setSignedBar(element, value, range = 3) {
    const amount = Math.min(1, Math.abs(value || 0) / range) * 50;
    element.style.left = value < 0 ? `${50 - amount}%` : "50%";
    element.style.width = `${amount}%`;
    element.style.background = Math.abs(value || 0) >= 2 ? "var(--amber)" : "var(--cyan)";
  }
  function meter(element, ratio, low = 0, high = 2) {
    const width = ratio == null ? 0 : Math.max(0, Math.min(100, ((ratio - low) / (high - low)) * 100));
    element.style.width = `${width}%`;
  }
  function resizeCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    return { width, height, ratio };
  }

  function drawOverlay(s) {
    const { width, height, ratio } = resizeCanvas(ui.overlay);
    overlayCtx.clearRect(0, 0, width, height);
    if (!s.roi_width_px || !s.roi_height_px) return;
    const scale = Math.min(width / s.roi_width_px, height / s.roi_height_px);
    const offsetX = (width - s.roi_width_px * scale) / 2;
    const offsetY = (height - s.roi_height_px * scale) / 2;
    const tx = offsetX + s.target_x_px * scale;
    const ty = offsetY + s.target_y_px * scale;
    [s.hold_enter_radius_px, s.hold_exit_radius_px].forEach((radius, index) => {
      overlayCtx.beginPath();
      overlayCtx.arc(tx, ty, radius * scale, 0, Math.PI * 2);
      overlayCtx.setLineDash(index ? [5 * ratio, 4 * ratio] : []);
      overlayCtx.strokeStyle = index ? "rgba(213,154,74,.75)" : "rgba(104,173,130,.95)";
      overlayCtx.lineWidth = ratio;
      overlayCtx.stroke();
    });
    overlayCtx.setLineDash([]);
    overlayCtx.strokeStyle = "rgba(235,240,237,.7)";
    overlayCtx.beginPath(); overlayCtx.moveTo(tx - 13 * ratio, ty); overlayCtx.lineTo(tx + 13 * ratio, ty); overlayCtx.stroke();
    overlayCtx.beginPath(); overlayCtx.moveTo(tx, ty - 13 * ratio); overlayCtx.lineTo(tx, ty + 13 * ratio); overlayCtx.stroke();
    if (s.has_signal && s.x_cm_px != null && s.y_cm_px != null) {
      overlayCtx.fillStyle = s.hold_active ? "#68ad82" : "#d59a4a";
      overlayCtx.beginPath();
      overlayCtx.arc(offsetX + s.x_cm_px * scale, offsetY + s.y_cm_px * scale, 4 * ratio, 0, Math.PI * 2);
      overlayCtx.fill();
    }
  }

  function drawTrend() {
    const { width, height, ratio } = resizeCanvas(ui.trend);
    const pad = { left: 32 * ratio, right: 10 * ratio, top: 9 * ratio, bottom: 20 * ratio };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    trendCtx.clearRect(0, 0, width, height);
    trendCtx.font = `${8 * ratio}px Consolas, monospace`;
    [-3, -2, -1, 0, 1, 2, 3].forEach((value) => {
      const y = pad.top + ((3 - value) / 6) * plotH;
      trendCtx.strokeStyle = "rgba(143,155,151,.15)";
      trendCtx.beginPath(); trendCtx.moveTo(pad.left, y); trendCtx.lineTo(width - pad.right, y); trendCtx.stroke();
      trendCtx.fillStyle = "rgba(143,155,151,.7)"; trendCtx.fillText(String(value).replace("-", "−"), 4 * ratio, y + 3 * ratio);
    });
    const draw = (key, color, lineWidth) => {
      trendCtx.beginPath(); let started = false;
      history.forEach((item, index) => {
        const value = item[key];
        if (value == null || Math.abs(value) > 3) { started = false; return; }
        const x = pad.left + (history.length < 2 ? 0 : index / (history.length - 1)) * plotW;
        const y = pad.top + ((3 - value) / 6) * plotH;
        if (!started) { trendCtx.moveTo(x, y); started = true; } else trendCtx.lineTo(x, y);
      });
      trendCtx.strokeStyle = color; trendCtx.lineWidth = lineWidth * ratio; trendCtx.stroke();
    };
    draw("x", "rgba(115,185,199,.9)", 1);
    draw("y", "rgba(110,145,200,.86)", 1);
    draw("r", "rgba(213,154,74,.95)", 1.4);
    trendCtx.fillStyle = "rgba(143,155,151,.7)";
    trendCtx.fillText("−120 s", pad.left, height - 4 * ratio);
    trendCtx.fillText("agora", width - pad.right - 28 * ratio, height - 4 * ratio);
  }

  function updateStatus(s) {
    const status = s.status || "AGUARDANDO";
    const fault = s.safety_stop_reason || !s.has_signal || status.includes("ANOMALIA") || status.includes("REJEITADO");
    const tracking = !fault && !s.hold_active;
    ui.stateFlag.className = `state-flag ${fault ? "lost" : tracking ? "tracking" : "stable"}`;
    ui.stateLabel.textContent = status.replaceAll(" - ", " · ");
    ui.systemState.textContent = s.safety_stop_reason ? "parada" : fault ? "mount parado" : tracking ? "atuando" : "normal";
    if (status !== lastStatus) {
      ui.eventTime.textContent = duration((s.elapsed_hours || 0) * 3600);
      ui.eventText.textContent = s.optical_anomaly_reason || status.toLowerCase();
      lastStatus = status;
    }
  }

  function render(s) {
    latest = s;
    totalSamples += 1;
    if (s.has_signal) acceptedSamples += 1;
    const x = s.dx_px;
    const y = s.dy_up_px;
    const radial = s.radial_error_px;
    ui.sessionTime.textContent = duration((s.elapsed_hours || 0) * 3600);
    ui.roiSize.textContent = `ROI ${s.roi_width_px || "—"} × ${s.roi_height_px || "—"}`;
    ui.windowTime.textContent = `${fmt(s.temporal_window_s, 2)} s`;
    ui.frameCount.textContent = `${s.temporal_frame_count || 0} frames`;
    ui.cameraRate.textContent = `${fmt(s.measurement_hz, 1)} Hz`;
    ui.coordinates.textContent = s.has_signal ? `X ${signed(x)} · Y ${signed(y)} px` : "sem medição válida";
    ui.windowDetail.textContent = `${s.temporal_frame_count || 0} frames em ${fmt(s.temporal_window_s, 2)} s`;
    ui.calibration.textContent = `${s.calibration_name || "contínua"} · IDS`;
    ui.radialError.textContent = fmt(radial, 2);
    ui.errorMarker.style.left = `${Math.min(100, (radial || 0) / 3 * 100)}%`;
    ui.holdEnter.textContent = `repouso ${fmt(s.hold_enter_radius_px, 0)}`;
    ui.holdExit.textContent = `retoma ${fmt(s.hold_exit_radius_px, 0)}`;
    setSignedBar(ui.errorXBar, x); setSignedBar(ui.errorYBar, y);
    ui.errorXValue.textContent = `${signed(x)} px`; ui.errorYValue.textContent = `${signed(y)} px`;
    ui.angularError.textContent = `Az ${signed(s.err_az_deg, 5)} · Alt ${signed(s.err_alt_deg, 5)} deg`;
    ui.actuation.textContent = s.trim_mode_active ? "correção fina ativa" : s.hold_active ? "repouso" : "controle normal";
    ui.mountCommand.textContent = `Az ${signed(s.cmd_az_deg_s, 4)} · Alt ${signed(s.cmd_alt_deg_s, 4)} °/s`;
    ui.mountOffset.textContent = `Az ${signed((s.offset_az_deg || 0) * 3600, 1)} · Alt ${signed((s.offset_alt_deg || 0) * 3600, 1)} arcsec`;
    ui.qualityLabel.textContent = s.optical_quality_phase || "—";
    ui.qualityLabel.style.color = s.optical_quality_phase === "normal" ? "var(--green)" : "var(--red)";
    meter(ui.intensityMeter, s.optical_intensity_ratio); meter(ui.areaMeter, s.optical_area_ratio);
    ui.intensityValue.textContent = s.optical_intensity_ratio == null ? "—" : `${fmt(s.optical_intensity_ratio)}×`;
    ui.areaValue.textContent = s.optical_area_ratio == null ? "—" : `${fmt(s.optical_area_ratio)}×`;
    const availability = 100 * acceptedSamples / Math.max(1, totalSamples);
    ui.validMeter.style.width = `${availability}%`; ui.validValue.textContent = `${fmt(availability, 1)}%`;
    ui.controlRate.textContent = `controle ${fmt(s.control_loop_hz, 1)} Hz`;
    ui.exposure.textContent = `${fmt(s.exposure_us, 0)} µs`;
    ui.signalLost.textContent = `${fmt(s.signal_lost_s, 1)} s`;
    updateStatus(s); drawOverlay(s);
  }

  async function poll() {
    try {
      const response = await fetch(`/api/state?t=${Date.now()}`, { cache: "no-store" });
      const state = await response.json();
      if (state.connected) {
        render(state);
        history.push({ t: Date.now(), x: state.dx_px, y: state.dy_up_px, r: state.radial_error_px });
        const cutoff = Date.now() - 120000;
        while (history.length && history[0].t < cutoff) history.shift();
        drawTrend();
      }
    } catch (_error) {
      ui.systemState.textContent = "painel desconectado";
      ui.stateFlag.className = "state-flag lost";
    }
  }
  function refreshFrame() {
    const image = new Image();
    image.onload = () => { ui.frame.src = image.src; if (latest) drawOverlay(latest); };
    image.src = `/api/frame.jpg?t=${Date.now()}`;
  }

  setInterval(poll, 200);
  setInterval(refreshFrame, 250);
  window.addEventListener("resize", () => { if (latest) drawOverlay(latest); drawTrend(); });
  poll(); refreshFrame(); drawTrend();
})();
