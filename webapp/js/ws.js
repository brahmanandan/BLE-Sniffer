// ws.js — WebSocket client with auto-reconnect, clock sync and a tiny pub/sub bus.
//
//   import { bus, send, connect } from "./ws.js";
//   bus.on("key", ev => ...);          // by event name (msg.ev)
//   bus.on("*", msg => ...);           // everything
//   bus.on("ws", ({up}) => ...);       // link state
//   send({cmd: "scan", sec: 8});

const handlers = new Map();

export const bus = {
  on(name, fn) {
    if (!handlers.has(name)) handlers.set(name, new Set());
    handlers.get(name).add(fn);
    return () => handlers.get(name).delete(fn);
  },
  emit(name, payload) {
    for (const fn of handlers.get(name) || []) {
      try { fn(payload); } catch (e) { console.error("handler for", name, e); }
    }
  },
};

let sock = null;
let backoff = 500;
let pending = [];
export const link = { up: false, url: "", offsetMs: 0, rttMs: null, attempts: 0 };

function wsUrl() {
  const qs = new URLSearchParams(location.search);
  const host = qs.get("ws") || location.host || "localhost:8000";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${host}/ws`;
}

export function connect() {
  link.url = wsUrl();
  link.attempts++;
  try {
    sock = new WebSocket(link.url);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  sock.onopen = () => {
    backoff = 500;
    link.up = true;
    bus.emit("ws", { up: true });
    send({ cmd: "bridge" });
    syncClock();
    for (const m of pending.splice(0)) sock.send(m);
  };
  sock.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    msg._rx = performance.now();
    if (msg.ev === "time") return onTime(msg);
    bus.emit("*", msg);
    if (msg.ev) bus.emit(msg.ev, msg);
  };
  sock.onclose = () => {
    const was = link.up;
    link.up = false;
    if (was) bus.emit("ws", { up: false });
    scheduleReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch { /* ignore */ } };
}

function scheduleReconnect() {
  setTimeout(connect, backoff);
  backoff = Math.min(backoff * 1.7, 5000);
}

export function send(obj) {
  const s = JSON.stringify(obj);
  if (sock && sock.readyState === WebSocket.OPEN) {
    sock.send(s);
    return true;
  }
  if (pending.length < 50) pending.push(s);
  return false;
}

// --- clock sync: serverTime ≈ Date.now() + offsetMs -------------------------
let best = null;
function syncClock() {
  let n = 0;
  const tick = () => {
    if (!link.up) return;
    send({ cmd: "time", t: Date.now() });
    if (++n < 5) setTimeout(tick, 150);
  };
  best = null;
  tick();
}
function onTime(msg) {
  const now = Date.now();
  const rtt = now - msg.client;
  if (best === null || rtt < best) {
    best = rtt;
    link.rttMs = rtt;
    link.offsetMs = msg.server - (msg.client + rtt / 2);
    bus.emit("clock", { offsetMs: link.offsetMs, rttMs: rtt });
  }
}
setInterval(() => link.up && syncClock(), 60000);

/** Server-clock "now" in ms, comparable to the bridge's host_ts stamps. */
export const serverNow = () => Date.now() + link.offsetMs;
