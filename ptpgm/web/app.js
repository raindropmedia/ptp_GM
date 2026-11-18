"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? (e.className = v) : e.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
};
const C = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v, d = 0, u = "") => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d) + u);
const pad = (n) => String(n).padStart(2, "0");
const hhmmss = (d) => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
const dot = (id, cls) => ($(id).className = "dot " + (cls || ""));

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Requested-With": "ptpgm" } };
  const r = await fetch(path, opt), t = await r.text();
  let j; try { j = JSON.parse(t); } catch { j = { raw: t }; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(text, err) {
  const t = $("#toast"); t.textContent = text; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), 7000);
}

/* ---------------- building blocks ---------------- */
const tile = (k, v, s, cls = "") => el("div", { class: "tile" }, el("div", { class: "k" }, k), el("div", { class: "v " + cls }, v), el("div", { class: "s" }, s || ""));
const kv = (id, rows) => $(id).replaceChildren(...rows.filter(Boolean).map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v == null || v === "" ? "–" : v))));

function prep(cv) {
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px system-ui"; return { g, w, h };
}
function lineChart(id, series, unit = "ns") {
  const { g, w, h } = prep($(id)); if (w < 30) return; const  L = 52, R = 10, T = 8, B = 20, pw = w - L - R, ph = h - T - B;
  const pts = series.flatMap((s) => s.data);
  g.fillStyle = C("--muted"); g.strokeStyle = C("--line");
  if (!pts.length) { g.fillText("Noch keine Daten", L + 8, T + 18); return; }
  let t0 = Math.min(...pts.map((p) => p[0])), t1 = Math.max(...pts.map((p) => p[0])); if (t1 - t0 < 1) t1 = t0 + 1;
  let lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
  if (hi - lo < 4) { const m = (hi + lo) / 2; lo = m - 2; hi = m + 2; }
  const pd = (hi - lo) * 0.08; lo -= pd; hi += pd;
  const y = (v) => T + ph - ((v - lo) / (hi - lo)) * ph, x = (t) => L + ((t - t0) / (t1 - t0)) * pw;
  g.textAlign = "right"; g.beginPath();
  for (let i = 0; i <= 4; i++) { const v = hi - ((hi - lo) * i) / 4, yy = T + (ph * i) / 4; g.moveTo(L, yy); g.lineTo(w - R, yy); g.fillText(Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1), L - 6, yy + 4); }
  g.stroke();
  if (lo < 0 && hi > 0) { g.strokeStyle = C("--grey"); g.beginPath(); g.moveTo(L, y(0)); g.lineTo(w - R, y(0)); g.stroke(); }
  for (const s of series) {
    g.strokeStyle = s.color; g.lineWidth = 1.5; g.beginPath(); let prev = null;
    for (const [t, v] of s.data) { const px = x(t), py = y(v); (prev == null || t - prev > 5) ? g.moveTo(px, py) : g.lineTo(px, py); prev = t; }
    g.stroke();
  }
  g.lineWidth = 1; g.fillStyle = C("--muted"); g.textAlign = "left"; g.fillText(new Date(t0 * 1000).toLocaleTimeString(), L, h - 5);
  g.textAlign = "right"; g.fillText(new Date(t1 * 1000).toLocaleTimeString() + "  [" + unit + "]", w - R, h - 5);
}
function histChart(id, vals) {
  const { g, w, h } = prep($(id)); if (w < 30) return; const  L = 8, B = 20, T = 8;
  g.fillStyle = C("--muted");
  if (vals.length < 5) { g.fillText("Zu wenige Daten", L, T + 14); return; }
  const lo = Math.min(...vals), hi = Math.max(...vals), n = 24, span = Math.max(hi - lo, 1), bins = new Array(n).fill(0);
  vals.forEach((v) => bins[Math.min(n - 1, Math.floor(((v - lo) / span) * n))]++);
  const mx = Math.max(...bins), bw = (w - 2 * L) / n; g.fillStyle = C("--green");
  bins.forEach((c, i) => g.fillRect(L + i * bw + 1, h - B - (c / mx) * (h - B - T), bw - 2, (c / mx) * (h - B - T)));
  g.fillStyle = C("--muted"); g.textAlign = "left"; g.fillText(lo.toFixed(0), L, h - 5); g.textAlign = "right"; g.fillText(hi.toFixed(0) + " ns", w - L, h - 5);
}
function skyplot(id, sats) {
  const { g, w, h } = prep($(id)); if (w < 30) return; const  cx = w / 2, cy = h / 2, r = Math.min(cx, cy) - 14;
  g.strokeStyle = C("--line"); g.fillStyle = C("--muted"); g.textAlign = "center";
  for (const f of [1, 2 / 3, 1 / 3]) { g.beginPath(); g.arc(cx, cy, r * f, 0, 7); g.stroke(); }
  g.beginPath(); g.moveTo(cx - r, cy); g.lineTo(cx + r, cy); g.moveTo(cx, cy - r); g.lineTo(cx, cy + r); g.stroke();
  g.fillText("N", cx, cy - r - 3); g.fillText("S", cx, cy + r + 11); g.fillText("O", cx + r + 8, cy + 4); g.fillText("W", cx - r - 8, cy + 4);
  for (const s of sats) {
    if (s.el == null || s.az == null) continue;
    const rr = r * (1 - s.el / 90), a = (s.az * Math.PI) / 180, px = cx + rr * Math.sin(a), py = cy - rr * Math.cos(a);
    g.fillStyle = s.used ? C("--green") : C("--grey"); g.beginPath(); g.arc(px, py, 9, 0, 7); g.fill();
    g.fillStyle = "#101418"; g.fillText(s.prn, px, py + 4);
  }
}
function snrChart(id, sats) {
  const { g, w, h } = prep($(id)); if (w < 30) return; const  L = 30, B = 22, T = 8, ph = h - B - T, mx = 55;
  g.strokeStyle = C("--line"); g.fillStyle = C("--muted"); g.textAlign = "right";
  for (const v of [0, 30, 40, 50]) { const yy = T + ph - (v / mx) * ph; g.beginPath(); g.moveTo(L, yy); g.lineTo(w, yy); g.stroke(); g.fillText(v, L - 4, yy + 4); }
  const list = [...sats].sort((a, b) => a.prn - b.prn); if (!list.length) return;
  const bw = (w - L) / list.length; g.textAlign = "center";
  list.forEach((s, i) => { const v = s.snr || 0; g.fillStyle = s.used ? C("--green") : C("--orange"); g.fillRect(L + i * bw + 2, T + ph - (v / mx) * ph, bw - 4, (v / mx) * ph);
    g.fillStyle = C("--muted"); g.fillText(s.prn, L + i * bw + bw / 2, h - 7); });
}
function stats(vals) {
  const v = vals.filter((x) => x != null); if (!v.length) return null;
  const n = v.length, mean = v.reduce((a, b) => a + b, 0) / n, sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
  const min = Math.min(...v), max = Math.max(...v);
  return { n, mean, sd, rms: Math.sqrt(v.reduce((a, b) => a + b * b, 0) / n), min, max, pp: max - min };
}

/* ---------------- state / tabs ---------------- */
let ST = null, HIST = [], tab = "ov", pendingMode = null, cfg = null;
const MODELBL = { grandmaster: "Sender · Grandmaster", measure: "Empfänger · Messgerät", off: "Aus" };
$("#nav").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return; tab = b.dataset.tab;
  document.querySelectorAll("#nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + tab));
  if (tab === "set") loadSettings(); if (tab === "log") loadLog(); render();
});
document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => {
  pendingMode = b.dataset.mode; if (pendingMode === ST?.mode) { $("#confirm").classList.remove("show"); return; }
  $("#confirmT").textContent = `Zu „${MODELBL[pendingMode]}“ wechseln? Dienste werden neu gestartet (Passwort erforderlich).`;
  $("#confirm").classList.add("show");
}));
$("#confirmNo").addEventListener("click", () => $("#confirm").classList.remove("show"));
$("#confirmYes").addEventListener("click", async () => {
  $("#confirm").classList.remove("show");
  try { const r = await api("/api/mode", { mode: pendingMode }); toast("Modus: " + MODELBL[r.mode] + " – " + r.log.join(", ")); }
  catch (e) { toast(e.message, true); }
});
$("#win").addEventListener("change", () => poll(true));
$("#btnClear").addEventListener("click", async () => { try { await api("/api/history/clear", {}); HIST = []; render(); } catch (e) { toast(e.message, true); } });

/* ---------------- render ---------------- */
function render() {
  if (!ST) return;
  const d = ST, g = d.gps, p = d.ptp, ts = d.ts2phc, gm = d.mode === "grandmaster", n = d.nic;
  $("#host").textContent = d.hostname + (d.demo ? " · Demo" : ""); $("#demoWarn").classList.toggle("show", !!d.demo);
  const now = new Date(d.now * 1000); $("#clkUtc").textContent = hhmmss(now); $("#clkTai").textContent = hhmmss(new Date((d.now + d.utc_offset) * 1000));
  // pills
  $("#pModeT").textContent = MODELBL[d.mode]; dot("#pMode", d.mode === "off" ? "" : "info");
  const fix = g.mode || 0; dot("#pGps", !g.connected ? "bad" : fix >= 3 ? "ok" : fix === 2 ? "warn" : "bad");
  $("#pGpsT").textContent = !g.connected ? "gpsd getrennt" : fix >= 3 ? "GPS 3D-Fix" : fix === 2 ? "GPS 2D-Fix" : "GPS kein Fix";
  dot("#pPps", d.mode === "off" ? "" : !ts ? "bad" : ts.servo_state === "s2" ? "ok" : "warn");
  $("#pPpsT").textContent = d.mode === "off" ? "PPS –" : !ts ? "PPS keine Daten" : "PPS " + fmt(ts.offset_ns, 0, " ns");
  const src = (d.chrony_sources || []).find((x) => x.state === "*");
  dot("#pChr", d.services.chrony !== "active" ? "" : src ? "ok" : "warn"); $("#pChrT").textContent = "chrony" + (src ? " · " + src.name : "");
  const ps = p?.port_state, pOk = gm ? ps === "MASTER" : ps === "SLAVE";
  dot("#pPtp", d.services.ptp4l !== "active" ? "" : pOk ? "ok" : "warn"); $("#pPtpT").textContent = "ptp4l" + (ps ? " · " + ps : "");
  document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === d.mode));

  // overview
  const tiles = [];
  if (gm) {
    tiles.push(tile("GPS", fix >= 3 ? "3D-Fix" : fix === 2 ? "2D-Fix" : "Kein Fix", `${g.sats_used}/${g.sats_seen} Satelliten`, fix >= 2 ? "ok" : "bad"));
    tiles.push(tile("PPS → PHC", ts ? fmt(ts.offset_ns, 0, " ns") : "–", ts ? `Servo ${ts.servo_state} · ${fmt(ts.freq_ppb, 0, " ppb")}` : "ts2phc liefert keine Daten", ts && ts.servo_state === "s2" ? "ok" : "warn"));
  } else if (d.mode === "measure") {
    tiles.push(tile("Offset zum GM", p ? fmt(p.master_offset_ns, 0, " ns") : "–", p ? "Port " + p.port_state : "kein ptp4l", ps === "SLAVE" ? "ok" : "warn"));
    tiles.push(tile("Pfadlaufzeit", p ? fmt(p.path_delay_ns, 0, " ns") : "–", ""));
  }
  tiles.push(tile("PTP-Port", ps || "–", p ? "Domain " + fmt(p.domain) : "ptp4l nicht aktiv", pOk ? "ok" : "warn"));
  tiles.push(tile("Grandmaster", p?.gm_identity || "–", p ? "Klasse " + fmt(p.gm_clock_class) : ""));
  tiles.push(tile("Netzwerk " + n.name, n.link ? n.speed || "Link" : "Kein Link", ((n.addrs || []).join(", ") || "keine IP") + " · " + (n.hw_timestamping ? "HW-Zeitstempel" : "SW-Zeitstempel"), n.link ? "ok" : "bad"));
  $("#ovTiles").replaceChildren(...tiles);
  $("#ovSkyInfo").textContent = `${g.sats_used}/${g.sats_seen} genutzt`; skyplot("#cvOvSky", g.satellites || []);
  const k1 = gm ? "pps_offset" : "offset";
  $("#ovChartTitle").textContent = gm ? "GPS-PPS → PHC Offset (ts2phc)" : "Offset zum Grandmaster";
  lineChart("#cvOv", [{ data: HIST.filter((s) => s[k1] != null).map((s) => [s.t, s[k1]]), color: C("--green") }]);
  $("#ovLegend").replaceChildren(el("span", {}, el("i", { style: "background:var(--green)" }), gm ? "PPS-Offset (ns)" : "Offset (ns)"));
  if (tab === "ov") renderNics();

  if (tab === "ptp") renderPtp(); if (tab === "gps") renderGps(); if (tab === "time") renderTime();
}
let NICS = [];
function renderNics() {
  $("#tNics").replaceChildren(el("tr", {}, ...["Name", "IP-Adressen", "Treiber", "Link", "Speed", "HW-Zeitstempel", "PHC", "Pins / EXTTS / PEROUT"].map((x) => el("th", {}, x))),
    ...NICS.map((i) => el("tr", {}, ...[i.name, (i.addrs || []).join(", ") || "–", i.driver || "–", i.link ? "ja" : "nein", i.speed || "–", i.hw_timestamping ? "ja" : "nein",
      i.phc_index != null ? "/dev/ptp" + i.phc_index : "–", i.phc ? `${(i.phc.pins || []).join(",") || "–"} / ${i.phc.n_external_timestamps} / ${i.phc.n_periodic_outputs}` : "–"].map((x) => el("td", {}, String(x))))));
}
function renderPtp() {
  const p = ST.ptp, gm = ST.mode === "grandmaster", raw = p?.raw || {};
  $("#ptpInfo").textContent = p ? "Domain " + fmt(p.domain) : "ptp4l nicht aktiv";
  $("#ptpTiles").replaceChildren(
    tile("Port", p?.port_state || "–", gm ? "erwartet MASTER" : "erwartet SLAVE", p && (gm ? p.port_state === "MASTER" : p.port_state === "SLAVE") ? "ok" : "warn"),
    tile("Offset", p ? fmt(p.master_offset_ns, 0, " ns") : "–", "zum Master"), tile("Pfadlaufzeit", p ? fmt(p.path_delay_ns, 0, " ns") : "–", "E2E mittlere Laufzeit"),
    tile("Schritte zum GM", p ? fmt(p.steps_removed) : "–", ""), tile("GM-Klasse", p ? fmt(p.gm_clock_class) : "–", gm && ST.gm?.cc ? (ST.gm.locked ? "GPS synchron" : "kein GPS-Lock") : p?.gm_clock_accuracy || ""));
  const key = gm ? "pps_offset" : "offset";
  $("#offTitle").textContent = gm ? "PPS → PHC Offset (ts2phc)" : "Offset zum Grandmaster";
  $("#csv").href = "/api/history.csv?seconds=" + $("#win").value;
  const off = HIST.filter((s) => s[key] != null).map((s) => [s.t, s[key]]);
  lineChart("#cvOff", [{ data: off, color: C("--green") }]);
  histChart("#cvHist", off.map((x) => x[1]));
  lineChart("#cvDelay", [{ data: HIST.filter((s) => s.delay != null).map((s) => [s.t, s.delay]), color: C("--blue") }]);
  const rows = [el("tr", {}, ...["", "N", "Mittel", "σ", "RMS", "Min", "Max", "Spanne"].map((x) => el("th", { class: x ? "num" : "" }, x)))];
  const add = (name, st) => st && rows.push(el("tr", {}, el("td", {}, name), ...[st.n, fmt(st.mean, 1), fmt(st.sd, 1), fmt(st.rms, 1), fmt(st.min), fmt(st.max), fmt(st.pp)].map((x) => el("td", { class: "num" }, String(x)))));
  add("Offset [ns]", stats(HIST.map((s) => s[key]))); add("Pfadlaufzeit [ns]", stats(HIST.map((s) => s.delay)));
  $("#tStats").replaceChildren(...rows);
  const R = (k, f) => Object.entries(raw[k] || {}).map(([a, b]) => [a, b]);
  kv("#tParent", R("PARENT_DATA_SET")); kv("#tTime", R("GRANDMASTER_SETTINGS_NP").concat(R("TIME_STATUS_NP")));
  kv("#tPort", R("PORT_DATA_SET")); kv("#tDefault", R("DEFAULT_DATA_SET")); kv("#tCurrent", R("CURRENT_DATA_SET"));
}
function renderGps() {
  const g = ST.gps, fix = g.mode || 0;
  $("#gpsTiles").replaceChildren(tile("gpsd", g.connected ? "verbunden" : "getrennt", g.device || "", g.connected ? "ok" : "bad"),
    tile("Fix", fix >= 3 ? "3D" : fix === 2 ? "2D" : "keiner", g.time || "", fix >= 2 ? "ok" : "bad"),
    tile("Satelliten", `${g.sats_used} / ${g.sats_seen}`, "genutzt / sichtbar"), tile("HDOP", fmt(g.hdop, 2), "PDOP " + fmt(g.pdop, 2)),
    tile("Zeitfehler", g.ept != null ? fmt(g.ept * 1e9, 0, " ns") : "–", "geschätzt (ept)"));
  skyplot("#cvSky", g.satellites || []); snrChart("#cvSnr", g.satellites || []);
  kv("#tPos", [["Breite", g.lat != null ? fmt(g.lat, 6) + "°" : null], ["Länge", g.lon != null ? fmt(g.lon, 6) + "°" : null], ["Höhe (MSL)", fmt(g.alt, 1, " m")], ["Höhe (HAE)", fmt(g.alt_hae, 1, " m")], ["Geschwindigkeit", fmt(g.speed, 2, " m/s")], ["Kurs", fmt(g.track, 0, "°")]]);
  kv("#tDop", [["HDOP", fmt(g.hdop, 2)], ["VDOP", fmt(g.vdop, 2)], ["PDOP", fmt(g.pdop, 2)], ["TDOP", fmt(g.tdop, 2)], ["GDOP", fmt(g.gdop, 2)], ["Lagefehler (eph)", fmt(g.eph, 1, " m")], ["Höhenfehler (epv)", fmt(g.epv, 1, " m")]]);
  kv("#tDev", [["gpsd", g.connected ? "verbunden" : "getrennt"], ["Gerät", g.device], ["Zeit", g.time], ["PPS über gpsd", g.pps_seen ? "ja" : "nein (PPS läuft über die I210)"]]);
  const rows = [el("tr", {}, ...["PRN", "GNSS", "Elevation", "Azimut", "C/N₀", "genutzt"].map((x) => el("th", {}, x)))];
  [...(g.satellites || [])].sort((a, b) => (b.snr || 0) - (a.snr || 0)).forEach((s) => rows.push(el("tr", {}, ...[s.prn, s.gnss ?? "–", fmt(s.el, 0, "°"), fmt(s.az, 0, "°"), fmt(s.snr, 0, " dB-Hz"), s.used ? "ja" : "nein"].map((x) => el("td", {}, String(x))))));
  $("#tSats").replaceChildren(...rows);
}
function renderTime() {
  const c = ST.chrony || {}, ts = ST.ts2phc;
  $("#timeTiles").replaceChildren(
    tile("Systemzeit", c["Stratum"] ? "Stratum " + c["Stratum"] : "–", c["Reference ID"] || "chrony nicht aktiv"),
    tile("System-Offset", c["Last offset"] || "–", "letzter Offset"), tile("RMS", c["RMS offset"] || "–", ""),
    tile("PPS-Servo", ts ? ts.servo_state : "–", ts ? fmt(ts.freq_ppb, 0, " ppb") : "", ts && ts.servo_state === "s2" ? "ok" : "warn"));
  lineChart("#cvPps", [{ data: HIST.filter((s) => s.pps_offset != null).map((s) => [s.t, s.pps_offset]), color: C("--green") }]);
  kv("#tTrack", Object.entries(c));
  const rows = [el("tr", {}, ...["", "Name", "Stratum", "Reach", "LastRx", "Offset"].map((x) => el("th", {}, x)))];
  (ST.chrony_sources || []).forEach((s) => rows.push(el("tr", {}, ...[s.mode + s.state, s.name, fmt(s.stratum), s.reach, fmt(s.last_rx, 0, " s"), fmt(s.offset * 1e9, 0, " ns")].map((x) => el("td", {}, String(x))))));
  $("#tSrc").replaceChildren(...rows);
  kv("#tSvc", Object.entries(ST.services || {}));
}

/* ---------------- settings ---------------- */
const SCHEMA = [
  ["GPS (NEO-6M)", "gps", [["device", "Serielle Schnittstelle", "text"], ["baud", "Baudrate", "enum", [4800, 9600, 19200, 38400, 57600, 115200], "NEO-6M: 9600"]]],
  ["PPS-Eingang (I210)", "pps", [["interface", "Netzwerkkarte", "text"], ["pin", "SDP-Pin (0–3)", "num", 0, "I210: GPS-PPS liegt an SDP1 (SDP0 ist mit GPIO18 des Pi verbunden)"], ["channel", "EXTTS-Kanal (0–1)", "num"], ["polarity", "Flanke", "enum", ["rising", "falling", "both"], "I210: immer „both“ (sie stempelt beide Flanken)"], ["pulsewidth_ns", "Pulsbreite [ns]", "num", 0, "NEO-6M: 100 ms = 100000000"],
    ["correction_ns", "Kompensation [ns]", "num", 0, "Kabel-/Antennenlaufzeit"], ["source", "Zeit der Sekunde", "enum", ["nmea", "generic"], "nmea: von GPS; generic: Systemzeit (muss auf ±50 ms stimmen)"], ["nmea_delay_ms", "NMEA-Verzögerung [ms]", "num", 0, "RMC-Ankunft nach dem PPS; messen mit: python3 -m ptpgm nmea-delay"]]],
  ["Grandmaster (Sender)", "ptp", [["preset", "Profil-Vorlage", "preset", 0, "füllt Domain, Transport, Delay und Intervalle (auch für das Messgerät)"], ["interface", "Netzwerkkarte", "text"], ["domain", "Domain", "num"], ["transport", "Transport", "enum", ["UDPv4", "UDPv6", "L2"]], ["delay_mechanism", "Delay-Mechanismus", "enum", ["E2E", "P2P"]],
    ["time_stamping", "Zeitstempel", "enum", ["hardware", "software"]], ["priority1", "priority1", "num"], ["priority2", "priority2", "num"], ["auto_clock_class", "clockClass automatisch (6 / 7 / 248)", "bool"], ["clock_class", "clockClass (fest)", "num", 0, "nur ohne Automatik"],
    ["clock_accuracy", "clockAccuracy", "text", 0, "0x20 = 25 ns, 0x21 = 100 ns"], ["time_source", "timeSource", "text", 0, "0x20 = GNSS"], ["utc_offset", "TAI−UTC [s]", "num"],
    ["log_announce_interval", "logAnnounceInterval", "num"], ["log_sync_interval", "logSyncInterval", "num"], ["log_min_delay_req_interval", "logMinDelayReqInterval", "num"], ["announce_receipt_timeout", "announceReceiptTimeout", "num"]]],
  ["Messgerät (Empfänger)", "measure", [["interface", "Netzwerkkarte", "text"], ["domain", "Domain", "num"], ["transport", "Transport", "enum", ["UDPv4", "UDPv6", "L2"]], ["delay_mechanism", "Delay-Mechanismus", "enum", ["E2E", "P2P"]],
    ["time_stamping", "Zeitstempel", "enum", ["hardware", "software"]], ["announce_receipt_timeout", "announceReceiptTimeout", "num"], ["free_running", "Lokale Uhr nicht nachführen", "bool"]]],
  ["System", "system", [["discipline", "Systemzeit-Regler", "enum", ["chrony", "phc2sys", "none"], "chrony: PHC der I210 als Refclock (GPS); phc2sys: wie bisher; none: Systemzeit unverändert"], ["ntp_server", "NTP-Server (chrony)", "bool"], ["ntp_allow", "NTP erlaubt für", "text", 0, "leer = Subnetz der PTP-Karte, sonst z. B. 192.168.0.0/16"]]],
  ["Web-UI (Neustart nötig)", "web", [["bind", "Adresse", "text"], ["port", "Port", "num"]]],
];
let PRESETS = {};
const PRESET_MAP = ["domain", "transport", "delay_mechanism", "log_announce_interval", "log_sync_interval", "log_min_delay_req_interval", "announce_receipt_timeout"];
function applyPreset(name) {
  const pr = PRESETS[name]; if (!pr) return;
  for (const k of PRESET_MAP) {
    for (const sec of ["ptp", "measure"]) {
      const i = document.querySelector(`#fSet [data-sec="${sec}"][data-key="${k}"]`); if (!i || pr[k] == null) continue;
      i.value = String(pr[k]);
    }
  }
}
async function loadSettings() {
  try { cfg = await api("/api/config"); PRESETS = await api("/api/presets"); } catch (e) { toast(e.message, true); return; }
  $("#fSet").replaceChildren(...SCHEMA.map(([title, sec, fields]) => el("fieldset", {}, el("legend", {}, title), el("div", { class: "row" }, ...fields.map(([key, label, type, opts, hint]) => {
    const v = cfg[sec][key]; let inp;
    if (type === "preset") {
      inp = el("select", {}, [...Object.entries(PRESETS).map(([k, p]) => [k, p.label]), ["custom", "Benutzerdefiniert"]].map(([k, l]) => { const x = el("option", { value: k }, l); if (k === v) x.selected = true; return x; }));
      inp.addEventListener("change", () => applyPreset(inp.value));
    } else if (type === "enum") inp = el("select", {}, (opts).map((o) => { const x = el("option", { value: String(o) }, String(o)); if (o === v) x.selected = true; return x; }));
    else if (type === "bool") { inp = el("input", { type: "checkbox" }); inp.checked = !!v; }
    else if (type === "num") inp = el("input", { type: "number", value: v, step: 1 });
    else inp = el("input", { type: "text", value: v ?? "" });
    Object.assign(inp.dataset, { sec, key, type });
    return el("label", { class: "f" + (type === "bool" ? " cb" : "") }, ...(type === "bool" ? [inp, label] : [label, inp]), hint ? el("span", { class: "h" }, hint) : null);
  })))));
  const pre = document.querySelector('#fSet [data-type="preset"]');
  document.querySelectorAll("#fSet [data-key]").forEach((i) => { if (PRESET_MAP.includes(i.dataset.key) && ["ptp", "measure"].includes(i.dataset.sec)) i.addEventListener("input", () => { if (pre) pre.value = "custom"; }); });
  loadRender();
}
async function loadRender() {
  try { const r = await api("/api/render"); $("#prePtp").textContent = r.ptp4l; $("#preTs").textContent = r.ts2phc; $("#prePhc").textContent = r.phc2sys; } catch {}
}
function collect() {
  const out = JSON.parse(JSON.stringify(cfg));
  document.querySelectorAll("#fSet [data-key]").forEach((i) => { const { sec, key, type } = i.dataset;
    let v = type === "bool" ? i.checked : type === "num" ? Number(i.value) : i.value; if (type === "enum" && /^\d+$/.test(v)) v = Number(v); out[sec][key] = v; });
  return out;
}
async function save(apply) {
  try {
    cfg = await api("/api/config", collect()); $("#setMsg").textContent = "Gespeichert."; loadRender();
    if (apply) { const r = await api("/api/apply", {}); toast("Angewendet: " + r.log.join(", ")); }
  } catch (e) { $("#setMsg").textContent = ""; toast(e.message, true); }
}
$("#btnSave").addEventListener("click", () => save(false));
$("#btnApply").addEventListener("click", () => save(true));
$("#btnPw").addEventListener("click", async () => { try { await api("/api/password", { password: $("#newpw").value }); $("#newpw").value = ""; toast("Passwort geändert."); } catch (e) { toast(e.message, true); } });
$("#btnCheck").addEventListener("click", async () => {
  try { const r = await api("/api/check");
    $("#checkOut").replaceChildren(...r.map((i) => el("div", { class: "chk" }, el("span", { class: "dot " + (i.ok ? "ok" : "bad"), style: "margin-top:6px" }), el("b", {}, i.name), el("span", { class: "muted" }, i.detail)))); }
  catch (e) { toast(e.message, true); }
});

/* ---------------- logs ---------------- */
async function loadLog() { try { const r = await api("/api/logs?unit=" + $("#logUnit").value); $("#rawLog").textContent = r.log || "(leer)"; } catch (e) { toast(e.message, true); } }
$("#btnLog").addEventListener("click", loadLog); $("#logUnit").addEventListener("change", loadLog);

/* ---------------- polling ---------------- */
let busy = false;
async function poll(histOnly) {
  if (busy) return; busy = true;
  try {
    if (!histOnly) { ST = await api("/api/status"); dot("#pConn", "ok"); }
    if (!histOnly || true) HIST = await api("/api/history?seconds=" + (tab === "ptp" ? $("#win").value : 600));
    if (!NICS.length || Date.now() - (poll.nics || 0) > 15000) { NICS = await api("/api/interfaces"); poll.nics = Date.now(); }
    render();
  } catch (e) { dot("#pConn", "bad"); } finally { busy = false; }
}
poll(); setInterval(() => { if (!document.hidden) poll(); }, 1500);
window.addEventListener("resize", render);

// deep links: /#ptp, /#gps, /#time, /#set, /#log
const hashTab = location.hash.slice(1);
if (hashTab) document.querySelector(`#nav button[data-tab="${hashTab}"]`)?.click();
