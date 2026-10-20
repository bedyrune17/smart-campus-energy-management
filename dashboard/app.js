/* Smart Campus Energy dashboard - plain JS + Chart.js, no build step. */
(() => {
  "use strict";

  const CFG = window.SCE_CONFIG || {};
  const LS_KEY = "sce.apiBaseUrl";
  const CAMPUS = "CAMPUS";
  const HOUR = 3600;
  const DAY = 86400;

  const STATUS = {
    occupied: { label: "In use", color: "--st-good", icon: "dot" },
    idle: { label: "Idle", color: "--st-neutral", icon: "ring" },
    wasting: { label: "Wasting", color: "--st-warning", icon: "warn" },
    power_issue: { label: "Power issue", color: "--st-serious", icon: "bolt" },
    fault: { label: "Fault", color: "--st-critical", icon: "stop" },
    offline: { label: "Offline", color: "--st-neutral", icon: "off" },
  };
  const SEVERITY = {
    critical: { label: "Critical", color: "--st-critical", icon: "stop" },
    high: { label: "High", color: "--st-serious", icon: "warn" },
    medium: { label: "Medium", color: "--st-warning", icon: "warn" },
    low: { label: "Low", color: "--st-neutral", icon: "ring" },
  };
  // Constant markup only - never interpolated with data.
  const ICONS = {
    dot: '<circle cx="8" cy="8" r="5" fill="currentColor"/>',
    ring: '<circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" stroke-width="2"/>',
    warn: '<path d="M8 1.2 15.2 14.5H.8z" fill="currentColor"/><rect x="7.2" y="5.6" width="1.6" height="4.6" fill="var(--surface)"/><rect x="7.2" y="11.3" width="1.6" height="1.6" fill="var(--surface)"/>',
    stop: '<path d="M5 1h6l4 4v6l-4 4H5l-4-4V5z" fill="currentColor"/><rect x="4" y="7.2" width="8" height="1.6" fill="var(--surface)"/>',
    bolt: '<path d="M9.5 1 3 9.2h4.2L6.5 15 13 6.8H8.8z" fill="currentColor"/>',
    off: '<circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3.5 12.5 12.5 3.5" stroke="currentColor" stroke-width="2"/>',
  };

  const state = {
    mode: null, api: "", scope: CAMPUS, range: 48,
    campus: null, summary: null, rooms: null, alerts: null, report: null,
    hourly: {}, forecast: {}, demo: null, charts: {}, timer: null, openRoom: null,
    tables: { load: false, daily: false },
  };

  // ------------------------------------------------------------------ helpers
  const $ = (id) => document.getElementById(id);
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "style") node.style.cssText = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  function icon(name, colorVar) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    svg.style.color = `var(${colorVar})`;
    svg.innerHTML = ICONS[name];
    return svg;
  }
  const chip = (def) => el("span", { class: "chip" }, icon(def.icon, def.color), def.label);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function alpha(hex, a) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  const offsetS = () => (state.campus?.utc_offset_minutes ?? 330) * 60;
  const nowTs = () => state.summary?.generated_at ?? Math.floor(Date.now() / 1000);
  const hourStart = (ts) => Math.floor((ts + offsetS()) / HOUR) * HOUR - offsetS();
  const dayStart = (ts) => Math.floor((ts + offsetS()) / DAY) * DAY - offsetS();
  function fmt(ts, opts) {
    return new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", ...opts }).format(new Date((ts + offsetS()) * 1000));
  }
  const fmtTime = (ts) => fmt(ts, { hour: "2-digit", minute: "2-digit", hour12: false });
  const fmtDayTime = (ts) => fmt(ts, { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const fmtDate = (ts) => fmt(ts, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const num = (v, d = 0) => Number(v ?? 0).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  function money(v) {
    const currency = state.summary?.currency || "INR";
    try {
      return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 0 }).format(v || 0);
    } catch {
      return `${currency} ${num(v)}`;
    }
  }
  function ago(ts) {
    const s = Math.max(0, nowTs() - ts);
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < DAY) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / DAY)} d ago`;
  }
  const buildingName = (id) => state.campus?.buildings.find((b) => b.id === id)?.name || id;
  const buildingColor = (id) => css(`--b-${id}`) || css("--b-IOT");
  const inScope = (buildingId) => state.scope === CAMPUS || buildingId === state.scope;

  // ------------------------------------------------------------------ data source
  function resolveSource() {
    const params = new URLSearchParams(location.search);
    const valid = (u) => /^https:\/\/[^\s"'<>]+$/i.test(u || "");
    if (params.has("demo")) return { mode: "demo" };
    let api = params.get("api");
    if (valid(api)) {
      try { localStorage.setItem(LS_KEY, api); } catch { /* storage unavailable */ }
    } else {
      api = "";
      try { api = localStorage.getItem(LS_KEY) || ""; } catch { /* storage unavailable */ }
    }
    if (api === "demo") return { mode: "demo" };
    if (!valid(api)) api = CFG.apiBaseUrl || "";
    return valid(api) ? { mode: "live", api: api.replace(/\/+$/, "") } : { mode: "demo" };
  }

  async function get(path) {
    if (state.mode === "demo") {
      if (!state.demo) {
        const r = await fetch("demo-data.json", { cache: "no-store" });
        if (!r.ok) throw new Error(`demo-data.json: HTTP ${r.status}`);
        state.demo = await r.json();
      }
      const v = state.demo.endpoints[path];
      if (v === undefined) throw new Error(`not in demo snapshot: ${path}`);
      return JSON.parse(JSON.stringify(v));
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    try {
      const r = await fetch(state.api + path, { signal: ctrl.signal, cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status} on ${path}`);
      return await r.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function loadAll() {
    document.body.classList.add("loading");
    try {
      if (!state.campus) {
        state.campus = await get("/campus");
        renderScopeFilter();
      }
      const scope = state.scope;
      const [summary, rooms, alerts, report, hourly, forecast] = await Promise.all([
        get("/summary"), get("/rooms"), get("/alerts?hours=24&limit=100"), get("/report?days=7"),
        get(`/hourly?scope=${encodeURIComponent(scope)}&hours=168`), get(`/forecast?scope=${encodeURIComponent(scope)}`),
      ]);
      Object.assign(state, { summary, rooms, alerts, report });
      state.hourly[scope] = hourly;
      state.forecast[scope] = forecast;
      renderAll();
      setBadge();
      if (state.mode === "demo") {
        showBanner(`Demo snapshot: a simulated campus generated by this project's backend code on ${fmtDate(summary.generated_at)} IST. ` +
          "Deploy the AWS stack (bash deploy.sh) and choose Data source to see live data.", false);
      } else {
        hideBanner();
      }
    } catch (err) {
      console.error(err);
      setBadge(true);
      showBanner(`Could not load data from ${state.mode === "demo" ? "the demo snapshot" : state.api}: ${err.message}. ` +
        (state.summary ? "Showing the last data received." : "Check the API URL under Data source."), true);
    } finally {
      document.body.classList.remove("loading");
    }
  }

  function setBadge(error = false) {
    const badge = $("mode-badge");
    badge.className = "badge " + (error ? "error" : state.mode === "live" ? "live" : "demo");
    badge.textContent = error ? "Offline" : state.mode === "live" ? "Live · AWS" : "Demo snapshot";
    $("updated").textContent = state.summary ? `Updated ${fmtTime(state.summary.generated_at)} IST` : "";
  }
  function showBanner(text, isError) {
    const b = $("banner");
    b.hidden = false;
    b.className = "banner" + (isError ? " error" : "");
    b.replaceChildren(text);
  }
  function hideBanner() { $("banner").hidden = true; }

  function scheduleRefresh() {
    clearInterval(state.timer);
    if (state.mode !== "live") return;
    state.timer = setInterval(() => { if (!document.hidden) loadAll(); }, Math.max(10, CFG.refreshSeconds || 30) * 1000);
  }

  // ------------------------------------------------------------------ filters
  function renderScopeFilter() {
    const box = $("scope-filter");
    const items = [{ id: CAMPUS, name: "Whole campus" }, ...state.campus.buildings];
    box.replaceChildren(...items.map((b) => el("button", {
      type: "button", "data-scope": b.id, "aria-pressed": String(b.id === state.scope),
      onclick: () => setScope(b.id),
    }, b.id === CAMPUS ? null : el("span", { class: "swatch", style: `background:var(--b-${b.id})` }), b.name)));
  }
  function setScope(scope) {
    state.scope = scope;
    for (const btn of $("scope-filter").querySelectorAll("button")) btn.setAttribute("aria-pressed", String(btn.dataset.scope === scope));
    loadAll();
  }
  function setRange(range) {
    state.range = range;
    for (const btn of $("range-filter").querySelectorAll("button")) btn.setAttribute("aria-pressed", String(Number(btn.dataset.range) === range));
    renderLoad();
  }

  // ------------------------------------------------------------------ rendering
  function renderAll() {
    renderKpis();
    renderLoad();
    renderAlerts();
    renderDaily();
    renderBuildings();
    renderRooms();
  }

  function scopeHourly() {
    return (state.hourly[state.scope]?.points || []).slice().sort((a, b) => a.hour - b.hour);
  }

  function renderKpis() {
    const s = state.summary;
    const scope = state.scope;
    const now = nowTs();
    const midnight = dayStart(now);
    const pts = scopeHourly();
    const sumWhere = (from, to, key) => pts.filter((p) => p.hour >= from && p.hour <= to).reduce((a, p) => a + (p[key] || 0), 0);
    const todayKwh = sumWhere(midnight, now, "energy_kwh");
    const wasteKwh = sumWhere(midnight, now, "waste_kwh");
    const yesterdayKwh = sumWhere(midnight - DAY, now - DAY, "energy_kwh");
    const rooms = state.rooms.rooms.filter((r) => inScope(r.building_id));
    const building = s.buildings.find((b) => b.id === scope);
    const powerKw = scope === CAMPUS ? s.campus.power_kw : building?.power_kw ?? 0;
    const tariff = s.tariff_per_kwh;

    $("kpi-power").textContent = `${num(powerKw, 1)} kW`;
    $("kpi-power-sub").textContent =
      `${rooms.filter((r) => r.status !== "offline").length} of ${rooms.length} rooms reporting · ` +
      `${rooms.filter((r) => r.status === "occupied").length} in use · ${rooms.filter((r) => r.status === "wasting").length} wasting`;

    $("kpi-energy").textContent = `${num(todayKwh, 0)} kWh`;
    const sub = $("kpi-energy-sub");
    sub.replaceChildren();
    if (yesterdayKwh > 0) {
      const pct = (todayKwh / yesterdayKwh - 1) * 100;
      const up = pct >= 0;
      sub.append(el("span", { class: up ? "delta-bad" : "delta-good" }, `${up ? "▲" : "▼"} ${num(Math.abs(pct), 1)}%`),
        " vs yesterday at this time");
    } else {
      sub.append("since midnight");
    }

    $("kpi-cost").textContent = money(todayKwh * tariff);
    $("kpi-cost-sub").textContent = `${num(todayKwh * s.co2_kg_per_kwh, 0)} kg CO₂ · ${money(tariff)}/kWh`;

    $("kpi-waste").textContent = `${num(wasteKwh, 0)} kWh`;
    $("kpi-waste-sub").textContent = `${money(wasteKwh * tariff)} avoidable · ${todayKwh ? num((wasteKwh / todayKwh) * 100, 0) : 0}% of today's energy`;

    const fc = state.forecast[scope];
    if (fc && fc.points?.length) {
      $("kpi-forecast").textContent = `${num(fc.next_24h_kwh, 0)} kWh`;
      const acc = fc.backtest?.wape_pct;
      $("kpi-forecast-sub").textContent = `peak ${num(fc.peak_kw, 0)} kW at ${fmtTime(fc.peak_hour)} · ${money(fc.next_24h_kwh * tariff)}` +
        (acc !== null && acc !== undefined ? ` · model error ${num(acc, 0)}%` : "");
    } else {
      $("kpi-forecast").textContent = "–";
      $("kpi-forecast-sub").textContent = "forecast runs hourly once data exists";
    }

    const alerts = state.alerts.alerts.filter((a) => inScope(a.building_id));
    const severe = alerts.filter((a) => a.severity === "high" || a.severity === "critical").length;
    $("kpi-alerts").textContent = num(alerts.length);
    $("kpi-alerts-sub").textContent = `${severe} high or critical · ${alerts.filter((a) => a.type === "ENERGY_WASTE" || a.type === "AFTER_HOURS").length} waste`;
  }

  // shared Chart.js look
  function baseOptions() {
    const muted = css("--axis-muted");
    const grid = css("--grid");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 4 } },
      plugins: {
        legend: {
          position: "top", align: "start",
          labels: { color: css("--text-secondary"), boxWidth: 14, boxHeight: 8, usePointStyle: true, font: { size: 12 } },
        },
        tooltip: {
          backgroundColor: css("--surface"), titleColor: css("--text-secondary"), bodyColor: css("--text-primary"),
          footerColor: css("--text-secondary"), borderColor: css("--baseline"), borderWidth: 1, padding: 10,
          bodyFont: { weight: "600" }, usePointStyle: true, boxWidth: 12, boxHeight: 2,
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: css("--baseline") }, ticks: { color: muted, maxRotation: 0, autoSkip: true, font: { size: 11 } } },
        y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: muted, font: { size: 11 } } },
      },
    };
  }

  // vertical hairline that follows the hovered x (crosshair) + a labelled "now" line
  const markersPlugin = {
    id: "markers",
    afterDatasetsDraw(chart, _args, opts) {
      const { ctx, chartArea: area, scales: { x } } = chart;
      ctx.save();
      ctx.lineWidth = 1;
      if (opts.nowIndex !== undefined && opts.nowIndex !== null && opts.nowIndex >= 0) {
        const px = x.getPixelForValue(opts.nowIndex) - (x.getPixelForValue(1) - x.getPixelForValue(0)) / 2;
        ctx.strokeStyle = css("--baseline");
        ctx.beginPath(); ctx.moveTo(px, area.top); ctx.lineTo(px, area.bottom); ctx.stroke();
        ctx.fillStyle = css("--text-muted");
        ctx.font = "11px system-ui, sans-serif";
        ctx.fillText("now", px + 4, area.top + 10);
      }
      for (const line of opts.hlines || []) {
        const py = chart.scales.y.getPixelForValue(line.value);
        if (py < area.top || py > area.bottom) continue;
        ctx.strokeStyle = css("--baseline");
        ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
        ctx.fillStyle = css("--text-muted");
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(line.label, area.right - 2, py - 4);
      }
      const active = chart.tooltip?.getActiveElements?.() || [];
      if (active.length) {
        const ax = active[0].element.x;
        ctx.strokeStyle = css("--axis-muted");
        ctx.beginPath(); ctx.moveTo(ax, area.top); ctx.lineTo(ax, area.bottom); ctx.stroke();
      }
      ctx.restore();
    },
  };

  function makeChart(key, canvasId, config) {
    if (typeof Chart === "undefined") return; // CDN blocked: the table views still carry every value
    if (state.charts[key]) state.charts[key].destroy();
    state.charts[key] = new Chart($(canvasId), config);
  }

  function renderLoad() {
    const scope = state.scope;
    const now = nowTs();
    const cur = hourStart(now);
    const hist = scopeHourly().filter((p) => p.hour < cur && p.hour >= cur - state.range * HOUR);
    const fpts = (state.forecast[scope]?.points || []).filter((p) => p.hour >= cur);
    const hours = [...new Set([...hist.map((p) => p.hour), ...fpts.map((p) => p.hour)])].sort((a, b) => a - b);
    const byHour = (arr, key) => { const m = new Map(arr.map((p) => [p.hour, p[key]])); return hours.map((h) => (m.has(h) ? m.get(h) : null)); };
    const actual = byHour(hist, "energy_kwh");
    const forecast = byHour(fpts, "kwh");
    const lower = byHour(fpts, "lower");
    const upper = byHour(fpts, "upper");
    const nowIndex = hours.indexOf(fpts[0]?.hour);

    $("load-title").textContent = `${scope === CAMPUS ? "Campus" : buildingName(scope)} demand: actual vs forecast`;
    const cActual = css("--actual");
    const cFc = css("--forecast");
    const opts = baseOptions();
    opts.plugins.markers = { nowIndex };
    opts.plugins.legend.labels.filter = (item) => item.text !== "lower";
    opts.plugins.tooltip.filter = (item) => item.dataset.label !== "lower" && item.raw !== null;
    opts.plugins.tooltip.callbacks = {
      title: (items) => { const h = hours[items[0].dataIndex]; return `${fmtDayTime(h)} – ${fmtTime(h + HOUR)}`; },
      label: (item) => {
        if (item.dataset.label === "95% interval") return ` ${num(lower[item.dataIndex], 1)} – ${num(upper[item.dataIndex], 1)} kWh  95% interval`;
        return ` ${num(item.raw, 1)} kWh  ${item.dataset.label}`;
      },
    };
    opts.scales.x.ticks.maxTicksLimit = state.range > 48 ? 8 : 9;
    opts.scales.x.ticks.callback = (_v, i) => {
      const h = hours[i];
      if (h === undefined) return "";
      return state.range > 48 ? fmt(h, { weekday: "short", hour: "2-digit", hour12: false }) : fmtTime(h);
    };
    opts.scales.y.title = { display: true, text: "kWh per hour", color: css("--axis-muted"), font: { size: 11 } };

    makeChart("load", "load-chart", {
      type: "line",
      data: {
        labels: hours,
        datasets: [
          { label: "Actual", data: actual, borderColor: cActual, backgroundColor: alpha(cActual, 0.1), fill: "origin",
            borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHoverBorderWidth: 2, pointHoverBorderColor: css("--surface"),
            pointStyle: "line", tension: 0.25, spanGaps: false },
          { label: "Forecast", data: forecast, borderColor: cFc, backgroundColor: cFc, borderDash: [6, 4], borderWidth: 2,
            pointRadius: 0, pointHoverRadius: 4, pointStyle: "line", tension: 0.25, fill: false },
          { label: "95% interval", data: upper, borderWidth: 0, backgroundColor: alpha(cFc, 0.12), pointRadius: 0,
            pointHoverRadius: 0, fill: "+1", pointStyle: "rect", tension: 0.25 },
          { label: "lower", data: lower, borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, fill: false, tension: 0.25 },
        ],
      },
      options: opts,
      plugins: [markersPlugin],
    });

    const rows = hours.map((h, i) => [`${fmtDayTime(h)}`, actual[i], forecast[i], lower[i], upper[i]]);
    renderTable("load", ["Hour (IST)", "Actual kWh", "Forecast kWh", "Lower 95%", "Upper 95%"], rows.reverse());
  }

  function renderDaily() {
    const rep = state.report;
    const labels = rep.days.map((d) => new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", weekday: "short", day: "numeric" })
      .format(new Date(`${d}T12:00:00Z`)));
    const buildings = rep.buildings.filter((b) => inScope(b.id));
    const surface = css("--surface");
    const topIndex = labels.map((_, i) => {
      let top = -1;
      buildings.forEach((b, j) => { if (b.kwh[i] > 0) top = j; });
      return top;
    });
    const opts = baseOptions();
    opts.plugins.legend.labels.pointStyle = "rect";
    opts.plugins.tooltip.callbacks = {
      label: (item) => ` ${num(item.raw, 0)} kWh  ${item.dataset.label}`,
      footer: (items) => `Total ${num(items.reduce((a, it) => a + (it.raw || 0), 0), 0)} kWh`,
    };
    opts.plugins.tooltip.usePointStyle = false;
    opts.plugins.tooltip.boxWidth = 10;
    opts.plugins.tooltip.boxHeight = 10;
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    opts.scales.y.title = { display: true, text: "kWh", color: css("--axis-muted"), font: { size: 11 } };
    opts.scales.y.ticks.callback = (v) => (v >= 1000 ? `${num(v / 1000, 1)}K` : v);

    makeChart("daily", "daily-chart", {
      type: "bar",
      data: {
        labels,
        datasets: buildings.map((b, j) => ({
          label: b.name,
          data: b.kwh,
          backgroundColor: buildingColor(b.id),
          hoverBackgroundColor: alpha(buildingColor(b.id), 0.8),
          borderColor: surface,
          borderWidth: { top: 2, bottom: 0, left: 0, right: 0 },
          borderSkipped: false,
          borderRadius: (ctx) => (topIndex[ctx.dataIndex] === j ? { topLeft: 4, topRight: 4 } : 0),
          maxBarThickness: 24,
        })),
      },
      options: opts,
    });

    const header = ["Day", ...buildings.map((b) => b.name), "Total kWh", "Waste kWh"];
    const rows = rep.days.map((d, i) => {
      const vals = buildings.map((b) => b.kwh[i]);
      const waste = buildings.reduce((a, b) => a + b.waste_kwh[i], 0);
      return [labels[i], ...vals, vals.reduce((a, v) => a + v, 0), waste];
    });
    renderTable("daily", header, rows.reverse());
  }

  function renderTable(key, header, rows) {
    const table = el("table", { class: "data" },
      el("thead", {}, el("tr", {}, header.map((h, i) => el("th", { class: i ? "num" : null }, h)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, r.map((v, i) => el("td", { class: i ? "num" : null },
        i === 0 ? v : v === null || v === undefined ? "–" : num(v, 1)))))));
    $(`${key}-table`).replaceChildren(table);
  }

  function renderBuildings() {
    const s = state.summary;
    const list = s.buildings.filter((b) => inScope(b.id));
    const tariff = s.tariff_per_kwh;
    const head = el("tr", {}, ["Building", "Status", "Live kW", "Today kWh", "Waste kWh", "Waste cost", "Rooms in use"]
      .map((h, i) => el("th", { class: i > 1 ? "num" : null }, h)));
    const rows = list.map((b) => el("tr", {},
      el("td", {}, el("span", { class: "name-cell" }, el("span", { class: "swatch", style: `background:var(--b-${b.id})` }), b.name)),
      el("td", {}, b.open ? chip({ label: "Open", color: "--st-good", icon: "dot" }) : chip({ label: "Closed", color: "--st-neutral", icon: "ring" })),
      el("td", { class: "num" }, num(b.power_kw, 1)),
      el("td", { class: "num" }, num(b.today_kwh, 0)),
      el("td", { class: "num" }, num(b.today_waste_kwh, 0)),
      el("td", { class: "num" }, money(b.today_waste_kwh * tariff)),
      el("td", { class: "num" }, `${b.occupied} / ${b.rooms}`)));
    if (state.scope === CAMPUS) {
      const c = s.campus;
      rows.push(el("tr", { style: "font-weight:600" },
        el("td", {}, "Whole campus"), el("td", {}, ""),
        el("td", { class: "num" }, num(c.power_kw, 1)), el("td", { class: "num" }, num(c.today_kwh, 0)),
        el("td", { class: "num" }, num(c.today_waste_kwh, 0)), el("td", { class: "num" }, money(c.today_waste_kwh * tariff)),
        el("td", { class: "num" }, `${c.rooms_occupied} / ${c.rooms_total}`)));
    }
    $("buildings-table").replaceChildren(el("thead", {}, head), el("tbody", {}, rows));
  }

  function renderAlerts() {
    const list = state.alerts.alerts.filter((a) => inScope(a.building_id));
    const box = $("alerts-list");
    if (!list.length) {
      box.replaceChildren(el("li", { class: "empty" }, "No abnormal usage in the last 24 hours."));
      return;
    }
    box.replaceChildren(...list.slice(0, 60).map((a) => el("li", { class: "alert" },
      el("div", { class: "alert-top" }, chip(SEVERITY[a.severity] || SEVERITY.low),
        el("span", { class: "alert-meta", title: fmtDate(a.ts) }, `${fmtTime(a.ts)} · ${ago(a.ts)}`)),
      el("p", { class: "alert-title" }, a.title),
      el("p", { class: "alert-msg" }, a.message),
      el("p", { class: "alert-meta" }, a.building_name || buildingName(a.building_id)))));
  }

  function renderRooms() {
    $("status-legend").replaceChildren(...Object.values(STATUS).map((d) => el("li", {}, chip(d))));
    const rooms = state.rooms.rooms.filter((r) => inScope(r.building_id));
    const groups = new Map();
    for (const r of rooms) {
      if (!groups.has(r.building_id)) groups.set(r.building_id, []);
      groups.get(r.building_id).push(r);
    }
    $("rooms-grid").replaceChildren(...[...groups].map(([bid, list]) => el("div", { class: "building-group" },
      el("h3", {}, el("span", { class: "swatch", style: `background:var(--b-${bid}, var(--b-IOT))` }), buildingName(bid)),
      el("div", { class: "room-list" }, list.map(roomCard)))));
  }

  function roomCard(r) {
    const st = STATUS[r.status] || STATUS.idle;
    const pct = Math.min(100, (r.power_w / r.rated_w) * 100 || 0);
    const meterColor = ["wasting", "fault", "power_issue"].includes(r.status) ? `var(${st.color})` : "var(--actual)";
    const meta = [];
    if (r.occupancy !== null && r.occupancy !== undefined) meta.push(`${r.occupancy} / ${r.capacity} people`);
    if (r.temperature_c !== null && r.temperature_c !== undefined) meta.push(`${num(r.temperature_c, 1)} °C`);
    meta.push(`today ${num(r.today_kwh, 1)} kWh`);
    return el("button", {
      type: "button", class: "room", style: `--room-status: var(${st.color})`,
      "aria-label": `${r.name}: ${st.label}, ${num(r.power_w / 1000, 1)} kilowatts`,
      onclick: () => openRoom(r.room_id),
    },
    el("span", { class: "room-name" }, r.name),
    chip(st),
    el("span", { class: "room-power" }, `${num(r.power_w / 1000, 2)} kW `, el("small", {}, `of ${num(r.rated_w / 1000, 1)} kW rated`)),
    el("span", { class: "meter", "aria-hidden": "true" }, el("span", { style: `width:${pct}%;background:${meterColor}` })),
    el("span", { class: "room-meta" }, meta.map((m) => el("span", {}, m))));
  }

  async function openRoom(roomId) {
    const r = state.rooms.rooms.find((x) => x.room_id === roomId);
    if (!r) return;
    state.openRoom = roomId;
    const dlg = $("room-dialog");
    $("room-title").textContent = r.name;
    $("room-sub").textContent = `${buildingName(r.building_id)} · ${r.type.replace(/_/g, " ")} · capacity ${r.capacity} · rated ${num(r.rated_w / 1000, 1)} kW`;
    const stat = (label, value) => el("div", {}, el("span", {}, label), el("strong", {}, value));
    $("room-stats").replaceChildren(
      stat("Status", (STATUS[r.status] || STATUS.idle).label),
      stat("Power now", `${num(r.power_w / 1000, 2)} kW`),
      stat("People", r.occupancy ?? "n/a"),
      stat("Temperature", r.temperature_c !== null && r.temperature_c !== undefined ? `${num(r.temperature_c, 1)} °C` : "n/a"),
      stat("Voltage", r.voltage_v ? `${num(r.voltage_v, 0)} V` : "n/a"),
      stat("Today", `${num(r.today_kwh, 1)} kWh`),
      stat("Wasted today", `${num(r.today_waste_kwh, 1)} kWh`));
    if (!dlg.open) dlg.showModal();
    try {
      const data = await get(`/rooms/${encodeURIComponent(roomId)}/readings?hours=6`);
      if (state.openRoom !== roomId) return;
      drawRoomCharts(r, data);
    } catch (err) {
      $("room-sub").textContent += ` · history unavailable (${err.message})`;
    }
  }

  function drawRoomCharts(room, data) {
    const rows = data.readings;
    const labels = rows.map((x) => x.ts);
    const color = buildingColor(room.building_id);
    const tick = (_v, i) => (labels[i] !== undefined ? fmtTime(labels[i]) : "");

    const p = baseOptions();
    p.plugins.legend.display = false;
    p.plugins.markers = { hlines: [
      { value: data.idle_threshold_w / 1000, label: "waste threshold when empty" },
      { value: data.rated_w / 1000, label: "rated load" },
    ] };
    p.plugins.tooltip.callbacks = {
      title: (items) => fmtDayTime(labels[items[0].dataIndex]),
      label: (item) => ` ${num(item.raw, 2)} kW  power`,
      afterLabel: (item) => { const s = rows[item.dataIndex].status; return s ? `  status: ${(STATUS[s] || { label: s }).label}` : ""; },
    };
    p.scales.x.ticks.callback = tick;
    p.scales.x.ticks.maxTicksLimit = 7;
    p.scales.y.suggestedMax = (data.rated_w / 1000) * 1.05;
    makeChart("roomPower", "room-power-chart", {
      type: "line",
      data: { labels, datasets: [{ label: "Power", data: rows.map((x) => x.power_w / 1000), borderColor: color,
        backgroundColor: alpha(color, 0.1), fill: "origin", borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
        pointHoverBorderWidth: 2, pointHoverBorderColor: css("--surface"), stepped: false, tension: 0.2 }] },
      options: p,
      plugins: [markersPlugin],
    });

    const o = baseOptions();
    o.plugins.legend.display = false;
    o.plugins.tooltip.callbacks = {
      title: (items) => fmtDayTime(labels[items[0].dataIndex]),
      label: (item) => ` ${item.raw ?? "n/a"} people`,
    };
    o.scales.x.ticks.callback = tick;
    o.scales.x.ticks.maxTicksLimit = 7;
    o.scales.y.ticks.precision = 0;
    o.scales.y.suggestedMax = room.capacity;
    makeChart("roomOcc", "room-occ-chart", {
      type: "bar",
      data: { labels, datasets: [{ label: "People", data: rows.map((x) => x.occupancy), backgroundColor: css("--actual"),
        borderRadius: { topLeft: 2, topRight: 2 }, borderSkipped: "start", maxBarThickness: 8, categoryPercentage: 0.9, barPercentage: 0.9 }] },
      options: o,
    });
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    Object.assign(state, resolveSource());
    $("repo-link").href = CFG.repoUrl || "#";
    for (const btn of $("range-filter").querySelectorAll("button")) btn.addEventListener("click", () => setRange(Number(btn.dataset.range)));
    $("refresh-btn").addEventListener("click", loadAll);
    for (const btn of document.querySelectorAll(".table-toggle")) {
      btn.setAttribute("aria-pressed", "false");
      btn.addEventListener("click", () => {
        const key = btn.dataset.target;
        state.tables[key] = !state.tables[key];
        btn.setAttribute("aria-pressed", String(state.tables[key]));
        $(`${key}-table`).hidden = !state.tables[key];
        $(`${key}-chart`).parentElement.hidden = state.tables[key];
      });
    }
    $("room-dialog").addEventListener("close", () => { state.openRoom = null; });
    $("settings-btn").addEventListener("click", () => {
      $("api-input").value = state.mode === "live" ? state.api : "";
      $("settings-dialog").showModal();
    });
    $("settings-form").addEventListener("submit", (ev) => {
      const action = ev.submitter?.value;
      const url = $("api-input").value.trim().replace(/\/+$/, "");
      if (action === "save" && /^https:\/\/[^\s"'<>]+$/i.test(url)) {
        try { localStorage.setItem(LS_KEY, url); } catch { /* storage unavailable */ }
        Object.assign(state, { mode: "live", api: url });
      } else if (action === "demo" || (action === "save" && !url)) {
        try { localStorage.setItem(LS_KEY, "demo"); } catch { /* storage unavailable */ }
        Object.assign(state, { mode: "demo", api: "" });
      } else {
        ev.preventDefault();
        $("api-input").setCustomValidity("Enter the https:// ApiUrl from the deploy output");
        $("api-input").reportValidity();
        return;
      }
      Object.assign(state, { campus: null, summary: null, hourly: {}, forecast: {} });
      scheduleRefresh();
      loadAll();
    });
    $("api-input").addEventListener("input", () => $("api-input").setCustomValidity(""));
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.summary) renderAll(); });
    if (typeof Chart === "undefined") {
      showBanner("Chart library failed to load (offline?). Tables still work - use the Table buttons.", true);
    }
    scheduleRefresh();
    loadAll();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
