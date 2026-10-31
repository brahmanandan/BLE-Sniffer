// state.js — single app state store. Tabs read `state` and subscribe to changes.
//
//   import { state, update, watch } from "./state.js";
//   watch("link", s => render(s.link));
//   update("link", {conn: true});            // shallow-merge + notify

import { bus } from "./ws.js";

export const state = {
  bridge: { connected: false, fw: null, ver: null, port: null, reason: null },
  hello: null,
  link: { conn: false, stage: "idle" },       // last `state` event
  gatt: null,
  hid: null,
  battery: { jig: null, remote: null },
  scan: new Map(),                             // addr -> scan event (+ seen time)
  voice: { active: false, frames: 0, codec: null, recording: false },
  keysSeen: new Map(),                         // usage -> {count, first, lat[]}
  bonds: [],                                   // remotes bonded to the jig (firmware `bonds`)
  remoteInfo: null,                            // identity of the connected remote (Connect ▸ Remote info)
  sessionLog: null,                            // persistent session log status
  ws: false,
};

const watchers = new Map();

export function watch(key, fn) {
  if (!watchers.has(key)) watchers.set(key, new Set());
  watchers.get(key).add(fn);
  return () => watchers.get(key).delete(fn);
}

export function notify(key) {
  for (const fn of watchers.get(key) || []) {
    try { fn(state); } catch (e) { console.error("watcher", key, e); }
  }
}

export function update(key, patch) {
  const cur = state[key];
  state[key] = cur && typeof cur === "object" && !(cur instanceof Map) && patch && typeof patch === "object"
    ? { ...cur, ...patch } : patch;
  notify(key);
}

// --- wire bus events into state -----------------------------------------------
bus.on("ws", ({ up }) => { state.ws = up; notify("ws"); });
bus.on("bridge", (m) => {
  const was = state.bridge.connected;
  update("bridge", m);
  if (!!m.connected !== !!was) {
    state.link = { conn: false, stage: "idle" };
    state.gatt = null;
    notify("link"); notify("gatt");
  }
});
bus.on("hello", (m) => update("hello", m));
bus.on("bonds", (m) => { state.bonds = m.list || []; notify("bonds"); });
bus.on("session_log", (m) => { state.sessionLog = m; notify("sessionLog"); });
bus.on("state", (m) => update("link", m));
bus.on("gatt", (m) => update("gatt", m));
bus.on("hid", (m) => update("hid", m));
bus.on("battery", (m) => {
  const which = m.which || "jig";
  state.battery = { ...state.battery, [which]: { ...m, at: Date.now() } };
  notify("battery");
});
bus.on("scan", (m) => {
  const prev = state.scan.get(m.addr);
  const merged = { ...prev, ...m, seen: Date.now() };
  if (prev && !m.name && prev.name) merged.name = prev.name;  // keep a learned name
  state.scan.set(m.addr, merged);
  notify("scan");
});

// --- tiny persistence helper (per-browser conveniences only) -----------------
export const store = {
  get(k, dflt) {
    try { const v = localStorage.getItem("jig." + k); return v === null ? dflt : JSON.parse(v); } catch { return dflt; }
  },
  set(k, v) {
    try { localStorage.setItem("jig." + k, JSON.stringify(v)); } catch { /* private mode etc. */ }
  },
};
