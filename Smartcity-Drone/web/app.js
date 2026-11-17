"use strict";

// Dashboard für den Linienfolger. Der Zustand kommt per Abfrage alle POLL_MS vom Server;
// das Videobild der ausgewählten Drohne läuft als MJPEG-Strom direkt im <img>.

const POLL_MS = 700;
const API_VERSION = 5; // muss zu API_VERSION in webapp.py passen
const KANON_STORE = "kanon";
const TUNING_FIELDS = ["speed", "height", "k_lateral", "k_lateral_d", "k_lateral_i", "k_curve", "video_bitrate"];
const TUNING_STORE = "tuning";
// Pfeiltasten für die Abzweigung (wie a / w / d / l im OpenCV-Fenster)
const ARROW_KEYS = { ArrowLeft: "a", ArrowUp: "w", ArrowRight: "d", ArrowDown: "l" };
const DIRECTION_NAMES = { a: "links", w: "geradeaus", d: "rechts" };

let state = null;
let selectedSerial = null; // ausgewählte Drohne: Ziel für Starten/Landen, Anzeige von Video und Telemetrie
const videoTiles = new Map(); // Flug (Seriennummer + Startzeit) -> Kachel mit laufendem Videostrom
const emptyVideoBox = document.querySelector("#videos .video-box"); // Platzhalter ohne Flug (bleibt erhalten)
let lastLogId = 0;
let tuningDefaults = null;
// Kanon-Modus: Starten, Landen, Abzweigungen und Flips gelten für alle Drohnen
let kanonMode = false;
try { kanonMode = localStorage.getItem(KANON_STORE) === "1"; } catch { /* privates Fenster */ }
// Synchron-Flip: Flip-Knöpfe lassen alle fliegenden Drohnen gleichzeitig flippen
const SYNC_STORE = "syncFlip";
let syncFlip = false;
try { syncFlip = localStorage.getItem(SYNC_STORE) === "1"; } catch { /* privates Fenster */ }

const $ = (selector) => document.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(", ") : body.detail;
    throw new Error(detail || `Fehler ${response.status}`);
  }
  return body;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 4000);
}

// ── Tabs ──
document.querySelectorAll(".tab-btn").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(`tab-${button.dataset.tab}`).classList.add("active");
  });
});

// ── Auswahl ──
function selectedDrone() {
  return state?.drones.find((d) => d.serial === selectedSerial) ?? null;
}

function selectedFlight() {
  return state?.flights.find((f) => f.serial === selectedSerial) ?? null;
}

function select(serial) {
  selectedSerial = serial;
  render();
}

// ── Startoptionen und Regler ──
function loadTuning() {
  try { return JSON.parse(localStorage.getItem(TUNING_STORE)) || {}; } catch { return {}; }
}

function saveTuning() {
  const form = $("#options");
  const values = Object.fromEntries(TUNING_FIELDS.map((name) => [name, form[name].value]));
  try { localStorage.setItem(TUNING_STORE, JSON.stringify(values)); } catch { /* privates Fenster */ }
}

function fillTuning(values) {
  const form = $("#options");
  for (const name of TUNING_FIELDS) {
    if (values[name] !== undefined && values[name] !== "") form[name].value = values[name];
  }
}

function startOptions() {
  const form = $("#options");
  const tuning = {};
  for (const name of TUNING_FIELDS) {
    const field = form[name];
    if (field.value === "") continue; // leer = Standardwert des Servers
    if (!field.checkValidity()) {
      throw new Error(`${field.closest("label").firstChild.textContent.trim()}: ${field.validationMessage}`);
    }
    tuning[name] = Number(field.value);
  }
  return { dashed: form.dashed.checked, record: form.record.checked, random: form.random.checked,
           search_start: form.search_start.checked, ...tuning };
}

// ── Kanon ──
function runningFlights() {
  return (state?.flights ?? []).filter((f) => f.running);
}

function idleDrones() {
  const busy = new Set(runningFlights().map((f) => f.serial));
  return (state?.drones ?? []).filter((d) => !busy.has(d.serial)).sort((a, b) => a.name.localeCompare(b.name));
}

// Wie FleetManager.kanon_heights in webapp.py
function kanonHeights(count, fallback) {
  if (count === 1) return [fallback];
  const [low, high] = state.kanon.heights;
  return Array.from({ length: count }, (_, i) => 5 * Math.round((low + (i * (high - low)) / (count - 1)) / 5));
}

function kanonPlan() {
  const drones = idleDrones();
  const fallback = Number($("#options").height.value) || tuningDefaults?.height || 55;
  const heights = kanonHeights(drones.length, fallback);
  return drones.map((d, i) => ({ name: d.name, height: heights[i], delay: i * state.kanon.delay }));
}

function setSyncFlip(on) {
  syncFlip = on;
  try { localStorage.setItem(SYNC_STORE, on ? "1" : "0"); } catch { /* privates Fenster */ }
  render();
}

async function syncFlipAll(key) {
  const flying = runningFlights().filter((f) => f.flying && f.kind !== "cool");
  if (!flying.length) return toast("Synchron-Flip nur im Flug – keine Drohne fliegt.");
  const low = flying.filter((f) => f.battery != null && f.battery < 60).map((f) => f.name);
  if (low.length) toast(`Akku unter 60 % bei ${low.join(", ")} – diese flippen nicht mit.`);
  try {
    const { drones } = await api("/api/sync-flip", { method: "POST", body: JSON.stringify({ key: `sync-${key}` }) });
    toast(`Synchron-Flip: ${drones.join(", ")} steigen und flippen gleichzeitig.`);
  } catch (error) {
    toast(error.message);
  }
}

function setKanonMode(on) {
  kanonMode = on;
  try { localStorage.setItem(KANON_STORE, on ? "1" : "0"); } catch { /* privates Fenster */ }
  render();
}

async function startKanon() {
  if (state.kanon.starting) return toast("Der Kanon startet gerade schon.");
  const plan = kanonPlan();
  if (!plan.length) return toast("Keine freie Drohne gefunden – erst suchen.");
  let options;
  try {
    options = startOptions();
  } catch (error) {
    return toast(error.message);
  }
  const lines = plan.map((m) => `  ${m.name}: ${m.height} cm, Start nach ${m.delay} s`).join("\n");
  const follow = "\n\nJede Drohne wählt an den Abzweigungen selbst eine zufällige Richtung.";
  if (!confirm(`Kanon mit ${plan.length} Drohne${plan.length === 1 ? "" : "n"} starten?\n\n${lines}${follow}`)) return;
  try {
    await api("/api/kanon", { method: "POST", body: JSON.stringify(options) });
    toast(`Kanon startet: ${plan.map((m) => m.name).join(", ")}`);
    refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function kanonPress(key, what) {
  try {
    const { count } = await api("/api/kanon/key", { method: "POST", body: JSON.stringify({ key }) });
    toast(count ? `${what}: ${count} Drohne${count === 1 ? "" : "n"}` : `${what}: keine Drohne betroffen.`);
  } catch (error) {
    toast(error.message);
  }
}

// ── Aktionen ──
async function start(dryRun) {
  if (kanonMode && !dryRun) return startKanon();
  const drone = selectedDrone();
  if (!drone) return toast("Erst eine Drohne in der Liste auswählen.");
  let options;
  try {
    options = startOptions();
  } catch (error) {
    return toast(error.message);
  }
  const mode = dryRun ? "im Testmodus öffnen (ohne Abheben)" : "abheben lassen";
  if (!confirm(`Drohne ${drone.name} ${mode}?`)) return;
  try {
    await api("/api/flights", { method: "POST", body: JSON.stringify({ drone: drone.serial, dry_run: dryRun, ...options }) });
    toast(`Drohne ${drone.name} startet …`);
    refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function cool() {
  const drone = selectedDrone();
  if (!drone) return toast("Erst eine Drohne in der Liste auswählen.");
  const field = $("#options").cool_target;
  if (!field.checkValidity()) return toast(`Kühlen bis: ${field.validationMessage}`);
  const target = Number(field.value);
  if (!confirm(`Drohne ${drone.name} bis ${target} °C kühlen?\n\nDie Propeller drehen sich langsam, die Drohne hebt nicht ab. `
    + "Hände weg von den Propellern, Drohne flach auf freiem Boden.")) return;
  try {
    await api("/api/cool", { method: "POST", body: JSON.stringify({ drone: drone.serial, target }) });
    toast(`Drohne ${drone.name}: Kühlmodus startet in 5 s …`);
    refresh();
  } catch (error) {
    toast(error.message);
  }
}

async function press(flight, key) {
  try {
    await api(`/api/flights/${encodeURIComponent(flight.serial)}/key`, { method: "POST", body: JSON.stringify({ key }) });
  } catch (error) {
    toast(error.message);
  }
}

async function landAll() {
  try {
    await api("/api/land-all", { method: "POST" });
    toast("Alle Drohnen landen.");
  } catch (error) {
    toast(error.message);
  }
}

function flip(key) {
  if (syncFlip) return syncFlipAll(key);
  if (kanonMode) {
    const flying = runningFlights().filter((f) => f.flying && f.kind !== "cool");
    if (!flying.length) return toast("Flip nur im Flug – keine Drohne fliegt.");
    const low = flying.filter((f) => f.battery != null && f.battery < 60).map((f) => f.name);
    if (low.length) toast(`Akku unter 60 % bei ${low.join(", ")} – deren Flip lehnt die Drohne ab.`);
    return kanonPress(key, "Flip");
  }
  const flight = selectedFlight();
  if (!flight?.running || !flight.flying) return toast("Flip nur im Flug – die ausgewählte Drohne fliegt nicht.");
  if (flight.battery != null && flight.battery < 60) return toast(`Kein Flip: Akku ${flight.battery} %, nötig sind 60 %.`);
  press(flight, key);
}

function chooseDirection(key) {
  if (kanonMode) {
    const waiting = runningFlights().filter((f) => f.prompt.length > 0 && (key === "l" || f.prompt.includes(key)));
    if (!waiting.length) return toast(key === "l" ? "Keine Drohne wartet an einer Abzweigung." : `Keine wartende Drohne kann ${DIRECTION_NAMES[key]}.`);
    return kanonPress(key, key === "l" ? "Landen an der Abzweigung" : `Richtung ${DIRECTION_NAMES[key]}`);
  }
  const flight = selectedFlight();
  const waiting = flight?.running && flight.prompt.length > 0;
  if (!waiting) return toast("Die ausgewählte Drohne wartet an keiner Abzweigung.");
  if (key !== "l" && !flight.prompt.includes(key)) return toast(`Hier geht es nicht ${DIRECTION_NAMES[key]}.`);
  press(flight, key);
}

// ── Anzeige ──
function batteryClass(percent) {
  if (percent == null) return "";
  return percent > 40 ? "ok" : percent > 20 ? "warn" : "bad";
}

const show = (id, value) => { $(id).textContent = value ?? "—"; };

function activityLabel(flight) {
  if (flight.kind === "cool") return "kühlt";
  return flight.flying ? "fliegt" : "läuft";
}

function renderHeader() {
  const count = state.drones.length;
  $("#conn-badge").className = `status-badge ${count ? "connected" : "disconnected"}`;
  $("#conn-text").textContent = count ? `${count} Drohne${count === 1 ? "" : "n"} online` : "Keine Drohne";
  $("#btn-emergency").hidden = !state.flights.some((f) => f.running);
}

function renderDrones() {
  const running = new Map(state.flights.filter((f) => f.running).map((f) => [f.serial, f]));
  if (!selectedSerial || !state.drones.some((d) => d.serial === selectedSerial)) {
    // Ohne Auswahl: zuerst eine fliegende, sonst die erste gefundene Drohne
    selectedSerial = (state.flights.find((f) => f.running) ?? state.drones[0])?.serial ?? selectedSerial;
  }
  $("#drones").replaceChildren(...state.drones.map((drone) => el("button", {
    type: "button",
    class: `drone-row${drone.serial === selectedSerial ? " selected" : ""}`,
    onclick: () => select(drone.serial),
  },
  el("span", { class: "name" }, drone.name),
  el("span", { class: "ip" }, drone.ip),
  el("span", { class: "spacer" }),
  running.has(drone.serial) ? el("span", { class: "pill ok" }, activityLabel(running.get(drone.serial))) : "",
  el("span", { class: `pill ${batteryClass(drone.battery)}` }, drone.battery == null ? "Akku ?" : `${drone.battery} %`),
  )));
  if (document.activeElement !== $("#network") && state.network) $("#network").value = state.network;
  const when = state.scanned_at ? new Date(state.scanned_at * 1000).toLocaleTimeString("de-DE") : null;
  $("#scan-info").textContent = !when
    ? "Suche läuft …"
    : state.drones.length
      ? `${state.drones.length} gefunden in ${state.network} – zuletzt gesucht ${when}. Drohne anklicken zum Auswählen.`
      : `Keine Drohne in ${state.network} (${when}) – eingeschaltet und im WLAN? Status/Video müssen vom Drohnennetz zum Rechner durchkommen.`;
}

function renderStatus(drone, flight) {
  const running = flight?.running;
  show("#status-text", flight ? flight.status : drone ? "Bereit" : "Offline");
  $("#flying-text").textContent = running && flight.flying ? "Ja" : "Nein";
  $("#flying-text").style.color = running && flight.flying ? "var(--success)" : "var(--muted)";
  show("#status-batt", (running ? flight.battery : drone?.battery) ?? "—");
  $("#status-drone").textContent = drone
    ? `Drohne ${drone.name} (${drone.ip})${flight?.error ? ` – ${flight.error}` : ""}`
    : "Keine Drohne ausgewählt.";
  $("#status-drone").style.color = flight?.error ? "var(--danger)" : "";
  const cooling = running && flight.kind === "cool";
  $("#btn-start").disabled = $("#btn-test").disabled = $("#btn-cool").disabled = !drone || !!running;
  $("#btn-land").disabled = !running;
  $("#btn-land").textContent = cooling ? "Kühlen stoppen" : "Landen";
  $("#btn-start").textContent = "Starten";
  $("#btn-kanon").classList.toggle("kanon-on", kanonMode);
  $("#btn-kanon").setAttribute("aria-pressed", String(kanonMode));
  if (kanonMode) {
    const idle = idleDrones().length;
    $("#btn-start").disabled = !idle || state.kanon.starting;
    $("#btn-start").textContent = idle ? `Kanon starten (${idle})` : "Kanon starten";
    $("#btn-test").disabled = true;
    $("#btn-land").disabled = !runningFlights().length;
    $("#btn-land").textContent = "Alle landen";
  }
  renderKanonInfo();
}

function renderKanonInfo() {
  const info = $("#kanon-info");
  info.hidden = !kanonMode;
  if (!kanonMode) return;
  const running = new Set(runningFlights().map((f) => f.serial));
  const active = state.kanon.members.filter((m) => running.has(m.serial));
  const describe = (list) => list.map((m) => `${m.name} ${m.height} cm (+${m.delay} s)`).join(" · ");
  const plan = kanonPlan();
  info.textContent = "Kanon-Modus: Landen und Flips gelten für alle Drohnen, an Abzweigungen wählt jede selbst. "
    + (active.length || state.kanon.starting
      ? `Im Kanon: ${describe(state.kanon.members)}.`
      : plan.length ? `Start: ${describe(plan)}.` : "Keine freie Drohne gefunden.");
}

function renderControl(flight) {
  $("#flight-chips").replaceChildren(...state.flights.filter((f) => f.running).map((f) => el("button", {
    type: "button",
    class: `btn-ghost${f.serial === selectedSerial ? " highlight" : ""}`,
    onclick: () => select(f.serial),
  }, `Drohne ${f.name}${f.kind === "cool" ? " ❄" : f.prompt.length ? " ⚠" : ""}`)));

  renderVideos(flight);

  const waitingFlights = kanonMode ? runningFlights().filter((f) => f.prompt.length > 0)
    : flight?.running && flight.prompt.length > 0 ? [flight] : [];
  const waiting = waitingFlights.length > 0;
  document.querySelectorAll(".control-pad [data-key]").forEach((button) => {
    const k = button.dataset.key;
    button.disabled = !waiting || (k !== "l" && !waitingFlights.some((f) => f.prompt.includes(k)));
  });
  const canFlip = kanonMode || syncFlip ? runningFlights().some((f) => f.flying && f.kind !== "cool")
    : flight?.running && flight.flying && !(flight.battery != null && flight.battery < 60);
  document.querySelectorAll("[data-flip]").forEach((button) => { button.disabled = !canFlip; });
  $("#btn-sync-flip").classList.toggle("kanon-on", syncFlip);
  $("#btn-sync-flip").setAttribute("aria-pressed", String(syncFlip));
  $("#flip-state").textContent = syncFlip
    ? "Synchron: alle fliegenden Drohnen zugleich · ab 60 % Akku"
    : "ab 60 % Akku · steigt dafür auf 1 m";
  const padState = $("#pad-state");
  padState.textContent = !waiting ? "Keine Abzweigung"
    : kanonMode ? `Abzweigung – ${waitingFlights.map((f) => f.name).join(", ")} wartet` : "Abzweigung – bitte wählen";
  padState.classList.toggle("waiting", !!waiting);
}

// Kamerabilder aller laufenden Flüge nebeneinander. Jede Kachel behält ihren Videostrom, solange
// der Flug läuft – nur neue Flüge verbinden sich neu. Ohne laufenden Flug bleibt das letzte Bild
// der ausgewählten Drohne stehen.
function renderVideos(flight) {
  let shown = runningFlights().filter((f) => f.kind !== "cool");
  if (!shown.length && flight && flight.kind !== "cool") shown = [flight];
  shown = [...shown].sort((a, b) => a.name.localeCompare(b.name));
  const keys = new Set();
  const tiles = shown.map((f) => {
    const key = `${f.serial}-${f.started_at}`;
    keys.add(key);
    let tile = videoTiles.get(key);
    if (!tile) {
      const img = el("img", { alt: `Kamerabild Drohne ${f.name}`,
        src: `/api/flights/${encodeURIComponent(f.serial)}/video?t=${Date.now()}` });
      const name = el("span", { class: "name" });
      const meta = el("span", { class: "meta" });
      const status = el("div", { class: "video-status" });
      const node = el("button", { type: "button", class: "video-tile", onclick: () => select(f.serial) },
        el("div", { class: "video-label" }, name, meta), el("div", { class: "video-box" }, img), status);
      tile = { node, img, name, meta, status };
      videoTiles.set(key, tile);
    }
    tile.name.textContent = `Drohne ${f.name}`;
    const height = f.height_cm == null ? "— cm" : `${f.height_cm} cm`;
    const battery = f.battery == null ? "" : ` · ${f.battery} %`;
    tile.meta.textContent = f.running ? `${height}${battery}` : "beendet";
    tile.status.textContent = f.error ? f.error : f.status;
    tile.status.classList.toggle("waiting", f.prompt.length > 0);
    tile.node.classList.toggle("selected", f.serial === selectedSerial);
    return tile.node;
  });
  for (const [key, tile] of videoTiles) {
    if (!keys.has(key)) {
      tile.img.removeAttribute("src"); // Videostrom schließen
      videoTiles.delete(key);
    }
  }
  const empty = emptyVideoBox.querySelector(".video-empty");
  empty.textContent = flight?.kind === "cool"
    ? "Kühlmodus – kein Kamerabild. Propeller drehen, die Drohne hebt nicht ab."
    : "Kein Flug ausgewählt – Drohne starten oder im Testmodus öffnen.";
  const container = $("#videos");
  const wanted = tiles.length ? tiles : [emptyVideoBox];
  if (wanted.length !== container.children.length || wanted.some((node, i) => container.children[i] !== node)) {
    container.replaceChildren(...wanted);
  }
}

function renderSystem(drone, flight) {
  const battery = flight?.running ? flight.battery : drone?.battery;
  show("#batt-val", battery);
  $("#batt-bar").style.width = `${battery || 0}%`;
  $("#batt-bar").style.background = battery > 40 ? "var(--success)" : battery > 20 ? "var(--warning)" : "var(--danger)";
  show("#alt-val", flight?.height_cm);
  show("#spd-val", flight?.speed_cm_s);
  show("#hdg-val", flight?.heading_deg);
  show("#temp-val", flight?.temp_c == null ? null : Math.round(flight.temp_c));
  const [lr, fb, yaw] = flight?.command ?? [0, 0, 0];
  show("#cmd-lr", lr);
  show("#cmd-fb", fb);
  show("#cmd-yaw", yaw);
  show("#line-offset", flight?.offset == null ? null : flight.offset.toFixed(2));
  show("#last-event", flight?.last_event?.msg);
  show("#telem-time", flight?.last_event?.time?.slice(11));
  $("#conn-detail").textContent = flight?.running
    ? `${flight.host} · Video ${flight.video_port}`
    : drone ? `${drone.ip} · bereit` : "Offline";
}

function appendLog(entries) {
  if (!entries.length) return;
  const container = $("#log-container");
  container.querySelector(".empty-log")?.remove();
  for (const entry of entries) {
    lastLogId = Math.max(lastLogId, entry.id);
    container.append(el("div", { class: "log-entry" },
      el("span", { class: "log-time" }, entry.time),
      el("span", { class: `log-level ${entry.level}` }, entry.level),
      el("span", { class: "log-msg" }, entry.drone ? `[${entry.drone}] ${entry.msg}` : entry.msg),
    ));
  }
  if ($("#auto-scroll").checked) container.scrollTop = container.scrollHeight;
}

function render() {
  if (!state) return;
  renderHeader();
  renderDrones();
  const drone = selectedDrone();
  const flight = selectedFlight();
  renderStatus(drone, flight);
  renderControl(flight);
  renderSystem(drone, flight);
}

async function refresh() {
  try {
    const next = await api(`/api/state?since=${lastLogId}`);
    if (next.api_version !== API_VERSION) {
      toast("Der Webserver läuft noch mit einer alten Version – im Terminal Strg+C drücken, python webapp.py neu starten und die Seite neu laden.");
      return;
    }
    state = next;
    if (!tuningDefaults) {
      tuningDefaults = state.defaults;
      fillTuning({ ...tuningDefaults, ...loadTuning() });
    }
    appendLog(state.log);
    render();
  } catch (error) {
    toast(`Server nicht erreichbar: ${error.message}`);
  }
}

// ── Ereignisse ──
$("#scan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#btn-scan");
  button.disabled = true;
  button.textContent = "Suche …";
  try {
    const network = $("#network").value.trim();
    const result = await api("/api/scan", { method: "POST", body: JSON.stringify({ network: network || null }) });
    state = { ...result, log: [] };
    render();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Suchen";
  }
});

$("#btn-start").addEventListener("click", () => start(false));
$("#btn-test").addEventListener("click", () => start(true));
$("#btn-land").addEventListener("click", () => {
  if (kanonMode) return kanonPress("q", "Landen");
  const flight = selectedFlight();
  if (flight) press(flight, "q");
});
$("#btn-kanon").addEventListener("click", () => setKanonMode(!kanonMode));
$("#btn-sync-flip").addEventListener("click", () => setSyncFlip(!syncFlip));
$("#btn-land-all").addEventListener("click", landAll);
$("#btn-cool").addEventListener("click", cool);
$("#btn-emergency").addEventListener("click", landAll);
document.querySelectorAll(".control-pad [data-key]").forEach((button) => {
  button.addEventListener("click", () => chooseDirection(button.dataset.key));
});
document.querySelectorAll("[data-flip]").forEach((button) => {
  button.addEventListener("click", () => flip(button.dataset.flip));
});

$("#options").addEventListener("input", saveTuning);
$("#reset-tuning").addEventListener("click", () => {
  if (!tuningDefaults) return;
  fillTuning(tuningDefaults);
  saveTuning();
});

$("#btn-clear-log").addEventListener("click", async () => {
  try {
    await api("/api/log/clear", { method: "POST" });
    $("#log-container").replaceChildren(el("div", { class: "empty-log" }, "Log geleert"));
  } catch (error) {
    toast(error.message);
  }
});

$("#btn-download-log").addEventListener("click", () => {
  const lines = [...document.querySelectorAll(".log-entry")].map((entry) => entry.innerText.replace(/\n/g, " "));
  const link = el("a", {
    href: URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/plain" })),
    download: `drohnen_log_${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.txt`,
  });
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000); // erst nach dem Start des Downloads freigeben
});

document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, textarea, select") || event.repeat) return;
  if (event.key === "Escape") {
    if (state?.flights.some((f) => f.running)) landAll();
    return;
  }
  if (event.key === "f" || event.key === "F") {
    flip("flip-b");
    return;
  }
  const key = ARROW_KEYS[event.key];
  if (!key) return;
  event.preventDefault(); // Seite nicht scrollen
  // Wartet nur eine Drohne an einer Abzweigung, geht die Taste an sie – sonst an die ausgewählte.
  const waiting = (state?.flights ?? []).filter((f) => f.running && f.prompt.length > 0);
  if (!kanonMode && waiting.length === 1 && waiting[0].serial !== selectedSerial) select(waiting[0].serial);
  chooseDirection(key);
});

refresh();
setInterval(refresh, POLL_MS);
