// ui.js — tab shell, toasts, connection banner, small DOM helpers.

import { bus, send } from "./ws.js";
import { state, watch, store } from "./state.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** el("li.dev", {onclick}, "text", child...) */
export function el(spec, attrs, ...kids) {
  const [tag, ...cls] = spec.split(".");
  const n = document.createElement(tag || "div");
  if (cls.length) n.className = cls.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c instanceof Node ? c : String(c));
  return n;
}

export function toast(msg, level = "info", ms = 3500) {
  const t = el("div.toast." + level, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
}

export const fmtTime = (d = new Date()) =>
  d.toTimeString().slice(0, 8) + "." + String(d.getMilliseconds()).padStart(3, "0");

export function hexSpaced(h) {
  return (h || "").replace(/\s+/g, "").replace(/(..)(?=.)/g, "$1 ").toUpperCase();
}

/** Keep a list/table body to at most `max` children (drops oldest = last). */
export function trim(parent, max) {
  while (parent.children.length > max) parent.lastElementChild.remove();
}

/** Copy text to the clipboard. Falls back to execCommand on plain-http pages (phones on the LAN),
 *  where navigator.clipboard is unavailable. Returns true on success. */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through */ }
  const ta = el("textarea", { readonly: true, style: "position:fixed;top:-1000px;opacity:0" });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

/** Offer `text` as a file download. */
export function downloadText(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: type + ";charset=utf-8" }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function logLine(list, msg, level = "info", max = 300) {
  list.prepend(el("li." + level, `${fmtTime()}  ${msg}`));
  trim(list, max);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
const tabInits = new Map();
let current = null;

/** Tabs are initialised eagerly so their bus handlers (key counting, logs) run even while hidden. */
export function registerTab(name, init) {
  tabInits.set(name, { init, done: true });
  init($("#tab-" + name));
}

export function showTab(name) {
  const btn = $(`.tabs button[data-tab="${name}"]`);
  if (!btn) return;
  current = name;
  for (const b of $$(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const p of $$(".panel")) p.hidden = p.id !== "tab-" + name;
  const t = tabInits.get(name);
  if (t && !t.done) { t.done = true; t.init($("#tab-" + name)); }
  bus.emit("tab", { name });
  store.set("tab", name);
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}

export const currentTab = () => current;

// The app drives only the xiao-test-tool firmware, so every tab is always available;
// the hint line explains why a command would fail (no bridge, no board, Nordic image).
function renderHint() {
  const hint = $("#mode-hint");
  const b = state.bridge;
  hint.hidden = false;
  hint.className = "mode-hint";
  if (!state.ws) {
    hint.textContent = "Connecting to the bridge… (is `python backend/bridge.py` running?)";
  } else if (b.reason === "nordic-sniffer-image") {
    hint.className = "mode-hint warn";
    hint.textContent = "";
    hint.append(
      el("span", "The XIAO is running the ", el("b", "Nordic sniffer"), " image. This app needs ", el("b", "xiao-test-tool"),
        " — flash it, or close the app and use Wireshark (README ▸ Passive capture) for passive capture. "),
      el("button.btn.small", { onclick: () => { if (confirm("Flash xiao-test-tool? Double-tap RST on the XIAO when asked.")) send({ cmd: "flash", target: "tool" }); } },
        "Flash xiao-test-tool"));
  } else if (!b.connected) {
    hint.textContent = "Bridge is up but no board is connected. Plug in the XIAO running xiao-test-tool; it is detected automatically.";
  } else {
    hint.hidden = true;
  }
}

function initTabs() {
  const want = location.hash.slice(1) || store.get("tab", "connect");
  showTab($(`.tabs button[data-tab="${want}"]`) ? want : "connect");
}

// ---------------------------------------------------------------------------
// Banner
// ---------------------------------------------------------------------------
function renderBanner() {
  const b = state.bridge;
  const pill = $("#ws-pill");
  pill.className = "link-pill " + (!state.ws ? "" : b.connected ? "on" : "wait");
  pill.lastElementChild.textContent = !state.ws ? "offline" : b.connected ? "board online"
    : b.reason === "nordic-sniffer-image" ? "sniffer image" : "no board";
  $("#st-mode").textContent = !state.ws ? "—" : b.connected ? (b.simulated ? "simulated" : "jig online")
    : b.reason === "nordic-sniffer-image" ? "sniffer image" : "none";
  $("#st-fw").textContent = b.fw ? `${b.fw}${b.ver && b.ver !== "None" ? " " + b.ver : ""}` : "—";
  $("#st-port").textContent = b.port || "—";
  const l = state.link;
  const linkEl = $("#st-link");
  linkEl.textContent = !b.connected ? "—" : !l.conn ? (l.stage || "idle")
    : l.live === false ? "silent ⚠" : !l.secured ? l.stage
    : l.live === true ? "bonded ✓ live" : l.stage === "ready" ? "bonded ✓ · press a key" : l.stage;
  linkEl.title = l.conn && l.live === false
    ? "Connected, but the remote isn't sending. It only sends on a link made in pairing mode: put it in pairing mode (STB off)."
    : l.conn && l.secured && l.live === undefined ? "Bonded. Press any key on the remote to confirm the link carries key presses." : "";
  $("#st-rssi").textContent = l.conn && l.rssi !== undefined ? `${l.rssi} dBm` : "—";
}

// Soft early warning only (brief §12): the cell's own PCM is the real over-discharge protection.
export const BATT_WARN_MV = 3400;
let battWarned = false;

function renderBattery() {
  const j = state.battery.jig;
  const g = $("#st-batt-gauge");
  if (!j) { $("#st-batt").textContent = "—"; g.firstElementChild.style.width = "0"; return; }
  const low = j.mv < BATT_WARN_MV;
  $("#st-batt").textContent = `${(j.mv / 1000).toFixed(2)} V · ${j.pct}%${low ? " · LOW" : ""}`;
  g.firstElementChild.style.width = Math.max(2, Math.min(100, j.pct)) * 0.22 + "px";
  g.className = "batt" + (low || j.pct < 15 ? " low" : j.pct < 40 ? " mid" : "");
  g.title = low ? `Jig battery below ${BATT_WARN_MV / 1000} V — charge it over USB-C` : "";
  if (low && !battWarned) toast(`Jig battery low (${(j.mv / 1000).toFixed(2)} V) — charge over USB-C`, "warn", 8000);
  battWarned = low ? true : j.mv > BATT_WARN_MV + 100 ? false : battWarned;
}

// Remote's own Battery Service (0x180F / 0x2A19): read on connect, then notifications on change.
function renderRemoteBattery() {
  const r = state.battery.remote;
  const g = $("#st-rbatt-gauge");
  const out = $("#st-rbatt");
  if (!r) { out.textContent = "—"; g.firstElementChild.style.width = "0"; g.className = "batt"; g.title = ""; return; }
  const live = !!state.link.conn;
  out.textContent = `${r.pct}%` + (live ? "" : " (last)");
  g.firstElementChild.style.width = Math.max(2, Math.min(100, r.pct)) * 0.22 + "px";
  g.className = "batt" + (r.pct < 15 ? " low" : r.pct < 40 ? " mid" : "") + (live ? "" : " stale");
  const when = r.at ? new Date(r.at).toLocaleTimeString() : "";
  g.title = out.title = `${r.via === "notify" ? "Notified by the remote" : "Read from the remote"} at ${when}` +
    (live ? "" : " — remote not connected");
}

function measureBanner() {
  document.documentElement.style.setProperty("--banner-h", $("#banner").offsetHeight + "px");
}

export function initShell() {
  for (const b of $$(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  watch("bridge", () => { renderBanner(); renderHint(); });
  watch("ws", () => { renderBanner(); renderHint(); });
  watch("link", renderBanner);
  let wasLive = null;
  watch("link", ({ link }) => {
    const live = link.conn ? link.live : null;
    if (live === false && wasLive !== false)
      toast("Remote connected but silent — put it in pairing mode (STB off) to start a working session", "warn", 10000);
    wasLive = live;
  });
  watch("battery", () => { renderBattery(); renderRemoteBattery(); });
  watch("link", renderRemoteBattery);
  bus.on("ws", ({ up }) => toast(up ? "Bridge connected" : "Bridge disconnected — retrying…", up ? "ok" : "error"));
  bus.on("error", (m) => toast(m.msg || "error", "error", 5000));
  bus.on("flash", (m) => toast(m.msg, m.error ? "error" : m.done ? "ok" : "info", 8000));
  bus.on("log", (m) => { if (m.level === "error") toast(m.msg, "error"); });
  window.addEventListener("resize", measureBanner);
  new ResizeObserver(measureBanner).observe($("#banner"));
  renderBanner();
  renderBattery();
  renderRemoteBattery();
  renderHint();
  initTabs();
}
