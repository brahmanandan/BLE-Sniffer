// tab_keys.js — visual remote, key log, press→display latency, "buttons seen".

import { bus, serverNow } from "./ws.js";
import { state, notify, store, watch } from "./state.js";
import { $, el, fmtTime, hexSpaced, toast, trim } from "./ui.js";
import { ROWS, buttons, usageIndex } from "./remote_layout.js";
import { currentProfile } from "./profile.js";

let extraMap = store.get("keymap", {});
let idx = usageIndex(extraMap, currentProfile());
const lats = [];

/** Latency for one key event, ms: USB half-RTT + bridge→browser (+ render, added by caller). */
export function eventLatency(ev) {
  const usb = state.bridge.usb_rtt_ms ? state.bridge.usb_rtt_ms / 2 : 0;
  return Math.max(0, serverNow() - ev.host_ts) + usb;
}

export function resetSeen() {
  state.keysSeen.clear();
  notify("keysSeen");
}

/** Button id for a usage name (after user mappings). */
export const buttonFor = (usage) => idx.get(usage);
export const remoteButtons = () => buttons(extraMap, currentProfile());

export function init(root) {
  const remote = $("#remote", root);
  const tbody = $("#keys-table tbody", root);
  const showUp = $("#keys-show-up", root);
  const keyEls = new Map();
  let mapping = null;   // {armed:true} or {id}

  function drawRemote() {
    remote.textContent = "";
    keyEls.clear();
    const prof = currentProfile();
    idx = usageIndex(extraMap, prof);
    const byId = new Map(buttons(extraMap, prof).map((b) => [b.id, b]));
    $("#keys-profile", root).textContent = `${prof.name}${prof.auto ? " (auto)" : ""}`;
    for (const r of ROWS) {
      const row = el("div.rrow", { style: `grid-template-columns:repeat(${r.cols},1fr)` });
      for (const k of r.keys) {
        if (k.spacer) { row.append(el("span")); continue; }
        const pb = byId.get(k.id);
        const b = el("button.rkey" + (k.cls ? "." + k.cls.split(" ").join(".") : "") + (pb.expect ? "" : ".opt"),
          { title: pb.usages.join(", ") || "unmapped — use Map key", onclick: () => onKeyClick(k) }, k.label);
        keyEls.set(k.id, b);
        row.append(b);
      }
      remote.append(row);
    }
    paintSeen();
  }

  function onKeyClick(k) {
    if (!mapping) return;
    for (const b of keyEls.values()) b.classList.remove("mapping");
    mapping = { id: k.id };
    keyEls.get(k.id).classList.add("mapping");
    $("#map-hint", root).hidden = false;
    $("#map-hint", root).textContent = `Now press the physical key for “${k.label}” on the remote…`;
  }

  $("#btn-map-key", root).onclick = () => {
    if (mapping) { mapping = null; for (const b of keyEls.values()) b.classList.remove("mapping"); $("#map-hint", root).hidden = true; return; }
    mapping = { armed: true };
    $("#map-hint", root).hidden = false;
    $("#map-hint", root).textContent = "Tap a button on the drawing to map it (tap Map key again to cancel).";
  };

  function paintSeen() {
    const seenIds = new Set();
    for (const u of state.keysSeen.keys()) { const id = idx.get(u); if (id) seenIds.add(id); }
    for (const [id, b] of keyEls) b.classList.toggle("seen", seenIds.has(id));
    const list = $("#seen-list", root);
    list.textContent = "";
    const btns = buttons(extraMap, currentProfile()).filter((b) => b.expect);
    let n = 0;
    for (const b of btns) {
      const yes = seenIds.has(b.id);
      n += yes;
      list.append(el("li" + (yes ? ".yes" : ""), b.label));
    }
    $("#seen-count", root).textContent = `${n} / ${btns.length}`;
    const other = [...state.keysSeen.keys()].filter((u) => !idx.get(u));
    $("#seen-other-wrap", root).hidden = !other.length;
    const ol = $("#seen-other", root);
    ol.textContent = "";
    for (const u of other) ol.append(el("li", u));
  }

  function light(id) {
    const b = keyEls.get(id);
    if (!b) return;
    b.classList.add("lit");
  }
  function unlight(id) {
    const b = keyEls.get(id);
    if (b) b.classList.remove("lit");
  }

  function renderLat() {
    if (!lats.length) return;
    const s = [...lats].sort((a, b) => a - b);
    const avg = s.reduce((a, b) => a + b, 0) / s.length;
    const p95 = s[Math.min(s.length - 1, Math.floor(s.length * 0.95))];
    const f = (v) => `${v.toFixed(1)} ms`;
    $("#lat-last", root).textContent = f(lats[lats.length - 1]);
    $("#lat-avg", root).textContent = f(avg);
    $("#lat-p95", root).textContent = f(p95);
    $("#lat-max", root).textContent = f(s[s.length - 1]);
    $("#lat-n", root).textContent = `${lats.length} presses`;
    for (const id of ["lat-last", "lat-p95", "lat-max"]) {
      const v = parseFloat($("#" + id, root).textContent);
      $("#" + id, root).className = v < 50 ? "lat-ok" : "lat-bad";
    }
  }

  bus.on("key", (ev) => {
    const id = idx.get(ev.usage);
    if (ev.down) {
      if (mapping && mapping.id) {
        const prev = extraMap[mapping.id] || [];
        if (!prev.includes(ev.usage)) extraMap[mapping.id] = [...prev, ev.usage];
        store.set("keymap", extraMap);
        idx = usageIndex(extraMap, currentProfile());
        toast(`Mapped ${ev.usage} → ${mapping.id}`, "ok");
        mapping = null;
        $("#map-hint", root).hidden = true;
        drawRemote();
      }
      const target = idx.get(ev.usage);
      if (target) light(target);
    } else if (id) {
      unlight(id);
    }
    if (!ev.down && !showUp.checked) return;
    const row = el("tr" + (ev.down ? ".fresh" : ".up"),
      el("td", fmtTime()), el("td", ev.handle || ""), el("td", hexSpaced(ev.report)), el("td", el("b", ev.usage)),
      el("td", ev.page || ""), el("td", ev.down ? "↓" : "↑"), el("td"));
    tbody.prepend(row);
    trim(tbody, 400);
    // Measure once the row is actually on screen.
    if (ev.down) requestAnimationFrame(() => {
      const lat = eventLatency(ev) + (performance.now() - ev._rx);
      row.lastElementChild.textContent = lat.toFixed(1);
      row.lastElementChild.className = lat < 50 ? "lat-ok" : "lat-bad";
      lats.push(lat);
      if (lats.length > 500) lats.shift();
      const rec = state.keysSeen.get(ev.usage) || { count: 0, first: Date.now(), lat: [] };
      rec.count++;
      rec.lat.push(lat);
      state.keysSeen.set(ev.usage, rec);
      notify("keysSeen");
      renderLat();
    });
  });

  $("#btn-keys-clear", root).onclick = () => { tbody.textContent = ""; };
  $("#btn-seen-reset", root).onclick = () => { resetSeen(); lats.length = 0; renderLat(); };
  bus.on("state", (m) => { $("#lat-int", root).textContent = m.interval_ms ? `${m.interval_ms} ms` : "—"; });
  watch("keysSeen", paintSeen);
  // ---- reports that carry no keys (vendor data streams) -------------------------------
  const hd = new Map();      // handle -> {rid, count, times[], last, prev}
  const hdCard = $("#hiddata-card", root);
  bus.on("hid_data", (m) => {
    const now = performance.now();
    const e = hd.get(m.handle) || { rid: m.rid, count: 0, times: [], last: "", prev: "" };
    e.count++;
    e.times.push(now);
    while (e.times.length && now - e.times[0] > 2000) e.times.shift();
    e.prev = e.last;
    e.last = m.bytes || "";
    hd.set(m.handle, e);
    paintHd();
  });
  const paintHd = (() => {
    let pending = false;
    return () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        hdCard.hidden = hd.size === 0;
        const tb = $("#hiddata-table tbody", root);
        tb.textContent = "";
        for (const [h, e] of hd) {
          const cur = e.last.match(/../g) || [], prev = e.prev.match(/../g) || [];
          const bytes = el("td.hdbytes", ...cur.map((b, i) => el("span" + (prev[i] !== undefined && prev[i] !== b ? ".chg" : ""), b.toUpperCase() + " ")));
          tb.append(el("tr", el("td", h), el("td", `${e.rid} (0x${Number(e.rid).toString(16).toUpperCase()})`), el("td", String(e.count)),
            el("td", `${(e.times.length / 2).toFixed(0)} /s`), bytes));
        }
      });
    };
  })();
  $("#btn-hiddata-clear", root).onclick = () => { hd.clear(); paintHd(); };

  drawRemote();
  watch("profile", drawRemote);
  watch("gatt", drawRemote);                 // "auto" profile follows the connected remote
}
