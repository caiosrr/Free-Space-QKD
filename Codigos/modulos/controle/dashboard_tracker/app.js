/* Painel do tracker — somente leitura.
   Consome /api/state e /api/frame.jpg. Nao mede, nao calibra e nao comanda.

   Cada grandeza aparece UMA vez, na forma que serve melhor:
     erro radial  -> numero grande + escala com as marcas de repouso/retomada
     X e Y        -> numero exato + historico no grafico (sem barras repetindo)
     visor        -> aneis de repouso e retomada em torno do alvo
*/

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = {};
  [
    "link-distance", "session-time", "system-state",
    "viewer-meta", "live-frame", "beacon-overlay", "camera-label",
    "measurement-rate", "coordinates", "sigma", "inset-caption",
    "radial-error", "radial-metric",
    "scale", "tick-rest", "tick-wake", "scale-bar",
    "error-x", "error-y", "sigma-inline", "state-detail",
    "actuation", "mount-command", "angular-error", "mount-offset",
    "quality", "exposure", "calibration",
    "trend-canvas", "last-update",
  ].forEach((id) => { ui[id] = $(id); });

  const overlayCtx = ui["beacon-overlay"].getContext("2d");
  const trendCtx = ui["trend-canvas"].getContext("2d");

  const HISTORY_MS = 120000;
  const TRAIL_MAX = 80;
  const history = [];
  const trail = [];

  let latest = null;
  let lastServerTimestamp = 0;
  let lastMetricRender = 0;

  const CSS = getComputedStyle(document.documentElement);
  const tone = (name) => CSS.getPropertyValue(name).trim();

  const fmt = (v, d = 2) =>
    v == null || !isFinite(v) ? "—" : Number(v).toFixed(d).replace(".", ",");
  const signed = (v, d = 2) =>
    v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d).replace(".", ",")}`;
  const clock = (seconds) => {
    const s = Math.max(0, Math.round(seconds || 0));
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
  };
  // kind nulo apenas limpa a cor: um valor sem leitura semantica (CNR ausente,
  // por exemplo) nao deve herdar a cor do estado anterior.
  const setTone = (el, kind) => {
    const base = el.className.split(" ").filter((c) => c && !c.startsWith("is-")).join(" ");
    el.className = kind ? `${base} is-${kind}` : base;
  };

  function resizeCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { width, height, ratio };
  }

  // ── interpretacao do estado ───────────────────────────────────────
  function leitura(s) {
    if (s.safety_stop_reason) {
      return { estado: "parada de segurança", kind: "fault", detalhe: s.safety_stop_reason };
    }
    if (!s.target_present) {
      return {
        estado: "sem sinal",
        kind: "fault",
        detalhe: `beacon ausente há ${fmt(s.signal_lost_s, 1)} s · mount parado, sem busca`,
      };
    }
    if (!s.has_signal) {
      const anomalia = (s.optical_anomaly_reason || "").replaceAll(",", ", ");
      return {
        estado: "aparência instável",
        kind: "fault",
        detalhe: anomalia
          ? `${anomalia} · mount parado até a luz normalizar`
          : `reconstruindo a média temporal há ${fmt(s.optical_unstable_s, 1)} s`,
      };
    }
    if (s.correction_phase === "parando" || s.correction_phase === "acomodacao") {
      return {
        estado: "acomodando",
        kind: "act",
        detalhe: "aguardando uma média inteiramente posterior ao movimento",
      };
    }
    if (!s.hold_active) {
      return {
        estado: "corrigindo",
        kind: "act",
        detalhe: s.trim_mode_active
          ? "micropulsos finos, com espera pela nova média entre eles"
          : "correção sobre erro persistente e coerente em direção",
      };
    }
    if (s.control_error_source === "aguardando_vies") {
      return { estado: "confirmando", kind: "act", detalhe: "deriva possível, aguardando persistência antes de atuar" };
    }
    if (!s.slow_bias_ready) {
      return { estado: "aquecendo", kind: "act", detalhe: "formando a mediana lenta de referência" };
    }
    return { estado: "em repouso", kind: "rest", detalhe: "dentro da zona de repouso · mount parado por decisão" };
  }

  function textoAtuacao(s) {
    if (s.correction_phase === "parando" || s.correction_phase === "acomodacao") return "acomodação pós-movimento";
    if (s.trim_mode_active) return "micropulso fino";
    if (!s.hold_active) return "correção ativa";
    if (s.control_error_source === "aguardando_vies") return "confirmando deriva";
    const n = s.correction_cycles || 0;
    return n ? `repouso · ${n} correç${n > 1 ? "ões" : "ão"} na sessão` : "repouso";
  }

  // ── render ────────────────────────────────────────────────────────
  function render(s) {
    const r = leitura(s);

    ui["session-time"].textContent = clock((s.elapsed_hours || 0) * 3600);
    ui["system-state"].textContent = r.estado;
    setTone(ui["system-state"], r.kind);
    ui["state-detail"].textContent = r.detalhe;
    ui["link-distance"].textContent = s.link_label || "";
    ui["camera-label"].textContent = s.camera_label || "";

    ui["radial-error"].textContent = fmt(s.radial_error_px, 2);
    setTone(ui["radial-error"], r.kind === "fault" ? "fault" : s.hold_active ? "rest" : "act");
    ui["radial-error"].classList.add("num");
    ui["radial-metric"].textContent =
      s.radial_error_px != null && s.cm_per_px
        ? `${fmt(s.radial_error_px * s.cm_per_px, 1)} cm no alvo`
        : "";
    desenharEscala(s);

    ui["error-x"].textContent = `${signed(s.dx_px, 2)} px`;
    ui["error-y"].textContent = `${signed(s.dy_up_px, 2)} px`;
    ui["sigma-inline"].textContent = s.sigma_centroide_px ? `${fmt(s.sigma_centroide_px, 3)} px` : "—";

    ui["viewer-meta"].textContent =
      `ROI ${s.roi_width_px || "—"}×${s.roi_height_px || "—"} · ${fmt(s.temporal_window_s, 2)} s · ${s.temporal_frame_count || 0} frames`;
    ui["measurement-rate"].textContent =
      `${fmt(s.measurement_hz, 1)} Hz medição · ${fmt(s.control_loop_hz, 1)} Hz controle`;
    ui["coordinates"].textContent = s.has_signal
      ? `X ${signed(s.dx_px)} · Y↑ ${signed(s.dy_up_px)} px`
      : "sem medição válida";
    ui["sigma"].textContent = s.sigma_centroide_px ? `σ ${fmt(s.sigma_centroide_px, 3)} px` : "—";

    ui["actuation"].textContent = textoAtuacao(s);
    ui["mount-command"].textContent =
      Math.abs(s.cmd_az_deg_s || 0) + Math.abs(s.cmd_alt_deg_s || 0) > 1e-9
        ? `Az ${signed(s.cmd_az_deg_s, 4)} · Alt ${signed(s.cmd_alt_deg_s, 4)} °/s`
        : "parado";
    ui["angular-error"].textContent = `Az ${signed(s.err_az_deg, 5)} · Alt ${signed(s.err_alt_deg, 5)}°`;
    ui["mount-offset"].textContent =
      `Az ${signed((s.offset_az_deg || 0) * 3600, 1)}″ · Alt ${signed((s.offset_alt_deg || 0) * 3600, 1)}″`;

    const fase = s.optical_quality_phase || "—";
    const razoes = s.optical_intensity_ratio == null
      ? ""
      : ` · int ${fmt(s.optical_intensity_ratio)}× · área ${fmt(s.optical_area_ratio)}×`;
    ui["quality"].textContent = fase + razoes;
    setTone(ui["quality"], fase === "normal" ? "rest" : s.target_present ? "act" : "fault");

    // Exposicao e CNR juntos: sozinho, nenhum dos dois diz se o beacon esta
    // bem exposto. 740 us pode ser folgado ou apertado dependendo do contraste
    // que ele entrega, e e o par que o operador precisa ler de relance.
    const cnr = Number.isFinite(s.auto_exposure_cnr) ? s.auto_exposure_cnr : null;
    ui["exposure"].textContent =
      `${fmt(s.exposure_us, 0)} µs` + (cnr === null ? "" : ` · CNR ${cnr.toFixed(1)}`);
    setTone(ui["exposure"], cnr === null ? null : cnr < 8 ? "fault" : "rest");
    ui["calibration"].textContent = s.calibration_name || "contínua";
    ui["last-update"].textContent = `atualizado ${new Date().toLocaleTimeString("pt-BR")}`;

    desenharVisor(s);
  }

  // barra crescente sobre trilho, com as marcas de repouso e retomada fixas
  function desenharEscala(s) {
    const rest = s.hold_enter_radius_px || 1;
    const wake = s.hold_exit_radius_px || 2;
    const max = Math.max(wake * 1.75, 3.5);
    const pos = (v) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;

    ui["tick-rest"].style.left = pos(rest);
    ui["tick-rest"].firstElementChild.textContent = `${fmt(rest, 1)} repouso`;
    ui["tick-wake"].style.left = pos(wake);
    ui["tick-wake"].firstElementChild.textContent = `${fmt(wake, 1)} retoma`;

    const v = s.radial_error_px;
    const semLeitura = v == null || !isFinite(v);
    ui["scale-bar"].style.width = semLeitura ? "0%" : pos(v);
    ui["scale-bar"].style.backgroundColor =
      semLeitura ? tone("--ink-3") : v <= rest ? tone("--rest") : v <= wake ? tone("--sodium") : tone("--fault");
  }

  // ── visor ─────────────────────────────────────────────────────────
  function desenharVisor(s) {
    const { width, height, ratio } = resizeCanvas(ui["beacon-overlay"]);
    overlayCtx.clearRect(0, 0, width, height);
    if (!s.roi_width_px || !s.roi_height_px) return;

    const escala = Math.min(width / s.roi_width_px, height / s.roi_height_px);
    const ox = (width - s.roi_width_px * escala) / 2;
    const oy = (height - s.roi_height_px * escala) / 2;
    const tx = ox + s.target_x_px * escala;
    const ty = oy + s.target_y_px * escala;

    // aneis de repouso e retomada: o que da significado imediato ao numero
    overlayCtx.setLineDash([4 * ratio, 5 * ratio]);
    overlayCtx.lineWidth = 1.1 * ratio;
    [[s.hold_enter_radius_px, tone("--rest")], [s.hold_exit_radius_px, tone("--sodium")]]
      .forEach(([raio, cor]) => {
        if (!raio) return;
        overlayCtx.strokeStyle = cor;
        overlayCtx.globalAlpha = 0.5;
        overlayCtx.beginPath();
        overlayCtx.arc(tx, ty, Math.max(raio * escala, 3 * ratio), 0, Math.PI * 2);
        overlayCtx.stroke();
      });
    overlayCtx.setLineDash([]);
    overlayCtx.globalAlpha = 1;

    // Eixos X e Y do alvo, com VAO no centro. A cruz continua anterior passava
    // exatamente por cima do ponto que interessa: o centro de massa fica a
    // fracoes de pixel do alvo na maior parte do tempo, entao o traco cobria a
    // propria medida. Com o vao, o miolo fica limpo e os bracos servem so de
    // referencia de direcao, que e o que eles precisam fazer.
    overlayCtx.strokeStyle = "rgba(250,243,230,.45)";
    overlayCtx.lineWidth = ratio;
    const vao = 13 * ratio;
    const braco = 16 * ratio;
    overlayCtx.beginPath();
    overlayCtx.moveTo(tx - vao - braco, ty); overlayCtx.lineTo(tx - vao, ty);
    overlayCtx.moveTo(tx + vao, ty); overlayCtx.lineTo(tx + vao + braco, ty);
    overlayCtx.moveTo(tx, ty - vao - braco); overlayCtx.lineTo(tx, ty - vao);
    overlayCtx.moveTo(tx, ty + vao); overlayCtx.lineTo(tx, ty + vao + braco);
    overlayCtx.stroke();

    // rastro recente
    if (trail.length > 1) {
      overlayCtx.strokeStyle = tone("--sodium");
      overlayCtx.lineWidth = 1.1 * ratio;
      for (let i = 1; i < trail.length; i += 1) {
        overlayCtx.globalAlpha = (i / trail.length) * 0.45;
        overlayCtx.beginPath();
        overlayCtx.moveTo(ox + trail[i - 1].x * escala, oy + trail[i - 1].y * escala);
        overlayCtx.lineTo(ox + trail[i].x * escala, oy + trail[i].y * escala);
        overlayCtx.stroke();
      }
      overlayCtx.globalAlpha = 1;
    }

    if (s.has_signal && s.x_cm_px != null && s.y_cm_px != null) {
      const cx = ox + s.x_cm_px * escala;
      const cy = oy + s.y_cm_px * escala;
      const cor = s.hold_active ? tone("--rest") : tone("--sodium");
      overlayCtx.strokeStyle = cor;
      overlayCtx.lineWidth = 1.4 * ratio;
      overlayCtx.beginPath();
      overlayCtx.arc(cx, cy, 7 * ratio, 0, Math.PI * 2);
      overlayCtx.stroke();
      overlayCtx.fillStyle = cor;
      overlayCtx.beginPath();
      overlayCtx.arc(cx, cy, 1.8 * ratio, 0, Math.PI * 2);
      overlayCtx.fill();
    }

    desenharLupa(s, width, height, ratio, escala);
  }

  // ── lupa ──────────────────────────────────────────────────────────
  //
  // A ROI tem 256 px e o visor uns 500 px de tela: 1 px de sensor vira ~2 px de
  // tela. Com o erro tipico em torno de 1 px, o centro de massa se mexe 2 px na
  // tela e o anel de repouso (1 px) fica MENOR que o proprio marcador. Nessa
  // escala nao ha desenho que resolva: a grandeza que interessa e 1/200 da
  // largura do quadro. A lupa mostra um recorte de LADO_PX px do sensor em
  // torno do alvo, onde 1 px de erro vira dezenas de px de tela e os aneis
  // voltam a significar alguma coisa. A imagem grande continua servindo para o
  // que ela e boa: a forma da ilha e o que mais entrou no campo.
  const LUPA_LADO_PX = 16;   // lado do recorte, em pixels de sensor
  const LUPA_TELA = 172;     // lado da lupa, em px de CSS

  function desenharLupa(s, width, height, ratio, escala) {
    const img = ui["live-frame"];
    if (!img.naturalWidth || !s.roi_width_px) return;

    const lado = LUPA_TELA * ratio;
    const margem = 10 * ratio;
    const x0 = width - lado - margem;
    const y0 = height - lado - margem;
    // Ampliacao dita em relacao a imagem grande, que e com o que o olho
    // compara. Ambas em px de dispositivo por px de sensor, entao o fator de
    // densidade de tela se cancela.
    const amp = (lado / LUPA_LADO_PX) / escala;
    ui["inset-caption"].textContent =
      `lupa ${amp.toFixed(0)}x · recorte de ${LUPA_LADO_PX} px do sensor`;

    // recorte da imagem ao vivo, centrado no alvo
    const meio = LUPA_LADO_PX / 2;
    const sx = s.target_x_px - meio;
    const sy = s.target_y_px - meio;
    overlayCtx.save();
    overlayCtx.beginPath();
    overlayCtx.rect(x0, y0, lado, lado);
    overlayCtx.clip();
    overlayCtx.fillStyle = tone("--well");
    overlayCtx.fillRect(x0, y0, lado, lado);
    overlayCtx.imageSmoothingEnabled = false;
    try {
      overlayCtx.drawImage(img, sx, sy, LUPA_LADO_PX, LUPA_LADO_PX, x0, y0, lado, lado);
    } catch (e) {
      // imagem ainda nao decodificada; o proximo quadro desenha
    }

    // do sensor para dentro da lupa
    const k = lado / LUPA_LADO_PX;
    const px = (v) => x0 + (v - sx) * k;
    const py = (v) => y0 + (v - sy) * k;
    const ax = px(s.target_x_px);
    const ay = py(s.target_y_px);

    [[s.hold_enter_radius_px, tone("--rest")], [s.hold_exit_radius_px, tone("--sodium")]]
      .forEach(([raio, cor]) => {
        if (!raio) return;
        overlayCtx.strokeStyle = cor;
        overlayCtx.globalAlpha = 0.65;
        overlayCtx.lineWidth = 1.1 * ratio;
        overlayCtx.setLineDash([4 * ratio, 5 * ratio]);
        overlayCtx.beginPath();
        overlayCtx.arc(ax, ay, raio * k, 0, Math.PI * 2);
        overlayCtx.stroke();
      });
    overlayCtx.setLineDash([]);
    overlayCtx.globalAlpha = 1;

    // eixos com vao, como no visor grande
    overlayCtx.strokeStyle = "rgba(250,243,230,.45)";
    overlayCtx.lineWidth = ratio;
    const vao = 9 * ratio;
    const bra = 13 * ratio;
    overlayCtx.beginPath();
    overlayCtx.moveTo(ax - vao - bra, ay); overlayCtx.lineTo(ax - vao, ay);
    overlayCtx.moveTo(ax + vao, ay); overlayCtx.lineTo(ax + vao + bra, ay);
    overlayCtx.moveTo(ax, ay - vao - bra); overlayCtx.lineTo(ax, ay - vao);
    overlayCtx.moveTo(ax, ay + vao); overlayCtx.lineTo(ax, ay + vao + bra);
    overlayCtx.stroke();

    if (s.has_signal && s.x_cm_px != null && s.y_cm_px != null) {
      // Fora do recorte o marcador encosta na borda, em vez de sumir: assim a
      // lupa nunca fica vazia sem dizer por que.
      const bx = Math.max(x0 + 2 * ratio, Math.min(x0 + lado - 2 * ratio, px(s.x_cm_px)));
      const by = Math.max(y0 + 2 * ratio, Math.min(y0 + lado - 2 * ratio, py(s.y_cm_px)));
      const dentro = bx === px(s.x_cm_px) && by === py(s.y_cm_px);
      const cor = s.hold_active ? tone("--rest") : tone("--sodium");
      overlayCtx.globalAlpha = dentro ? 1 : 0.5;
      overlayCtx.strokeStyle = cor;
      overlayCtx.lineWidth = 1.6 * ratio;
      overlayCtx.beginPath();
      overlayCtx.arc(bx, by, 9 * ratio, 0, Math.PI * 2);
      overlayCtx.stroke();
      overlayCtx.fillStyle = cor;
      overlayCtx.beginPath();
      overlayCtx.arc(bx, by, 2.4 * ratio, 0, Math.PI * 2);
      overlayCtx.fill();
      overlayCtx.globalAlpha = 1;
    }
    overlayCtx.restore();

    overlayCtx.strokeStyle = tone("--rule");
    overlayCtx.lineWidth = ratio;
    overlayCtx.strokeRect(x0, y0, lado, lado);
  }

  // ── historico ─────────────────────────────────────────────────────
  function desenharGrafico() {
    const { width, height, ratio } = resizeCanvas(ui["trend-canvas"]);
    const pad = { left: 30 * ratio, right: 6 * ratio, top: 8 * ratio, bottom: 18 * ratio };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const yMin = -4;
    const yMax = 4;
    const toY = (v) => pad.top + ((yMax - v) / (yMax - yMin)) * plotH;

    trendCtx.clearRect(0, 0, width, height);
    trendCtx.font = `${10 * ratio}px Consolas, monospace`;

    // faixa de repouso: mesma referencia dos aneis do visor
    const rest = latest && latest.hold_enter_radius_px;
    if (rest) {
      trendCtx.fillStyle = "rgba(147,177,122,.08)";
      trendCtx.fillRect(pad.left, toY(rest), plotW, toY(-rest) - toY(rest));
    }

    [-4, -2, 0, 2, 4].forEach((v) => {
      const y = toY(v);
      trendCtx.strokeStyle = v === 0 ? "rgba(168,151,124,.26)" : "rgba(168,151,124,.10)";
      trendCtx.lineWidth = ratio;
      trendCtx.beginPath(); trendCtx.moveTo(pad.left, y); trendCtx.lineTo(width - pad.right, y); trendCtx.stroke();
      trendCtx.fillStyle = "rgba(168,151,124,.65)";
      trendCtx.fillText(String(v).replace("-", "−"), 6 * ratio, y + 3.5 * ratio);
    });

    const agora = Date.now();
    const traco = (chave, cor) => {
      trendCtx.lineCap = "round";
      trendCtx.lineJoin = "round";
      trendCtx.strokeStyle = cor;
      trendCtx.lineWidth = 1.7 * ratio;
      trendCtx.beginPath();
      let iniciado = false;
      history.forEach((item) => {
        const valor = item[chave];
        if (valor == null || valor < yMin || valor > yMax) { iniciado = false; return; }
        const x = pad.left + Math.max(0, 1 - (agora - item.t) / HISTORY_MS) * plotW;
        const y = toY(valor);
        if (!iniciado) { trendCtx.moveTo(x, y); iniciado = true; } else trendCtx.lineTo(x, y);
      });
      trendCtx.stroke();
    };
    traco("x", tone("--sodium"));
    traco("y", tone("--rest"));

    trendCtx.fillStyle = "rgba(109,94,72,.9)";
    trendCtx.fillText("−120 s", pad.left, height - 4 * ratio);
    const fim = "agora";
    trendCtx.fillText(fim, width - pad.right - trendCtx.measureText(fim).width, height - 4 * ratio);
  }

  // ── ciclo ─────────────────────────────────────────────────────────
  async function poll() {
    try {
      const response = await fetch(`/api/state?t=${Date.now()}`, { cache: "no-store" });
      const state = await response.json();
      if (!state.connected) return;
      latest = state;

      if (state.updated_unix_s !== lastServerTimestamp) {
        lastServerTimestamp = state.updated_unix_s;
        history.push({ t: Date.now(), x: state.dx_px, y: state.dy_up_px });
        const corte = Date.now() - HISTORY_MS;
        while (history.length && history[0].t < corte) history.shift();

        if (state.has_signal && state.x_cm_px != null) {
          trail.push({ x: state.x_cm_px, y: state.y_cm_px });
          while (trail.length > TRAIL_MAX) trail.shift();
        } else if (!state.target_present) {
          trail.length = 0;
        }
        desenharGrafico();
      }

      const agora = Date.now();
      if (agora - lastMetricRender >= 1000) {
        render(state);
        lastMetricRender = agora;
      }
    } catch (_erro) {
      ui["system-state"].textContent = "painel desconectado";
      setTone(ui["system-state"], "fault");
    }
  }

  function atualizarFrame() {
    const imagem = new Image();
    imagem.onload = () => {
      ui["live-frame"].src = imagem.src;
      if (latest) desenharVisor(latest);
    };
    imagem.src = `/api/frame.jpg?t=${Date.now()}`;
  }

  setInterval(poll, 200);
  setInterval(atualizarFrame, 1000);
  window.addEventListener("resize", () => { if (latest) desenharVisor(latest); desenharGrafico(); });
  poll(); atualizarFrame(); desenharGrafico();
})();
