// tab_voice.js — mic indicator, frame rate, level meter, record + WAV save.

import { bus, send } from "./ws.js";
import { state, notify } from "./state.js";
import { $, el, logLine, toast } from "./ui.js";

export function init(root) {
  const ind = $("#mic-ind", root);
  const meter = $("#voice-meter i", root);
  const vlog = $("#voice-log", root);
  const times = [];     // arrival times (ms) for fps
  let bytesWin = [];
  let level = 0;

  function setActive(on) {
    state.voice.active = on;
    ind.className = "mic" + (on ? " on" : "");
    ind.lastElementChild.textContent = on ? "streaming" : "idle";
    if (on) { state.voice.frames = 0; $("#v-frames", root).textContent = "0"; }
    notify("voice");
  }

  bus.on("voice_state", (m) => {
    setActive(!!m.active);
    logLine(vlog, m.active ? "stream started" : `stream stopped (${state.voice.frames} frames)`);
  });
  bus.on("voice_ctl", (m) => logLine(vlog, `ctl ${m.op}  ${m.bytes || ""}`));
  bus.on("voice", (m) => {
    const now = performance.now();
    times.push(now);
    bytesWin.push([now, (m.bytes || "").length / 2]);
    while (times.length && now - times[0] > 1000) times.shift();
    bytesWin = bytesWin.filter(([t]) => now - t <= 1000);
    state.voice.frames++;
    state.voice.codec = m.codec;
    level = Math.max(level * 0.6, m.level || 0);
    if (!state.voice.active) setActive(true);
    notify("voice");
  });

  // Paint at most once per frame, and decay the meter when the stream stops.
  (function paint() {
    level *= 0.92;
    meter.style.width = Math.min(100, level * 160) + "%";
    $("#v-fps", root).textContent = times.length ? String(times.length) : "—";
    const bps = bytesWin.reduce((a, [, n]) => a + n, 0);
    $("#v-bps", root).textContent = bps ? `${(bps * 8 / 1000).toFixed(1)} kbit/s` : "—";
    $("#v-frames", root).textContent = String(state.voice.frames);
    // IMA-ADPCM carries 4 bits per sample, so bytes/s * 2 is the sample rate.
    const khz = bps ? (bps * 2 > 12000 ? 16 : 8) : null;
    $("#v-codec", root).textContent = state.voice.codec ? `${state.voice.codec} (IMA${khz ? `, ${khz} kHz` : ""})` : "—";
    const now = performance.now();
    while (times.length && now - times[0] > 1000) times.shift();
    requestAnimationFrame(paint);
  })();

  const rec = $("#btn-rec", root);
  rec.onclick = () => {
    const on = !state.voice.recording;
    state.voice.recording = on;
    send({ cmd: "voice_record", on });
    rec.className = "btn " + (on ? "rec" : "primary");
    rec.textContent = on ? "■ Stop" : "● Record";
  };
  bus.on("voice_record", (m) => {
    $("#v-rec", root).textContent = m.on ? "recording…" : `${m.frames} frames`;
  });
  $("#btn-save", root).onclick = () => send({ cmd: "voice_save" });
  $("#btn-mic-open", root).onclick = () => send({ cmd: "mic", on: true });
  $("#btn-mic-close", root).onclick = () => send({ cmd: "mic", on: false });
  $("#btn-vlog-clear", root).onclick = () => { vlog.textContent = ""; };

  bus.on("voice_saved", (m) => {
    const box = $("#voice-saved", root);
    box.textContent = "";
    if (!m.ok) return toast(m.msg || "nothing to save", "warn");
    const i = m.info || {};
    box.append(el("div.saved",
      el("div", el("b", "Saved "), `${m.frames} frames, ${m.bytes} bytes`, i.seconds !== undefined ? ` · ${i.seconds} s decoded as ${i.codec}` : ""),
      m.url && el("audio", { controls: true, src: m.url }),
      el("div.row.gap.wrap", m.url && el("a", { href: m.url, download: "" }, "Download WAV"),
        el("a", { href: m.raw_url, download: "" }, "Download raw"),
        m.decode_error && el("span.muted", "decode failed: " + m.decode_error))));
    toast("Voice saved", "ok");
  });
}
