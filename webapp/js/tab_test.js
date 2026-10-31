// tab_test.js — scripted pass/fail suite + report export (via bridge report.py).

import { bus, send } from "./ws.js";
import { state, store, watch } from "./state.js";
import { $, el, toast } from "./ui.js";
import { buttonFor, eventLatency, remoteButtons } from "./tab_keys.js";
import { PROFILES, currentProfile, profileChoice, setProfileChoice } from "./profile.js";

const num = (s) => (typeof s === "string" ? parseInt(s, s.trim().toLowerCase().startsWith("0x") ? 16 : 10) : Number(s));

class Aborted extends Error {}

/** Resolve with the first `ev` event passing `pred`, reject on timeout/abort. */
function waitFor(ev, pred, ms, signal) {
  return new Promise((resolve, reject) => {
    const off = bus.on(ev, (m) => { if (pred(m)) { done(); resolve(m); } });
    const t = ms ? setTimeout(() => { done(); reject(new Error("timeout")); }, ms) : null;
    const onAbort = () => { done(); reject(new Aborted()); };
    signal.addEventListener("abort", onAbort);
    function done() { off(); if (t) clearTimeout(t); signal.removeEventListener("abort", onAbort); }
  });
}

const sleep = (ms, signal) => new Promise((res, rej) => {
  const t = setTimeout(res, ms);
  signal.addEventListener("abort", () => { clearTimeout(t); rej(new Aborted()); });
});

export function init(root) {
  const cfgIds = ["bmin", "bmax", "vmin", "vto", "kto", "klat", "iraddr", "ircode", "irto", "irtries"];
  for (const id of cfgIds) {
    const inp = $("#cfg-" + id, root);
    inp.value = store.get("cfg." + id, inp.value);
    inp.onchange = () => store.set("cfg." + id, inp.value);
  }
  for (const id of ["dut", "operator"]) {
    const inp = $("#t-" + id, root);
    inp.value = store.get("t." + id, "");
    inp.onchange = () => store.set("t." + id, inp.value);
  }
  const cfg = (id) => $("#cfg-" + id, root).value;

  // ---- remote profile: expected buttons and voice method per remote model ----
  const profSel = $("#t-profile", root);
  function renderProfile() {
    const cur = currentProfile();
    profSel.textContent = "";
    profSel.append(el("option", { value: "auto", selected: profileChoice() === "auto" },
      `Auto — ${PROFILES[cur.id].name}`));
    for (const [id, p] of Object.entries(PROFILES))
      profSel.append(el("option", { value: id, selected: profileChoice() === id }, p.name));
  }
  profSel.onchange = () => setProfileChoice(profSel.value);
  watch("profile", renderProfile);
  watch("gatt", renderProfile);
  renderProfile();
  const prompt = $("#t-prompt", root);
  const say = (...nodes) => { prompt.textContent = ""; prompt.append(...nodes); };

  // -------------------------------------------------------------------------
  const STEPS = [
    { id: "link", name: "BLE link", desc: "Remote connected, bonded and encrypted", run: stepLink },
    { id: "keys", name: "Keys", desc: "Every expected button decoded", run: stepKeys },
    { id: "voice", name: "Voice", desc: "Mic stream ≥ N frames", run: stepVoice },
    { id: "ir", name: "IR loopback", desc: "NEC TX received back by the IR unit", run: stepIr },
    { id: "battery", name: "Battery", desc: "Jig VBAT within range", run: stepBattery },
  ];
  let results = {};
  let ctl = null;
  let runAddr = null, addrChanges = [];   // H4: a different remote taking over mid-run is flagged
  bus.on("state", (m) => {
    if (!ctl || !m.conn || !m.addr || m.cached) return;
    if (runAddr && m.addr !== runAddr && addrChanges[addrChanges.length - 1] !== m.addr) {
      if (!addrChanges.length) addrChanges.push(runAddr);
      addrChanges.push(m.addr);
      toast(`Warning: a different remote (${m.addr}) is now connected`, "warn", 8000);
    }
    if (!runAddr) runAddr = m.addr;
  });
  let runStarted = null;
  let manual = null;     // resolver for operator pass/fail buttons

  function renderSteps() {
    const ol = $("#t-steps", root);
    ol.textContent = "";
    for (const s of STEPS) {
      const r = results[s.id];
      const st = r ? r.status : "";
      ol.append(el("li" + (st === "running" ? ".running" : ""),
        el("div", el("div.sname", s.name), el("div.sdetail", r && r.detail ? r.detail : s.desc)),
        el("span.res" + (st ? "." + st : ""), st ? st.toUpperCase() : "—"),
        el("button.btn.small", { disabled: !!ctl, title: "Run this step", onclick: () => runSteps([s]) }, "▶")));
    }
    const ran = Object.values(results).filter((r) => r.status !== "skip" && r.status !== "running");
    const v = $("#t-verdict", root);
    if (!ran.length || ctl) { v.textContent = ""; v.className = "verdict"; return; }
    const pass = ran.every((r) => r.status === "pass");
    v.textContent = pass ? "PASS" : "FAIL";
    v.className = "verdict " + (pass ? "pass" : "fail");
  }

  function operatorButtons() {
    return el("div.row.gap.wrap.btns",
      el("button.btn", { onclick: () => manual && manual("pass") }, "Mark pass"),
      el("button.btn.danger", { onclick: () => manual && manual("fail") }, "Mark fail"),
      el("button.btn.ghost", { onclick: () => manual && manual("skip") }, "Skip"));
  }
  const operatorDecision = (signal) => new Promise((resolve, reject) => {
    manual = resolve;
    signal.addEventListener("abort", () => reject(new Aborted()));
  });

  async function runSteps(steps) {
    if (ctl) return;
    if (!state.bridge.connected) return toast("Test runner needs the jig running xiao-test-tool", "warn");
    ctl = new AbortController();
    runStarted = runStarted && steps.length === 1 ? runStarted : Date.now();
    if (steps.length > 1) { results = {}; addrChanges = []; runAddr = state.link.addr || null; }
    const prof = currentProfile();
    send({ cmd: "note", text: `test run started: DUT "${$("#t-dut", root).value}", operator "${$("#t-operator", root).value}", ` +
      `remote ${state.link.addr || "none"}, profile ${prof.name}, steps ${steps.map((s) => s.id).join(",")}` });
    for (const s of steps) {
      results[s.id] = { status: "running", started: Date.now() };
      renderSteps();
      try {
        const r = await s.run(ctl.signal);
        results[s.id] = { ...results[s.id], ...r, finished: Date.now() };
      } catch (e) {
        if (e instanceof Aborted) {
          results[s.id] = { ...results[s.id], status: "skip", detail: "aborted", finished: Date.now() };
          break;
        }
        results[s.id] = { ...results[s.id], status: "error", detail: String(e.message || e), finished: Date.now() };
      }
      manual = null;
      renderSteps();
    }
    ctl = null;
    const ran = Object.values(results).filter((r) => r.status && r.status !== "skip");
    const verdict = !ran.length ? "incomplete" : ran.every((r) => r.status === "pass") ? "PASS" : "FAIL";
    send({ cmd: "note", text: `test run finished: ${verdict} — ` +
      Object.entries(results).map(([k, r]) => `${k}=${r.status}`).join(" ") +
      (addrChanges.length ? ` — WARNING remote address changed: ${addrChanges.join(" → ")}` : "") });
    say(el("p", "Run finished. ", el("b", "Export report"), " to save JSON + printable HTML."),
      addrChanges.length ? el("p.warn", `Warning: the connected remote changed during the run (${addrChanges.join(" → ")}).`) : null);
    renderSteps();
  }

  // ------------------------------------------------------------------ steps
  async function stepLink() {
    const l = state.link;
    if (!l.conn) return { status: "fail", detail: "not connected — use the Connect tab first" };
    if (!l.secured) return { status: "fail", detail: "connected but not encrypted/bonded — press Bond" };
    if (l.live === false) return { status: "fail", detail: "link is silent (the remote sends nothing) — put it in pairing mode" };
    return { status: "pass", detail: `${l.addr} · ${l.phy} · MTU ${l.mtu} · ${l.rssi} dBm`,
      metrics: { addr: l.addr, phy: l.phy, mtu: l.mtu, rssi: l.rssi, interval_ms: l.interval_ms } };
  }

  async function stepKeys(signal) {
    const kp = currentProfile();
    if (kp.keys === "none") return { status: "skip", detail: `n/a — ${kp.keysNote || "key format not supported"}`, metrics: { profile: kp.id } };
    const expect = remoteButtons().filter((b) => b.expect);
    const seen = new Map();   // button id -> latency
    const extra = new Set();
    const maxLat = Number(cfg("klat")) || 0;
    const draw = () => say(
      el("div.big", "Press each button on the remote"),
      el("p.muted", `${seen.size} / ${expect.length} done — the visual remote on the Keys tab lights up too.`),
      el("div.todo", ...expect.filter((b) => !seen.has(b.id)).map((b) => el("span", b.label))),
      operatorButtons());
    draw();
    const off = bus.on("key", (ev) => {
      if (!ev.down) return;
      const id = buttonFor(ev.usage);
      if (!id) { extra.add(ev.usage); return; }
      if (seen.has(id)) return;
      seen.set(id, null);
      // Same measurement point as the Keys tab: once the frame is painted.
      requestAnimationFrame(() => {
        seen.set(id, eventLatency(ev) + (performance.now() - ev._rx));
        draw();
        if (expect.every((b) => seen.get(b.id) !== undefined && seen.get(b.id) !== null) && manual) manual("auto");
      });
    });
    try {
      const timeout = sleep(Number(cfg("kto")) * 1000, signal).then(() => "timeout", () => new Promise(() => {}));  // abort is reported by operatorDecision
      const r = await Promise.race([operatorDecision(signal), timeout]);
      const lat = {};
      for (const b of expect) if (seen.get(b.id) != null) lat[b.label] = +seen.get(b.id).toFixed(1);
      const vals = Object.values(lat);
      const metrics = { seen: seen.size, expected: expect.length, latency_ms: lat,
        latency_max_ms: vals.length ? Math.max(...vals) : null,
        latency_avg_ms: vals.length ? +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1) : null,
        unmapped_usages: [...extra] };
      const missing = expect.filter((b) => !seen.has(b.id)).map((b) => b.label);
      if (r === "skip") return { status: "skip", detail: "skipped by operator", metrics };
      if (r === "fail") return { status: "fail", detail: "failed by operator" + (missing.length ? `; missing ${missing.join(", ")}` : ""), metrics };
      if (missing.length && r !== "pass") return { status: "fail", detail: `${r === "timeout" ? "timed out; " : ""}missing ${missing.join(", ")}`, metrics };
      if (maxLat && metrics.latency_max_ms > maxLat) return { status: "fail", detail: `max latency ${metrics.latency_max_ms} ms > ${maxLat} ms`, metrics };
      return { status: "pass", detail: `${seen.size}/${expect.length} buttons · max ${metrics.latency_max_ms ?? "—"} ms`, metrics };
    } finally { off(); }
  }

  async function stepVoice(signal) {
    const prof = currentProfile();
    if (prof.voice === "none") return { status: "skip", detail: `n/a — ${prof.voiceNote || "voice not supported"}`, metrics: { profile: prof.id } };
    const need = Number(cfg("vmin")) || 1;
    let frames = 0, bytes = 0, first = 0;
    say(el("div.big", "Press the mic button and speak"), el("p.muted", `Waiting for a voice stream of ≥ ${need} frames…`), operatorButtons());
    const off = bus.on("voice", (m) => {
      if (!first) first = performance.now();
      frames++;
      bytes += (m.bytes || "").length / 2;
      if (frames >= need && manual) manual("auto");
    });
    try {
      const timeout = sleep(Number(cfg("vto")) * 1000, signal).then(() => "timeout", () => new Promise(() => {}));  // abort is reported by operatorDecision
      const r = await Promise.race([operatorDecision(signal), timeout]);
      const secs = first ? (performance.now() - first) / 1000 : 0;
      const metrics = { frames, bytes, fps: secs ? +(frames / secs).toFixed(1) : null, required: need };
      if (r === "skip") return { status: "skip", detail: "skipped by operator", metrics };
      if (r === "fail") return { status: "fail", detail: "failed by operator", metrics };
      if (r === "pass" || frames >= need) return { status: "pass", detail: `${frames} frames, ${bytes} bytes`, metrics };
      return { status: "fail", detail: frames ? `only ${frames} frames (need ${need})` : "no voice stream (timeout)", metrics };
    } finally { off(); }
  }

  async function stepIr(signal) {
    const addr = cfg("iraddr"), code = cfg("ircode");
    const tries = Math.max(1, Number(cfg("irtries")) || 1);
    const to = Number(cfg("irto")) || 1500;
    const want = (m) => m.proto === "NEC" && num(m.addr) === num(addr) && num(m.code) === num(code);
    let lastSeen = null;
    for (let i = 1; i <= tries; i++) {
      say(el("div.big", "IR loopback"), el("p.muted", `Sending NEC ${addr}/${code} (try ${i}/${tries}). TX and RX share the Unit IR: hold a reflective surface (white card, wall) a few cm in front of it to close the loop.`));
      const t0 = performance.now();
      const got = waitFor("ir", (m) => { if (!m.repeat) lastSeen = m; return want(m); }, to, signal);
      send({ cmd: "ir_tx", proto: "NEC", addr, code });
      try {
        const m = await got;
        const rt = +(performance.now() - t0).toFixed(0);
        return { status: "pass", detail: `received ${m.proto} ${m.addr}/${m.code} in ${rt} ms (try ${i})`,
          metrics: { tries: i, round_trip_ms: rt, addr: m.addr, code: m.code } };
      } catch (e) {
        if (e instanceof Aborted) throw e;
      }
      await sleep(200, signal);
    }
    return { status: "fail", detail: lastSeen ? `wrong frame back: ${lastSeen.proto} ${lastSeen.addr || ""}/${lastSeen.code || ""}` : "nothing received",
      metrics: { tries } };
  }

  async function stepBattery(signal) {
    const lo = Number(cfg("bmin")), hi = Number(cfg("bmax"));
    say(el("div.big", "Battery"), el("p.muted", "Reading VBAT…"));
    const got = waitFor("battery", (m) => (m.which || "jig") === "jig", 4000, signal);
    send({ cmd: "battery" });
    const m = await got;
    const ok = m.mv >= lo && m.mv <= hi;
    return { status: ok ? "pass" : "fail", detail: `${m.mv} mV (${m.pct}%) — limits ${lo}–${hi} mV`,
      metrics: { mv: m.mv, pct: m.pct, min_mv: lo, max_mv: hi } };
  }

  // ------------------------------------------------------------------ export
  function buildReport() {
    const steps = STEPS.filter((s) => results[s.id]).map((s) => ({ id: s.id, name: s.name, ...results[s.id] }))
      .map((s) => ({ ...s, status: s.status === "running" ? "skip" : s.status }));
    const b = state.bridge, l = state.link, h = state.hello || {};
    return {
      title: "Remote test report" + ($("#t-dut", root).value ? " — " + $("#t-dut", root).value : ""),
      started: runStarted, finished: Date.now(), operator: $("#t-operator", root).value,
      jig: { firmware: `${b.fw || "?"} ${b.ver || ""}`.trim(), port: b.port, jig_addr: h.addr, simulated: !!b.simulated,
        battery_mv: state.battery.jig ? state.battery.jig.mv : null, browser: navigator.userAgent.split(") ")[0] + ")" },
      dut: { label: $("#t-dut", root).value, addr: l.addr, rssi: l.rssi, phy: l.phy, mtu: l.mtu,
        remote_battery_pct: state.battery.remote ? state.battery.remote.pct : null,
        profile: currentProfile().name, identity: state.remoteInfo || {},
        address_changes: addrChanges.length ? addrChanges.join(" → ") : "none" },
      notes: (addrChanges.length ? `WARNING: the connected remote changed during the run (${addrChanges.join(" → ")}). ` : "") +
        (state.sessionLog?.name ? `Session log: reports/logs/${state.sessionLog.name}` : "Session log: off"),
      steps,
    };
  }

  $("#btn-t-run", root).onclick = () => runSteps(STEPS);
  $("#btn-t-abort", root).onclick = () => ctl && ctl.abort();
  $("#btn-t-export", root).onclick = () => {
    if (!Object.keys(results).length) return toast("Run at least one step first", "warn");
    const rep = buildReport();
    send({ cmd: "report_save", report: rep });
    const blob = new Blob([JSON.stringify(rep, null, 2)], { type: "application/json" });
    const box = $("#t-export", root);
    box.textContent = "";
    box.append(el("div.saved", "Saving on the bridge… ", el("a", { href: URL.createObjectURL(blob), download: "report.json" }, "download JSON here")));
  };
  bus.on("report_saved", (m) => {
    const box = $("#t-export", root);
    box.textContent = "";
    box.append(el("div.saved", el("b", `Report saved (${m.verdict.toUpperCase()}): `),
      el("a", { href: m.html, target: "_blank" }, "report.html"), " · ", el("a", { href: m.json, download: "" }, "report.json")));
    toast("Report saved", "ok");
  });
  renderSteps();
}
