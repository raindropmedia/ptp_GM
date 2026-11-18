"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(k ?? ""));
  return e;
};
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Requested-With": "ptpgm" },
  };
  const r = await fetch(path, opt);
  const t = await r.text();
  let j; try { j = JSON.parse(t); } catch { j = { raw: t }; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function say(text, kind = "good") {
  const m = $("#msg"); m.hidden = !text; m.textContent = text || ""; m.className = kind === "err" ? "err" : "good";
  if (text) clearTimeout(say.t), say.t = setTimeout(() => (m.hidden = true), 8000);
}
const fmt = (v, d = 0, unit = "") => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d) + unit);

/* ---------------------------------------------------------- tabs / modes */
let tab = "dash", status = null;
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + tab));
  if (tab === "cfg") loadConfig();
  if (tab === "log") loadLog();
  if (tab === "ptp") loadIfaces();
  refresh();
});
$("#modes").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (!confirm("Modus wechseln und Dienste neu starten?")) return;
  try { const r = await api("/api/mode", { mode: b.dataset.mode }); say("Modus: " + r.mode + " – " + r.log.join(", ")); }
  catch (err) { say(err.message, "err"); }
  refresh();
});

/* ---------------------------------------------------------- dashboard */
function card(k, v, s, cls = "") {
  return el("div", { class: "card" }, el("div", { class: "k" }, k), el("div", { class: "v " + cls }, v), el("div", { class: "s" }, s || ""));
}
function renderDash(s) {
  const gps = s.gps, ptp = s.ptp, ts = s.ts2phc, gm = s.mode === "grandmaster";
  const cards = [];
  if (gm) {
    const fix = gps.mode >= 3 ? "3D-Fix" : gps.mode === 2 ? "2D-Fix" : "Kein Fix";
    cards.push(card("GPS", fix, `${gps.sats_used}/${gps.sats_seen} Satelliten`, gps.mode >= 2 ? "ok" : "bad"));
    cards.push(card("PPS → PHC", ts ? fmt(ts.offset_ns, 0, " ns") : "–",
      ts ? `Servo ${ts.servo_state} · ${fmt(ts.freq_ppb, 0, " ppb")}` : "ts2phc liefert nichts",
      ts && ts.servo_state === "s2" ? "ok" : "warn"));
  } else if (s.mode === "measure") {
    cards.push(card("Offset zum GM", ptp ? fmt(ptp.master_offset_ns, 0, " ns") : "–", ptp ? "Port " + ptp.port_state : "kein ptp4l",
      ptp && ptp.port_state === "SLAVE" ? "ok" : "warn"));
    cards.push(card("Pfadverzögerung", ptp ? fmt(ptp.path_delay_ns, 0, " ns") : "–", ""));
  }
  cards.push(card("PTP-Port", ptp ? ptp.port_state || "–" : "–", ptp ? "Domain " + fmt(ptp.domain) : "ptp4l nicht aktiv",
    ptp && ["MASTER", "SLAVE"].includes(ptp.port_state) ? "ok" : "warn"));
  cards.push(card("Grandmaster-ID", ptp ? ptp.gm_identity || "–" : "–", ptp ? "Klasse " + fmt(ptp.gm_clock_class) : ""));
  const n = s.nic;
  cards.push(card("Netzwerk " + n.name, n.link ? (n.speed || "Link") : "Kein Link", n.hw_timestamping ? "HW-Zeitstempel" : "nur SW-Zeitstempel", n.link ? "ok" : "bad"));
  if (s.chrony) cards.push(card("NTP (chrony)", "Stratum " + (s.chrony["Stratum"] || "?"), s.chrony["System time"] || ""));
  $("#cards").replaceChildren(...cards.filter(Boolean));
  const sv = Object.entries(s.services || {}).map(([k, v]) => el("span", { class: "chip " + (v === "active" ? "ok" : v === "inactive" ? "" : "bad") }, k + ": " + v));
  $("#services").replaceChildren(...sv);
  document.querySelectorAll("#modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === s.mode));
  $("#ver").textContent = "v" + s.version;
}

/* ---------------------------------------------------------- chart */
function stats(vals) {
  const v = vals.filter((x) => x != null); if (!v.length) return null;
  const n = v.length, mean = v.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
  const rms = Math.sqrt(v.reduce((a, b) => a + b * b, 0) / n);
  const min = Math.min(...v), max = Math.max(...v);
  return { n, mean, sd, rms, min, max, pp: max - min };
}
function drawChart(h, key1) {
  const c = $("#chart"), dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, ht = c.clientHeight;
  c.width = w * dpr; c.height = ht * dpr;
  const g = c.getContext("2d"); g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const col = (n) => css.getPropertyValue(n).trim();
  g.clearRect(0, 0, w, ht);
  g.font = "11px system-ui"; g.fillStyle = col("--mut"); g.strokeStyle = col("--bd");
  const L = 56, R = 56, T = 8, B = 20, pw = w - L - R, ph = ht - T - B;
  if (!h.length) { g.fillText("Noch keine Daten", L + 10, T + 20); return; }
  const t0 = h[0].t, t1 = h[h.length - 1].t || t0 + 1, dt = Math.max(1, t1 - t0);
  const series = [[key1, col("--l1"), L, "left"], ["delay", col("--l2"), w - R, "right"]];
  g.beginPath(); for (let i = 0; i <= 4; i++) { const y = T + (ph * i) / 4; g.moveTo(L, y); g.lineTo(w - R, y); } g.stroke();
  for (const [k, color, x, side] of series) {
    const v = h.map((s) => s[k]).filter((x) => x != null); if (!v.length) continue;
    let lo = Math.min(...v), hi = Math.max(...v); if (hi - lo < 2) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
    g.fillStyle = color; g.textAlign = side === "left" ? "right" : "left";
    for (let i = 0; i <= 4; i++) g.fillText(Math.round(hi - ((hi - lo) * i) / 4), side === "left" ? x - 6 : x + 6, T + (ph * i) / 4 + 4);
    g.strokeStyle = color; g.lineWidth = 1.5; g.beginPath(); let pen = false;
    for (const s of h) {
      if (s[k] == null) { pen = false; continue; }
      const px = L + ((s.t - t0) / dt) * pw, py = T + ph - ((s[k] - lo) / (hi - lo)) * ph;
      pen ? g.lineTo(px, py) : g.moveTo(px, py); pen = true;
    }
    g.stroke(); g.lineWidth = 1; g.strokeStyle = col("--bd");
  }
  g.fillStyle = col("--mut"); g.textAlign = "left"; g.fillText(new Date(t0 * 1000).toLocaleTimeString(), L, ht - 5);
  g.textAlign = "right"; g.fillText(new Date(t1 * 1000).toLocaleTimeString(), w - R, ht - 5);
}
async function renderChart(s) {
  const secs = $("#win").value, key1 = s.mode === "grandmaster" ? "pps_offset" : "offset";
  $("#chart-title").textContent = s.mode === "grandmaster" ? "GPS-PPS → PHC Offset (ts2phc)" : "Offset zum Grandmaster";
  $("#csv").href = "/api/history.csv?seconds=" + secs;
  const h = await api("/api/history?seconds=" + secs);
  drawChart(h, key1);
  const a = stats(h.map((x) => x[key1])), d = stats(h.map((x) => x.delay));
  const rows = [el("tr", {}, ...["", "N", "Mittel", "σ", "RMS", "Min", "Max", "Spanne"].map((x) => el("th", {}, x)))];
  const row = (name, st) => st && rows.push(el("tr", {}, el("td", {}, name), ...[st.n, fmt(st.mean, 1), fmt(st.sd, 1), fmt(st.rms, 1), fmt(st.min), fmt(st.max), fmt(st.pp)].map((x) => el("td", {}, String(x)))));
  row("Offset [ns]", a); row("Pfadverzögerung [ns]", d);
  $("#stats").replaceChildren(...rows);
}
$("#win").addEventListener("change", refresh);
$("#clear").addEventListener("click", async () => { await api("/api/history/clear", {}); refresh(); });

/* ---------------------------------------------------------- gps tab */
function renderGps(s) {
  const g = s.gps;
  $("#gps-cards").replaceChildren(
    card("gpsd", g.connected ? "verbunden" : "nicht erreichbar", "", g.connected ? "ok" : "bad"),
    card("Fix", g.mode >= 3 ? "3D" : g.mode === 2 ? "2D" : "keiner", g.time || "", g.mode >= 2 ? "ok" : "bad"),
    card("Satelliten", `${g.sats_used} / ${g.sats_seen}`, `HDOP ${fmt(g.hdop, 1)} · PDOP ${fmt(g.pdop, 1)}`),
    card("Position", g.lat != null ? fmt(g.lat, 5) + "°" : "–", g.lon != null ? fmt(g.lon, 5) + "° · " + fmt(g.alt, 0, " m") : ""),
    card("PPS (gpsd)", g.pps_seen ? "ja" : "nein", g.pps_qerr != null ? "qErr " + g.pps_qerr + " ps" : "Hinweis: PPS läuft über die I210, nicht über gpsd"),
  );
  const bars = (g.satellites || []).sort((a, b) => (b.snr || 0) - (a.snr || 0)).map((x) =>
    el("div", { class: "sat" + (x.used ? " used" : ""), title: `PRN ${x.prn} el ${x.el} az ${x.az}` },
      el("i", { style: `height:${Math.min(60, (x.snr || 0) * 1.2)}px` }), String(x.prn)));
  $("#sats").replaceChildren(el("div", { class: "bars" }, bars.length ? bars : "keine Daten"));
}

/* ---------------------------------------------------------- ptp tab */
function renderPtp(s) {
  const p = s.ptp; if (!p) { $("#ptp-table").replaceChildren(el("tr", {}, el("td", {}, "ptp4l läuft nicht oder antwortet nicht"))); return; }
  const rows = [["Port-Status", p.port_state], ["Offset zum Master", fmt(p.offset_ns, 1, " ns")], ["Mittlere Pfadverzögerung", fmt(p.path_delay_ns, 1, " ns")],
    ["Peer-Verzögerung (P2P)", fmt(p.peer_delay_ns, 1, " ns")], ["Schritte zum GM", fmt(p.steps_removed)],
    ["Lokale Clock-ID", p.local_clock_identity], ["Lokale Clock-Klasse", fmt(p.local_clock_class)], ["Domain", fmt(p.domain)],
    ["Grandmaster-ID", p.gm_identity], ["GM Priority1/2", fmt(p.gm_priority1) + " / " + fmt(p.gm_priority2)],
    ["GM Clock-Klasse", fmt(p.gm_clock_class)], ["GM Genauigkeit", p.gm_clock_accuracy], ["Parent-Port", p.parent_port],
    ["UTC-Offset", fmt(p.utc_offset) + (p.utc_offset_valid === "1" ? " (gültig)" : "")], ["PTP-Zeitskala", p.ptp_timescale],
    ["Zeit rückführbar", p.time_traceable], ["Frequenz rückführbar", p.frequency_traceable]];
  $("#ptp-table").replaceChildren(...rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v ?? "–"))));
}
async function loadIfaces() {
  const list = await api("/api/interfaces");
  $("#ifaces").replaceChildren(el("table", {},
    el("tr", {}, ...["Name", "Treiber", "Link", "Speed", "HW-TS", "PHC", "Pins / EXTTS / PEROUT"].map((x) => el("th", {}, x))),
    ...list.map((i) => el("tr", {}, ...[i.name, i.driver || "–", i.link ? "ja" : "nein", i.speed || "–", i.hw_timestamping ? "ja" : "nein",
      i.phc_index != null ? "/dev/ptp" + i.phc_index : "–",
      i.phc ? `${(i.phc.pins || []).join(",") || "–"} / ${i.phc.n_external_timestamps} / ${i.phc.n_periodic_outputs}` : "–"].map((x) => el("td", {}, String(x)))))));
}

/* ---------------------------------------------------------- config tab */
const SCHEMA = [
  ["Betrieb", "", [["mode", "Modus", "enum", ["grandmaster", "measure", "off"]]]],
  ["GPS (NEO-6M)", "gps", [["device", "Serielle Schnittstelle", "text"], ["baud", "Baudrate", "enum", [4800, 9600, 19200, 38400, 57600, 115200], "NEO-6M Werkseinstellung: 9600"]]],
  ["PPS-Eingang (I210)", "pps", [
    ["interface", "Netzwerkkarte", "text"], ["pin", "SDP-Pin (0–3)", "num", "", "SDP-Pin der I210, an dem PPS anliegt"],
    ["channel", "EXTTS-Kanal (0–1)", "num"], ["polarity", "Flanke", "enum", ["rising", "falling", "both"]],
    ["correction_ns", "Kompensation [ns]", "num", "", "Kabel-/Antennenlaufzeit"],
    ["source", "Zeit-der-Sekunde", "enum", ["nmea", "generic"], "nmea: Uhrzeit von gpsd; generic: Systemzeit"]]],
  ["PTP Grandmaster", "ptp", [
    ["interface", "Netzwerkkarte", "text"], ["domain", "Domain", "num"], ["transport", "Transport", "enum", ["UDPv4", "UDPv6", "L2"]],
    ["delay_mechanism", "Delay-Mechanismus", "enum", ["E2E", "P2P"]], ["time_stamping", "Zeitstempel", "enum", ["hardware", "software"]],
    ["priority1", "Priority1", "num"], ["priority2", "Priority2", "num"], ["clock_class", "Clock-Klasse", "num", "", "6 = GNSS-synchronisiert"],
    ["clock_accuracy", "Clock-Genauigkeit", "text", "", "0x20 = 25 ns, 0x21 = 100 ns, 0x22 = 250 ns"], ["time_source", "Zeitquelle", "text", "", "0x20 = GNSS"],
    ["utc_offset", "TAI–UTC [s]", "num", "", "aktuell 37"], ["log_announce_interval", "Announce-Intervall (log2 s)", "num"],
    ["log_sync_interval", "Sync-Intervall (log2 s)", "num"], ["log_min_delay_req_interval", "Delay-Req-Intervall (log2 s)", "num"]]],
  ["PTP Messgerät", "measure", [
    ["interface", "Netzwerkkarte", "text"], ["domain", "Domain", "num"], ["transport", "Transport", "enum", ["UDPv4", "UDPv6", "L2"]],
    ["delay_mechanism", "Delay-Mechanismus", "enum", ["E2E", "P2P"]], ["time_stamping", "Zeitstempel", "enum", ["hardware", "software"]],
    ["free_running", "Uhr nicht nachführen", "bool", "", "Nur messen – die lokale PHC wird nicht verändert"]]],
  ["System", "system", [
    ["phc2sys", "Systemzeit aus PHC (phc2sys)", "bool"], ["ntp_server", "NTP-Server (chrony)", "bool"],
    ["ntp_allow", "NTP erlaubt für", "text", "", "z. B. 192.168.0.0/16"]]],
  ["Web-UI (Neustart nötig)", "web", [["bind", "Adresse", "text"], ["port", "Port", "num"]]],
];
let cfg = null;
function fieldEl(sec, [key, label, type, opts, hint]) {
  const v = sec ? cfg[sec][key] : cfg[key], id = `f-${sec}-${key}`;
  let inp;
  if (type === "enum") { inp = el("select", { id }, opts.map((o) => { const x = el("option", { value: String(o) }, String(o)); if (o === v) x.selected = true; return x; })); }
  else if (type === "bool") { inp = el("input", { id, type: "checkbox" }); inp.checked = !!v; }
  else if (type === "num") { inp = el("input", { id, type: "number", value: v, step: 1 }); }
  else { inp = el("input", { id, type: "text", value: v ?? "" }); }
  inp.dataset.sec = sec; inp.dataset.key = key; inp.dataset.type = type;
  return [el("div", { class: "row" }, el("label", { for: id }, label), inp), hint ? el("div", { class: "row" }, el("span"), el("div", { class: "h" }, hint)) : null];
}
async function loadConfig() {
  cfg = await api("/api/config");
  $("#cfg-form").replaceChildren(...SCHEMA.map(([title, sec, fields]) =>
    el("fieldset", {}, el("legend", {}, title), ...fields.flatMap((f) => fieldEl(sec, f)).filter(Boolean))));
}
function collect() {
  const out = JSON.parse(JSON.stringify(cfg));
  document.querySelectorAll("#cfg-form [data-key]").forEach((i) => {
    const { sec, key, type } = i.dataset;
    let v = type === "bool" ? i.checked : type === "num" ? Number(i.value) : i.value;
    if (type === "enum" && /^\d+$/.test(v)) v = Number(v);
    (sec ? out[sec] : out)[key] = v;
  });
  return out;
}
async function saveCfg(apply) {
  try {
    cfg = await api("/api/config", collect());
    say("Gespeichert.");
    if (apply) {
      if (!confirm("Konfiguration anwenden und Dienste neu starten?")) return;
      const r = await api("/api/apply", {}); say("Angewendet: " + r.log.join(", "));
    }
  } catch (e) { say(e.message, "err"); }
}
$("#save").addEventListener("click", () => saveCfg(false));
$("#save-apply").addEventListener("click", () => saveCfg(true));
$("#setpw").addEventListener("click", async () => {
  try { await api("/api/password", { password: $("#newpw").value }); $("#newpw").value = ""; say("Passwort geändert. Beim nächsten Request erneut anmelden."); }
  catch (e) { say(e.message, "err"); }
});

/* ---------------------------------------------------------- logs */
async function loadLog() { const r = await api("/api/logs?unit=" + $("#log-unit").value); $("#log").textContent = r.log || "(leer)"; }
$("#log-refresh").addEventListener("click", loadLog);
$("#log-unit").addEventListener("change", loadLog);

/* ---------------------------------------------------------- main loop */
async function refresh() {
  try {
    status = await api("/api/status");
    renderDash(status); renderPtp(status); renderGps(status);
    if (tab === "dash") await renderChart(status);
  } catch (e) { say(e.message, "err"); }
}
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 2000);
