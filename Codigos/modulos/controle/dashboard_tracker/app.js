(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = {
    sessionTime: $("session-time"), systemState: $("system-state"), stateLabel: $("state-label"),
    stateDetail: $("state-detail"), radialError: $("radial-error"), errorX: $("error-x"),
    errorY: $("error-y"), frameCount: $("frame-count"), windowTime: $("window-time"),
    exposure: $("exposure"), measurementRate: $("measurement-rate"), controlRate: $("control-rate"),
    roiSize: $("roi-size"), frame: $("live-frame"), overlay: $("beacon-overlay"),
    coordinates: $("coordinates"), trend: $("trend-canvas"), historyStatus: $("history-status"),
    actuation: $("actuation"), angularError: $("angular-error"), mountCommand: $("mount-command"),
    mountOffset: $("mount-offset"), quality: $("quality"), signalLost: $("signal-lost"),
    lastUpdate: $("last-update"),
  };
  const overlayCtx = ui.overlay.getContext("2d");
  const trendCtx = ui.trend.getContext("2d");
  const history = [];
  let latest = null;
  let lastServerTimestamp = 0;
  let lastMetricRender = 0;

  function fmt(value, digits = 2) {
    return value == null ? "—" : Number(value).toFixed(digits).replace(".", ",");
  }
  function signed(value, digits = 2) {
    if (value == null) return "—";
    return `${value >= 0 ? "+" : "−"}${Math.abs(Number(value)).toFixed(digits).replace(".", ",")}`;
  }
  function clock(seconds) {
    const value = Math.max(0, Math.round(seconds || 0));
    return `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor((value % 3600) / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }
  function resizeCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    return { width, height, ratio };
  }

  function statusInfo(s) {
    const label = (s.status || "AGUARDANDO").replaceAll(" - ", " · ");
    if (s.safety_stop_reason) return { label, detail: s.safety_stop_reason, css: "state-fault", system: "PARADA" };
    if (!s.has_signal || label.includes("ANOMALIA") || label.includes("REJEITADO")) {
      const targetPresent = Boolean(s.target_present);
      const detail = targetPresent
        ? `beacon presente · aparência instável ${fmt(s.optical_unstable_s, 1)} s`
        : s.optical_anomaly_reason || `beacon ausente ${fmt(s.signal_lost_s, 1)} s`;
      return { label, detail, css: targetPresent ? "state-active" : "state-fault", system: "MOUNT PARADO" };
    }
    if (s.hold_active) {
      const waiting = s.control_error_source === "aguardando_vies";
      const warming = !s.slow_bias_ready;
      const detail = waiting ? "confirmando deriva persistente" : warming ? "formando mediana lenta" : "dentro da zona de repouso";
      return { label, detail, css: waiting || warming ? "state-active" : "state-stable", system: waiting || warming ? "AVALIANDO" : "NORMAL" };
    }
    return { label, detail: s.trim_mode_active ? "micropulso fino" : "correção ativa", css: "state-active", system: "ATUANDO" };
  }

  function renderMetrics(s) {
    const state = statusInfo(s);
    ui.sessionTime.textContent = clock((s.elapsed_hours || 0) * 3600);
    ui.systemState.textContent = state.system;
    ui.systemState.className = state.css;
    ui.stateLabel.textContent = state.label;
    ui.stateLabel.className = state.css;
    ui.stateDetail.textContent = state.detail;
    ui.radialError.textContent = fmt(s.radial_error_px, 2);
    ui.errorX.textContent = `X ${signed(s.dx_px, 2)}`;
    ui.errorY.textContent = `Y↑ ${signed(s.dy_up_px, 2)}`;
    ui.frameCount.textContent = s.temporal_frame_count || 0;
    ui.windowTime.textContent = `${fmt(s.temporal_window_s, 2)} s acumulados`;
    ui.exposure.textContent = fmt(s.exposure_us, 0);
    ui.measurementRate.textContent = fmt(s.measurement_hz, 1);
    ui.controlRate.textContent = `controle ${fmt(s.control_loop_hz, 1)} Hz`;
    ui.roiSize.textContent = `ROI ${s.roi_width_px || "—"} × ${s.roi_height_px || "—"}`;
    ui.coordinates.textContent = s.has_signal ? `X ${signed(s.dx_px)} px · Y↑ ${signed(s.dy_up_px)} px` : "sem medição válida";
    ui.actuation.textContent = s.trim_mode_active ? "micropulso por viés lento" : s.control_error_source === "aguardando_vies" ? "confirmando deriva" : s.hold_active ? "repouso" : "correção rápida";
    ui.angularError.textContent = `Az ${signed(s.err_az_deg, 5)} · Alt ${signed(s.err_alt_deg, 5)} deg`;
    ui.mountCommand.textContent = `Az ${signed(s.cmd_az_deg_s, 4)} · Alt ${signed(s.cmd_alt_deg_s, 4)} °/s`;
    ui.mountOffset.textContent = `Az ${signed((s.offset_az_deg || 0) * 3600, 1)} · Alt ${signed((s.offset_alt_deg || 0) * 3600, 1)} arcsec`;
    const ratios = s.optical_intensity_ratio == null ? "" : ` · int ${fmt(s.optical_intensity_ratio)}× · área ${fmt(s.optical_area_ratio)}×`;
    const consensus = s.optical_quality_phase === "recuperando" ? ` · consenso ${fmt((s.optical_recovery_fraction || 0) * 100, 0)}%` : "";
    ui.quality.textContent = `${s.optical_quality_phase || "—"}${ratios}${consensus}`;
    ui.quality.className = s.optical_quality_phase === "normal"
      ? "state-stable"
      : s.target_present ? "state-active" : "state-fault";
    ui.signalLost.textContent = `${fmt(s.signal_lost_s, 1)} s`;
    ui.historyStatus.textContent = s.has_signal ? "medição aceita" : "medição suspensa";
    ui.historyStatus.className = s.has_signal ? "state-stable" : "state-fault";
    ui.lastUpdate.textContent = `atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
    drawOverlay(s);
  }

  function drawOverlay(s) {
    const { width, height, ratio } = resizeCanvas(ui.overlay);
    overlayCtx.clearRect(0, 0, width, height);
    if (!s.roi_width_px || !s.roi_height_px) return;
    const scale = Math.min(width / s.roi_width_px, height / s.roi_height_px);
    const offsetX = (width - s.roi_width_px * scale) / 2;
    const offsetY = (height - s.roi_height_px * scale) / 2;
    const targetX = offsetX + s.target_x_px * scale;
    const targetY = offsetY + s.target_y_px * scale;

    overlayCtx.strokeStyle = "rgba(103,183,195,.68)";
    overlayCtx.lineWidth = ratio;
    overlayCtx.beginPath(); overlayCtx.moveTo(offsetX, targetY); overlayCtx.lineTo(width - offsetX, targetY); overlayCtx.stroke();
    overlayCtx.beginPath(); overlayCtx.moveTo(targetX, offsetY); overlayCtx.lineTo(targetX, height - offsetY); overlayCtx.stroke();
    overlayCtx.fillStyle = "rgba(233,239,237,.9)";
    overlayCtx.beginPath(); overlayCtx.arc(targetX, targetY, 2.2 * ratio, 0, Math.PI * 2); overlayCtx.fill();

    if (s.has_signal && s.x_cm_px != null && s.y_cm_px != null) {
      overlayCtx.strokeStyle = s.hold_active ? "#65b488" : "#d3a04c";
      overlayCtx.lineWidth = 1.2 * ratio;
      overlayCtx.beginPath();
      overlayCtx.arc(offsetX + s.x_cm_px * scale, offsetY + s.y_cm_px * scale, 5 * ratio, 0, Math.PI * 2);
      overlayCtx.stroke();
    }
  }

  function drawTrend() {
    const { width, height, ratio } = resizeCanvas(ui.trend);
    const pad = { left: 36 * ratio, right: 12 * ratio, top: 12 * ratio, bottom: 24 * ratio };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const yMin = -4;
    const yMax = 4;
    const toY = (value) => pad.top + ((yMax - value) / (yMax - yMin)) * plotH;
    trendCtx.clearRect(0, 0, width, height);
    trendCtx.font = `${8 * ratio}px Consolas, monospace`;

    [-4, -2, 0, 2, 4].forEach((value) => {
      const y = toY(value);
      trendCtx.strokeStyle = "rgba(129,144,147,.16)";
      trendCtx.lineWidth = ratio;
      trendCtx.beginPath(); trendCtx.moveTo(pad.left, y); trendCtx.lineTo(width - pad.right, y); trendCtx.stroke();
      trendCtx.fillStyle = "rgba(129,144,147,.75)";
      trendCtx.fillText(String(value).replace("-", "−"), 7 * ratio, y + 3 * ratio);
    });
    const now = Date.now();
    const trace = (key) => {
      trendCtx.beginPath();
      let started = false;
      history.forEach((item) => {
        const value = item[key];
        if (value == null || value < yMin || value > yMax) { started = false; return; }
        const x = pad.left + Math.max(0, 1 - (now - item.t) / 120000) * plotW;
        const y = toY(value);
        if (!started) { trendCtx.moveTo(x, y); started = true; } else trendCtx.lineTo(x, y);
      });
    };
    const draw = (key, color, glow) => {
      trendCtx.lineCap = "round";
      trendCtx.lineJoin = "round";
      trace(key);
      trendCtx.strokeStyle = glow;
      trendCtx.lineWidth = 4.5 * ratio;
      trendCtx.stroke();
      trace(key);
      trendCtx.strokeStyle = color;
      trendCtx.lineWidth = 2 * ratio;
      trendCtx.stroke();
    };
    draw("x", "rgba(112,204,216,.98)", "rgba(105,183,195,.16)");
    draw("y", "rgba(225,173,83,.98)", "rgba(211,160,76,.15)");
    trendCtx.fillStyle = "rgba(129,144,147,.7)";
    trendCtx.fillText("−120 s", pad.left, height - 6 * ratio);
    trendCtx.fillText("agora", width - pad.right - 30 * ratio, height - 6 * ratio);
  }

  async function poll() {
    try {
      const response = await fetch(`/api/state?t=${Date.now()}`, { cache: "no-store" });
      const state = await response.json();
      if (!state.connected) return;
      latest = state;
      if (state.updated_unix_s !== lastServerTimestamp) {
        lastServerTimestamp = state.updated_unix_s;
        history.push({ t: Date.now(), x: state.dx_px, y: state.dy_up_px });
        const cutoff = Date.now() - 120000;
        while (history.length && history[0].t < cutoff) history.shift();
        drawTrend();
      }
      const now = Date.now();
      if (now - lastMetricRender >= 1000) {
        renderMetrics(state);
        lastMetricRender = now;
      }
    } catch (_error) {
      ui.systemState.textContent = "PAINEL DESCONECTADO";
      ui.systemState.className = "state-fault";
    }
  }

  function refreshFrame() {
    const image = new Image();
    image.onload = () => { ui.frame.src = image.src; if (latest) drawOverlay(latest); };
    image.src = `/api/frame.jpg?t=${Date.now()}`;
  }

  setInterval(poll, 200);
  setInterval(refreshFrame, 1000);
  window.addEventListener("resize", () => { if (latest) drawOverlay(latest); drawTrend(); });
  poll(); refreshFrame(); drawTrend();
})();
