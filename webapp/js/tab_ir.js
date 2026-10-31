// tab_ir.js — IR receive log, learn, send (named NEC/SIRC or raw replay).

import { bus, send } from "./ws.js";
import { store } from "./state.js";
import { $, el, fmtTime, toast, trim } from "./ui.js";

let learned = store.get("ir.learned", []);

export function sendIrFrame(f) {
  if (f.proto === "NEC" || f.proto === "SIRC") {
    send({ cmd: "ir_tx", proto: f.proto, addr: f.addr, code: f.code });
  } else {
    send({ cmd: "ir_tx_raw", carrier: 38000, timings: f.raw.slice(0, 200) });
  }
}

export function init(root) {
  const tbody = $("#ir-table tbody", root);
  const list = $("#ir-learned", root);
  const status = $("#ir-tx-status", root);
  let learnNext = false;
  let txStart = 0;

  function save() { store.set("ir.learned", learned); }

  function renderLearned() {
    list.textContent = "";
    if (!learned.length) { list.append(el("li.empty", "Nothing learned yet.")); return; }
    learned.forEach((f, i) => {
      const name = el("input", { value: f.name, "aria-label": "Name", oninput: (e) => { f.name = e.target.value; save(); } });
      list.append(el("li.item", name,
        el("span.desc", f.proto === "UNKNOWN" ? `raw · ${f.raw.length} edges` : `${f.proto} ${f.addr}/${f.code}`),
        el("button.btn.small", { onclick: () => { txStart = performance.now(); sendIrFrame(f); } }, "Send"),
        f.proto !== "UNKNOWN" && el("button.btn.small.ghost", { title: "Replay the captured timings exactly",
          onclick: () => { txStart = performance.now(); sendIrFrame({ ...f, proto: "UNKNOWN" }); } }, "Raw"),
        el("button.btn.small.ghost.danger", { onclick: () => { learned.splice(i, 1); save(); renderLearned(); } }, "✕")));
    });
  }

  $("#ir-send-form", root).onsubmit = (e) => {
    e.preventDefault();
    txStart = performance.now();
    send({ cmd: "ir_tx", proto: $("#ir-proto", root).value, addr: $("#ir-addr", root).value.trim(),
      code: $("#ir-code", root).value.trim(), repeats: Number($("#ir-rep", root).value) || 0 });
    status.textContent = "sending…";
  };
  const learnBtn = $("#btn-ir-learn", root);
  learnBtn.onclick = () => {
    learnNext = !learnNext;
    learnBtn.textContent = learnNext ? "Waiting… (point a remote at the jig)" : "Learn next frame";
    learnBtn.classList.toggle("primary", learnNext);
  };
  $("#ir-rx-on", root).onchange = (e) => send({ cmd: "ir_rx", on: e.target.checked });
  $("#btn-ir-clear", root).onclick = () => { tbody.textContent = ""; };

  bus.on("ir", (m) => {
    const raw = m.raw || [];
    const learn = el("button.btn.small.ghost", { onclick: () => addLearned(m) }, "Keep");
    tbody.prepend(el("tr.fresh", el("td", fmtTime()), el("td", el("b", m.proto)), el("td", m.addr || "—"), el("td", m.code || "—"),
      el("td", m.repeat ? "R" : ""), el("td", String(Math.ceil(raw.length / 2))), el("td", learn)));
    trim(tbody, 300);
    if (learnNext && !m.repeat) {
      addLearned(m);
      learnNext = false;
      learnBtn.textContent = "Learn next frame";
      learnBtn.classList.remove("primary");
    }
  });

  function addLearned(m) {
    learned.unshift({ name: m.proto === "UNKNOWN" ? `raw ${learned.length + 1}` : `${m.proto} ${m.addr}/${m.code}`,
      proto: m.proto, addr: m.addr, code: m.code, raw: m.raw || [], t: Date.now() });
    learned = learned.slice(0, 50);
    save();
    renderLearned();
    toast("IR frame learned", "ok");
  }

  bus.on("ir_tx_done", (m) => {
    const ms = txStart ? (performance.now() - txStart).toFixed(0) : "?";
    status.textContent = m.ok ? `tx done (${ms} ms round trip)` : "tx failed";
    txStart = 0;
  });
  renderLearned();
}
