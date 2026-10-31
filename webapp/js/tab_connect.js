// tab_connect.js — scan, connect, bond; link state and GATT tree.

import { bus, send } from "./ws.js";
import { state, watch, store } from "./state.js";
import { $, el, logLine, toast, fmtTime, copyText, downloadText } from "./ui.js";
import { serviceInfo, charInfo, explainProps, decodeValue, parseReportMap, shortUuid, ROLES, parseAdv, describeAdv } from "./gatt_info.js";

const BUSY = new Set(["connecting", "discovering", "pairing", "reading"]);

export function init(root) {
  const list = $("#scan-list", root);
  const addr = $("#conn-addr", root);
  const atype = $("#conn-atype", root);
  const logEl = $("#conn-log", root);
  // The list on screen keeps the newest 300 lines; this buffer keeps the session for Copy/Download.
  const LOG_MAX = 20000;
  const logBuf = [];
  const p2 = (n) => String(n).padStart(2, "0");
  const localDate = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;   // not toISOString(): that is UTC
  const clog = (msg, level = "info") => {
    const d = new Date();
    logBuf.push(`${localDate(d)} ${fmtTime(d)}  ${level === "info" ? "" : level.toUpperCase() + " "}${msg}`);
    if (logBuf.length > LOG_MAX) logBuf.splice(0, logBuf.length - LOG_MAX);
    logLine(logEl, msg, level);
  };
  addr.value = store.get("lastAddr", "");
  atype.value = store.get("lastAtype", "random");

  const flt = { hid: $("#scan-hid-only", root), named: $("#scan-named-only", root), text: $("#scan-filter", root) };
  for (const f of Object.values(flt)) f.addEventListener("input", renderScan);

  $("#btn-scan", root).onclick = () => {
    state.scan.clear();
    renderScan();
    send({ cmd: "scan", sec: Number($("#scan-sec", root).value) });
  };
  $("#btn-connect", root).onclick = () => {
    const a = addr.value.trim().toUpperCase();
    if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(a)) return toast("Enter an address like 60:74:B1:80:AF:1E", "warn");
    store.set("lastAddr", a);
    store.set("lastAtype", atype.value);
    send({ cmd: "connect", addr: a, atype: atype.value });
  };
  $("#btn-bond", root).onclick = () => send({ cmd: "bond" });
  /** One-step pairing (connect → pair → subscribe, with retries). Warns for a NOT PAIRING remote. */
  function pairWith(a, at) {
    if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(a)) return toast("Enter an address like 60:74:B1:80:AF:1E", "warn");
    const d = state.scan.get(a);
    const ad = d ? parseAdv(d.adv, d.rsp) : null;
    if (ad && !d.directed && ad.discoverable === false &&
        !confirm(`${a} is NOT in pairing mode (its advert isn't discoverable), so it will most likely refuse the jig.\n\n` +
                 "Put the remote in pairing mode first (STB off). Try anyway?")) return;
    store.set("lastAddr", a);
    store.set("lastAtype", at);
    send({ cmd: "pair", addr: a, atype: at });
  }
  $("#btn-pair", root).onclick = () => pairWith(addr.value.trim().toUpperCase(), atype.value);
  $("#btn-bonds-refresh", root).onclick = () => send({ cmd: "bonds" });
  $("#btn-unlock", root).onclick = () => send({ cmd: "unlock" });
  function renderBonds() {
    const ul = $("#bond-list", root);
    ul.textContent = "";
    $("#bonds-count", root).textContent = state.bonds.length ? `(${state.bonds.length})` : "";
    if (!state.bonds.length) { ul.append(el("li.empty", "No bonds.")); return; }
    for (const b of state.bonds) {
      ul.append(el("li",
        el("span.addr", b.addr),
        b.locked && el("span.badge.pair", { title: "Session lock: the jig reconnects only to this remote" }, "LOCKED"),
        b.connected && el("span.badge.hid", "CONNECTED"),
        b.rpa && el("span.badge.rand", { title: "uses private addresses (IRK stored)" }, "RPA"),
        el("span.grow"),
        !b.locked && el("button.btn.ghost.small", { title: "Lock the session to this remote", onclick: () => send({ cmd: "lock", addr: b.addr }) }, "🔒"),
        el("button.btn.ghost.small", { title: "Forget this remote", onclick: () => {
          if (confirm(`Forget the bond with ${b.addr}? It will have to pair again.`)) send({ cmd: "bond_delete", addr: b.addr });
        } }, "✕")));
    }
  }
  watch("bonds", renderBonds);
  $("#btn-subscribe", root).onclick = () => send({ cmd: "subscribe", what: "all" });
  $("#btn-disconnect", root).onclick = () => send({ cmd: "disconnect" });
  $("#btn-clear-bonds", root).onclick = () => {
    if (confirm(`Forget all ${state.bonds.length || ""} remotes bonded to the jig?`)) send({ cmd: "clear_bonds" });
  };
  $("#btn-log-clear", root).onclick = () => { logEl.textContent = ""; logBuf.length = 0; };

  // ---- persistent session log: written by the bridge, so it runs with no page open -------
  const persist = $("#log-persist", root);
  const persistFile = $("#log-persist-file", root);
  persist.onchange = () => send({ cmd: "session_log", on: persist.checked });
  bus.on("session_log", (m) => {
    persist.checked = !!m.on;
    persistFile.hidden = !m.name;
    if (m.name) {
      persistFile.textContent = (m.on ? "● " : "") + m.name;
      persistFile.href = m.url;
      persistFile.title = m.on ? "Logging to this file on the PC — click to open" : "Last session log — click to open";
      persistFile.classList.toggle("on", !!m.on);
    }
    if (!m.cached && m.name) {
      clog(m.on ? `session log started: reports/logs/${m.name}` : `session log stopped: reports/logs/${m.name} (${m.lines} lines)`);
      toast(m.on ? `Logging to reports/logs/${m.name}` : "Session log saved", "ok");
    }
  });

  function logExport() {
    const b = state.bridge, l = state.link;
    const info = state.gatt ? Object.fromEntries(infoRows().filter(([, v]) => v && v !== "(empty)")) : {};
    const head = [
      `# Remote Jig log — exported ${new Date().toString()}`,
      `# jig: ${b.fw || "?"} ${b.ver || ""} on ${b.port || "?"}`,
      `# remote: ${l.addr || "none"}${info["Device name"] ? ` "${info["Device name"]}"` : ""}` +
        `${info["Model number"] ? `, model ${info["Model number"]}` : ""}${info["Firmware revision"] ? `, fw ${info["Firmware revision"]}` : ""}`,
      `# session lock: ${l.lock || "off"} · session log: ${state.sessionLog?.on ? "reports/logs/" + state.sessionLog.name : "off"}`,
      `# lines: ${logBuf.length}${logBuf.length >= LOG_MAX ? ` (only the last ${LOG_MAX} kept)` : ""}`,
      "",
    ];
    return head.join("\n") + logBuf.join("\n") + "\n";
  }
  $("#btn-log-copy", root).onclick = async () => {
    if (!logBuf.length) return toast("Log is empty", "warn");
    const ok = await copyText(logExport());
    toast(ok ? `Copied ${logBuf.length} log lines` : "Copy failed — use Download", ok ? "ok" : "warn");
  };
  $("#btn-log-dl", root).onclick = () => {
    if (!logBuf.length) return toast("Log is empty", "warn");
    const d = new Date();
    const stamp = `${localDate(d).replace(/-/g, "")}-${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
    const who = (state.link.addr || "no-remote").replace(/:/g, "");
    downloadText(`remote-log_${who}_${stamp}.txt`, logExport());
  };
  for (const b of root.querySelectorAll("[data-flash]")) {
    b.onclick = () => {
      const t = b.dataset.flash;
      const msg = t === "sniffer"
        ? "Replace xiao-test-tool with the Nordic sniffer image for passive capture in Wireshark? This app can't drive it; " +
          "switch back later with “Flash xiao-test-tool” (needs a double-tap on RST)."
        : "Re-flash the xiao-test-tool image?";
      if (confirm(msg)) send({ cmd: "flash", target: t });
    };
  }

  function pick(d) {
    addr.value = d.addr;
    atype.value = d.atype === "public" ? "public" : "random";
    renderScan();
  }

  /** Worth treating as a remote: advertises HID, has a remote/HID appearance, or is bonded to the jig
   *  (some remotes, e.g. the Android TV one, put neither HID nor appearance in their adverts). */
  const isRemote = (d, ad) => !!(d.hid || d.bonded || (ad.appearance !== null && [0x0180, 0x03C0, 0x03C1].includes(ad.appearance)) ||
    (d.appearance && ((d.appearance >> 6) === 0x0F || d.appearance === 0x0180)));

  function renderScan() {
    const t = flt.text.value.trim().toUpperCase();
    const devs = [...state.scan.values()]
      .filter((d) => (!flt.hid.checked || isRemote(d, parseAdv(d.adv, d.rsp))) && (!flt.named.checked || d.name) &&
        (!t || d.addr.includes(t) || (d.name || "").toUpperCase().includes(t)))
      .sort((a, b) => (!!b.bonded - !!a.bonded) || (!!b.hid - !!a.hid) || (b.rssi - a.rssi));
    list.textContent = "";
    if (!devs.length) {
      list.append(el("li.empty", state.scan.size ? "No devices match the filter." : "No devices yet — press Scan."));
      return;
    }
    for (const d of devs.slice(0, 80)) {
      const pct = Math.max(0, Math.min(100, (d.rssi + 100) * 1.6));
      const ad = parseAdv(d.adv, d.rsp);
      list.append(el("li.dev" + (d.addr === addr.value ? ".sel" : ""), { onclick: () => pick(d), ondblclick: () => { pick(d); pairWith(d.addr, d.atype === "public" ? "public" : "random"); } },
        el("div.rssi", `${d.rssi}`, el("span.bar", el("i", { style: `width:${pct}%` }))),
        el("div",
          el("div.name", d.name || el("span.muted", "(no name)")),
          el("div.meta",
            el("span.addr", d.addr),
            d.directed && el("span.badge.direct", "DIRECT"),
            d.hid && el("span.badge.hid", "HID"),
            // A HID remote that isn't LE-discoverable is only reconnecting to its bonded host and
            // ignores connections from the jig (they fail with 0x3E): it is not in pairing mode.
            isRemote(d, ad) && !d.directed && ad.discoverable === true && el("span.badge.pair", "PAIRING"),
            isRemote(d, ad) && !d.directed && ad.discoverable === false &&
              el("span.badge.direct", { title: "Advert flags say not discoverable: the remote is reconnecting to its own host and will refuse the jig. Put it in pairing mode." }, "NOT PAIRING"),
            d.bonded && el("span.badge.bonded", { title: "the jig holds a bond with this remote" }, "BONDED"),
            d.atype && d.atype !== "public" && el("span.badge.rand", d.atype.toUpperCase())),
          describeAdv(ad) && el("div.advline", describeAdv(ad))),
        el("button.btn.small" + (isRemote(d, ad) ? ".primary" : ""), { title: "Connect, pair and subscribe (one step)",
          onclick: (e) => { e.stopPropagation(); pick(d); pairWith(d.addr, d.atype === "public" ? "public" : "random"); } }, "Pair")));
    }
  }

  function yn(v) { return v === undefined ? "—" : el("span." + (v ? "yes" : "no"), v ? "yes" : "no"); }
  function setKV(id, v) { const n = $("#" + id, root); n.textContent = ""; n.append(v ?? "—"); }

  function renderLink() {
    const l = state.link;
    const st = $("#link-stage", root);
    st.textContent = l.stage || "idle";
    st.className = "stage" + (l.stage === "ready" ? " ready" : BUSY.has(l.stage) ? " busy" : "");
    setKV("kv-addr", l.addr || "—");
    setKV("kv-conn", yn(l.conn));
    setKV("kv-bonded", l.conn ? yn(l.bonded) : "—");
    setKV("kv-secured", l.conn ? yn(l.secured) : "—");
    setKV("kv-mtu", l.conn ? String(l.mtu) : "—");
    setKV("kv-phy", l.conn ? l.phy : "—");
    setKV("kv-int", l.conn && l.interval_ms ? `${l.interval_ms} ms` : "—");
    setKV("kv-rssi", l.conn ? `${l.rssi} dBm` : "—");
    setKV("kv-svcs", l.services ? String(l.services) : "—");
    setKV("kv-live", !l.conn ? "—" : l.live === true ? el("span.yes", "yes — remote is sending")
      : l.live === false ? el("span.no", "SILENT — put the remote in pairing mode") : "unknown — press a key on the remote");
    setKV("kv-lock", l.lock ? `${l.lock}` : "off (any bonded remote)");
    $("#live-hint", root).hidden = !(l.conn && l.secured && l.stage === "ready" && l.live === undefined);
    if (l.stage !== "pairing") $("#passkey-box", root).hidden = true;
    const busy = BUSY.has(l.stage);
    $("#btn-connect", root).disabled = l.conn || busy;
    $("#btn-pair", root).disabled = busy || (l.conn && l.secured);
    $("#btn-bond", root).disabled = !l.conn || l.secured;
    $("#btn-subscribe", root).disabled = !l.conn;
    $("#btn-disconnect", root).disabled = !l.conn && l.stage !== "connecting";
    $("#btn-scan", root).disabled = l.conn;
  }

  // ---- GATT: every characteristic explained, with its decoded value ----------------
  const vals = new Map();          // handle -> {hex, ok, serial}
  let connSerial = 0;              // bumps on every new connection
  bus.on("state", (m) => { if (m.conn && !wasConn) connSerial++; wasConn = !!m.conn; });
  let wasConn = false;
  let valsFor = null;              // address the values belong to
  let readToken = 0, autoRead = false;

  function waitRead(handle, ms) {
    return new Promise((res) => {
      const off = bus.on("gatt_read", (m) => { if (m.handle === handle) { off(); clearTimeout(t); res(m); } });
      const t = setTimeout(() => { off(); res(); }, ms);
    });
  }

  /** Read each readable characteristic once, one at a time (the firmware handles one GATT op at a time). */
  async function readAll() {
    const g = state.gatt;
    if (!g || !state.link.conn) return;
    const my = ++readToken;
    const todo = g.services.flatMap((s) => (s.chars || [])
      .filter((c) => c.props.includes("R") && c.uuid !== "2A4D" && !(c.uuid === "2A4B" && state.hid?.map))
      .map((c) => c.handle));
    $("#btn-gatt-read", root).disabled = true;
    // Several pages may be open (PC + phone): start at a random moment and skip whatever another
    // page already read on this connection — every page sees every gatt_read reply.
    await new Promise((r) => setTimeout(r, Math.random() * 1500));
    const voiceIdle = async () => { while (state.voice.active && my === readToken) await new Promise((r) => setTimeout(r, 500)); };
    for (const h of todo) {
      for (let attempt = 0; attempt < 3; attempt++) {
        await voiceIdle();               // a read during voice would cost audio frames (M6)
        if (my !== readToken || !state.link.conn) break;
        if (vals.get(h)?.serial === connSerial) break;
        send({ cmd: "gatt_read", handle: h });
        const m = await waitRead(h, 4000);
        if (!m?.busy) break;
      }
      if (my !== readToken || !state.link.conn) break;
    }
    $("#btn-gatt-read", root).disabled = false;
  }
  $("#btn-gatt-read", root).onclick = () => readAll();

  // ---- traffic log: who sent what, and how often ---------------------------------
  // read   = the jig asked (GATT Read Request -> Response)
  // notify = the remote pushed it by itself (Handle Value Notification / Indication)
  const stats = new Map();         // handle -> {reads, notifs, last, firstN, lastN}
  const charByHandle = (h) => {
    for (const sv of state.gatt?.services || []) for (const c of sv.chars || []) if (c.handle === h) return c;
    return null;
  };
  const handleByRole = (role) => {
    for (const sv of state.gatt?.services || []) for (const c of sv.chars || []) if (c.role === role) return c.handle;
    return null;
  };
  const ago = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : ms < 120000 ? `${(ms / 1000).toFixed(1)} s` : `${(ms / 60000).toFixed(1)} min`);

  /** Count one exchange; log it unless it is high-rate (keys/voice are counted only). */
  function record(handle, kind, valueText, quiet = false) {
    if (!handle) return;
    const now = Date.now();
    const st = stats.get(handle) || { reads: 0, notifs: 0, last: 0, firstN: 0, lastN: 0 };
    const prev = kind === "notify" ? st.lastN : st.last;
    if (kind === "read") st.reads++;
    else { st.notifs++; if (!st.firstN) st.firstN = now; st.lastN = now; }
    st.last = now;
    stats.set(handle, st);
    if (!quiet) {
      const c = charByHandle(handle);
      const name = c ? charInfo(c.uuid)[0] : "";
      let v = valueText === undefined ? "" : String(valueText);
      // Raw hex can be hundreds of bytes (report maps, audio): shorten it; keep decoded text whole.
      if (/^[0-9a-f]+$/i.test(v) && v.length > 48) v = `${v.slice(0, 32)}… (${v.length / 2} bytes, see GATT panel)`;
      else if (v.length > 200) v = v.slice(0, 200) + "…";
      clog(`${kind === "read" ? "read  " : "notify"} ${handle} ${name}${v ? " = " + v : ""}` +
        (prev ? `  (+${ago(now - prev)} since last)` : "  (first)"));
    }
    renderStatsSoon();
  }

  bus.on("gatt_read", (m) => {
    if (m.busy) return;                  // refused during voice; readAll retries it
    vals.set(m.handle, { hex: m.bytes || "", ok: m.ok, serial: connSerial });
    const c = charByHandle(m.handle);
    record(m.handle, "read", m.ok ? (c && decodeValue(c.uuid, m.bytes)) ?? (m.bytes || "(empty)") : "read not permitted");
    renderGattSoon();
  });
  bus.on("battery", (m) => {
    if (m.which !== "remote" || m.cached) return;      // cached = replay of an old value, not new traffic
    const h = handleByRole("battery");
    if (m.via === "notify") record(h, "notify", `${m.pct} %`);
    else if (m.via === "read") record(h, "read", `${m.pct} %`);
  });
  bus.on("notify", (m) => record(m.handle, "notify", m.bytes));
  bus.on("voice_ctl", (m) => record(handleByRole("voice_ctl"), "notify", `${m.op} ${m.bytes || ""}`));
  bus.on("key", (m) => record(m.handle, "notify", undefined, true));
  bus.on("hid_data", (m) => record(m.handle, "notify", undefined, true));    // vendor streams: count only
  bus.on("voice", (m) => record(m.handle || handleByRole("voice_audio"), "notify", undefined, true));

  function statsLine(c) {
    const st = stats.get(c.handle);
    if (!st) return null;
    const parts = [];
    if (st.reads) parts.push(`read ${st.reads}×`);
    if (st.notifs) {
      let t = `notified ${st.notifs}×`;
      if (st.lastN) t += `, last ${ago(Date.now() - st.lastN)} ago`;
      if (st.notifs > 1 && st.lastN > st.firstN) t += `, avg every ${ago((st.lastN - st.firstN) / (st.notifs - 1))}`;
      parts.push(t);
    }
    return el("div.cstat", parts.join(" · "));
  }
  // Counters change at up to ~400/s during voice: repaint them at most once a second.
  const renderStatsSoon = throttle(() => renderGatt(), 1000);
  setInterval(() => { if (stats.size && !document.hidden) renderGattSoon(); }, 5000);   // keep "ago" fresh

  function maybeAutoRead() {
    const l = state.link;
    if (l.addr && l.addr !== valsFor) { vals.clear(); valsFor = l.addr; }
    if (!l.conn) { autoRead = false; readToken++; return; }
    if (l.stage === "ready" && state.gatt && !autoRead) { autoRead = true; readAll(); }
  }

  function valueLine(c) {
    if (c.uuid === "2A4B" && state.hid?.map) return null;          // shown as the decoded map below
    const v = vals.get(c.handle);
    if (!v) return c.props.includes("R") && c.uuid !== "2A4D" ? el("div.cval.muted", "value: (not read yet)") : null;
    if (!v.ok) return el("div.cval.muted", "value: read not permitted");
    const dec = decodeValue(c.uuid, v.hex);
    return el("div.cval", "value: ", el("b", dec ?? "—"), dec === null || !/^"/.test(dec) ? el("span.muted.mono", `  ${v.hex || ""}`) : null);
  }

  function reportMapBlock(map) {
    const { lines, reports } = parseReportMap(map);
    return el("details.hidmap", el("summary", `Report map: ${map.length / 2} bytes, ${reports.size} report IDs (decoded)`),
      el("pre.mono", lines.map((l) => "  ".repeat(l.depth) + l.text).join("\n")));
  }

  function renderGatt() {
    const g = state.gatt;
    const tree = $("#gatt-tree", root);
    tree.textContent = "";
    if (!g || !g.services) { tree.append(el("p.empty", "Connect to discover services.")); $("#gatt-sum", root).textContent = ""; return; }
    const rmap = state.hid?.map ? parseReportMap(state.hid.map).reports : null;
    const atv = (s) => s.uuid === "AB5E0001-5A21-4F05-BC7D-AF01F617B664";
    let nc = 0;
    for (const s of g.services) {
      const [sname, sdesc] = serviceInfo(s.uuid);
      const ul = el("ul");
      for (const c of s.chars || []) {
        nc++;
        const [cname, cdesc] = charInfo(c.uuid);
        let role = c.role && ROLES[c.role];
        if (c.role === "voice_audio" && !atv(s))
          role = "Vendor notifications: forwarded raw as voice frames (guess — the protocol is not public).";
        let report = null;
        if (c.report_id !== undefined) {
          const fields = rmap?.get(c.report_id) || [];
          report = el("div.cdesc", `Report ID ${c.report_id} (${c.report_type})`,
            fields.filter((f) => f.dir.toLowerCase() === c.report_type).map((f) => el("div.field", "• " + f.text)));
        }
        ul.append(el("li",
          el("div.chead", el("span.h", c.handle), el("span.u", { title: c.uuid }, shortUuid(c.uuid)), el("b", cname),
            el("span.props", { title: explainProps(c.props) }, c.props)),
          el("div.cdesc", explainProps(c.props) + (cdesc ? " — " + cdesc : "")),
          role && el("div.cdesc.role", role),
          report,
          valueLine(c),
          statsLine(c),
          c.uuid === "2A4B" && state.hid?.map && reportMapBlock(state.hid.map)));
      }
      tree.append(el("details", { open: true },
        el("summary", sname, " ", el("span.mono", s.uuid.length > 4 ? s.uuid : "0x" + s.uuid), " ",
          el("span.muted.small", `${s.start}–${s.end}`)),
        sdesc && el("p.sdesc", sdesc), ul));
    }
    const nread = [...vals.values()].filter((v) => v.ok).length;
    $("#gatt-sum", root).textContent = `${g.services.length} services · ${nc} characteristics · ${nread} values read`;
  }
  const renderGattSoon = throttle(() => { renderGatt(); renderInfo(); }, 150);

  // ---- Remote info: Device Information + the other identity facts, one tidy list -------
  const INFO = [
    ["Device name", "2A00"], ["Appearance", "2A01"], ["Manufacturer", "2A29"], ["Model number", "2A24"],
    ["Serial number", "2A25"], ["Hardware revision", "2A27"], ["Firmware revision", "2A26"],
    ["Software revision", "2A28"], ["PnP ID", "2A50"], ["System ID", "2A23"], ["Regulatory data", "2A2A"],
    ["HID", "2A4A"], ["Protocol mode", "2A4E"], ["Preferred connection", "2A04"], ["Battery", "2A19"],
  ];
  const infoRows = () => {
    const byUuid = new Map();
    for (const sv of state.gatt?.services || []) for (const c of sv.chars || []) if (!byUuid.has(c.uuid)) byUuid.set(c.uuid, c);
    const rows = [];
    const l = state.link;
    if (l.addr) {
      const sc = state.scan.get(l.addr);
      rows.push(["Address", `${l.addr}${sc?.atype ? ` (${sc.atype})` : ""}`]);
    }
    for (const [label, uuid] of INFO) {
      const c = byUuid.get(uuid);
      if (!c) continue;
      let v = vals.get(c.handle);
      let text;
      if (uuid === "2A19" && state.battery.remote) text = `${state.battery.remote.pct} %`;
      else if (!v) text = null;
      else if (!v.ok) text = "read not permitted";
      else text = (decodeValue(uuid, v.hex) ?? v.hex).replace(/^"(.*)"$/, "$1");
      rows.push([label, text]);
    }
    if (state.hid?.map) rows.push(["HID report map", `${state.hid.map_len} bytes, ${(state.hid.reports || []).length} reports`]);
    return rows;
  };

  function renderInfo() {
    const dl = $("#remote-info", root);
    dl.textContent = "";
    const rows = state.gatt ? infoRows() : [];
    state.remoteInfo = rows.length ? Object.fromEntries(rows.filter(([, v]) => v !== null)) : state.remoteInfo;
    if (!rows.length) {
      dl.append(el("dt", "—"), el("dd.muted", "Connect to a remote to read its Device Information."));
      $("#info-sum", root).textContent = "";
      return;
    }
    for (const [k, v] of rows) dl.append(el("dt", k), v === null ? el("dd.muted", "reading…") : el("dd", v));
    const missing = rows.filter(([, v]) => v === null).length;
    $("#info-sum", root).textContent = state.link.conn ? (missing ? `reading ${missing}…` : "") : "last connection";
  }
  $("#btn-info-copy", root).onclick = async () => {
    const txt = infoRows().map(([k, v]) => `${k}: ${v ?? ""}`).join("\n");
    const ok = await copyText(txt);
    toast(ok ? "Remote info copied" : "Copy failed", ok ? "ok" : "warn");
  };

  watch("scan", throttle(renderScan, 250));
  watch("link", renderLink);
  watch("link", maybeAutoRead);
  watch("gatt", () => { renderGatt(); renderInfo(); maybeAutoRead(); });
  watch("link", throttle(renderInfo, 500));
  watch("hid", renderGattSoon);
  watch("battery", () => {
    renderInfo();
    const r = state.battery.remote;
    setKV("kv-rbatt", r ? `${r.pct}%` : "—");
  });
  bus.on("log", (m) => clog(m.msg, m.level));
  // ---- Summary: a live table above the log (toggle; nothing is written into the log) ------
  const sumBox = $("#log-summary", root);
  const sumBtn = $("#btn-log-stats", root);
  const sumRows = () => [...stats.entries()].filter(([, st]) => st.notifs || st.reads).map(([h, st]) => {
    const c = charByHandle(h);
    return {
      h, name: c ? charInfo(c.uuid)[0] : "", reads: st.reads, notifs: st.notifs,
      last: st.last ? ago(Date.now() - st.last) + " ago" : "—",
      avg: st.notifs > 1 && st.lastN > st.firstN ? ago((st.lastN - st.firstN) / (st.notifs - 1)) : "—",
    };
  });
  function renderSummary() {
    const tb = $("#sum-table tbody", root);
    tb.textContent = "";
    const rows = sumRows();
    if (!rows.length) tb.append(el("tr", el("td.muted", { colspan: 6 }, "No GATT traffic yet — connect a remote.")));
    for (const r of rows) tb.append(el("tr", el("td", r.h), el("td", r.name), el("td", String(r.reads)),
      el("td", String(r.notifs)), el("td", r.last), el("td", r.avg)));
  }
  let sumTimer = null;
  function showSummary(on) {
    sumBox.hidden = !on;
    sumBtn.textContent = on ? "Hide summary" : "Summary";
    sumBtn.setAttribute("aria-pressed", String(on));
    clearInterval(sumTimer);
    sumTimer = on ? setInterval(renderSummary, 1000) : null;
    if (on) renderSummary();
  }
  sumBtn.onclick = () => showSummary(sumBox.hidden);
  $("#btn-sum-close", root).onclick = () => showSummary(false);
  sumBox.addEventListener("keydown", (e) => { if (e.key === "Escape") showSummary(false); });
  $("#btn-sum-copy", root).onclick = async () => {
    const lines = sumRows().map((r) => `${r.h}\t${r.name}\treads ${r.reads}\tnotifications ${r.notifs}\tlast ${r.last}\tavg ${r.avg}`);
    const ok = lines.length && await copyText(lines.join("\n"));
    toast(ok ? "Summary copied" : "Nothing to copy", ok ? "ok" : "warn");
  };
  bus.on("scan_done", (m) => clog(`scan finished (${m.found} devices)`));

  // ---- everything the link does, in the log (not only firmware messages) ----------------
  bus.on("link", (m) => {
    if (m.cached) return;
    if (m.what === "conn_params") clog(`link: connection interval ${m.interval_ms} ms, slave latency ${m.latency}, supervision timeout ${m.timeout_ms} ms`);
    else if (m.what === "phy") clog(`link: PHY tx ${m.tx} / rx ${m.rx}${m.status ? ` (status 0x${m.status.toString(16)})` : ""}`);
    else if (m.what === "data_length") clog(`link: data length tx ${m.tx_octets} / rx ${m.rx_octets} octets`);
    else if (m.what === "mtu") clog(`link: ATT MTU ${m.mtu} (remote offered ${m.remote_mtu})`);
  });
  let lastStage = null;
  bus.on("state", (m) => {
    if (m.cached || !m.stage || m.stage === lastStage) return;
    if (lastStage !== null) clog(`stage: ${lastStage} → ${m.stage}${m.addr ? `  (${m.addr})` : ""}`);
    lastStage = m.stage;
  });
  bus.on("passkey", (m) => {
    $("#passkey-code", root).textContent = m.code;
    $("#passkey-box", root).hidden = false;
    toast(`Passkey pairing: type ${m.code} on the remote, then OK / Enter`, "warn", 60000);
  });
  let bondSig = null;
  bus.on("bonds", (m) => {
    const sig = (m.list || []).map((b) => b.addr + (b.locked ? "*" : "")).join(",");
    if (!m.cached && bondSig !== null && sig !== bondSig)
      clog(`bonds: ${(m.list || []).map((b) => b.addr + (b.locked ? " (locked)" : "")).join(", ") || "none"}`);
    bondSig = sig;
  });
  // HID remotes: first sighting and changes (pairing mode on/off, bonded) — not every advert.
  const seenHid = new Map();
  bus.on("scan", (m) => {
    if (m.cached) return;
    const ad = parseAdv(m.adv, m.rsp);
    if (!isRemote(m, ad)) return;
    const mode = m.directed ? (m.to_jig ? "reconnecting to the jig (directed)" : `reconnecting to its host ${m.target || ""} (directed)`)
      : ad.discoverable === true ? "PAIRING mode" : ad.discoverable === false ? "NOT pairing (reconnect advert)" : "advertising";
    const sig = `${mode}|${m.bonded}|${m.name || ad.name}`;
    if (seenHid.get(m.addr) === sig) return;
    const first = !seenHid.has(m.addr);
    seenHid.set(m.addr, sig);
    clog(`${first ? "remote seen" : "remote changed"}: ${m.name || ad.name || "(no name)"} ${m.addr} — ${mode}` +
      `${m.bonded ? ", bonded to the jig" : ""}, ${m.rssi} dBm${describeAdv(ad) ? " · " + describeAdv(ad) : ""}`);
  });
  bus.on("error", (m) => clog(m.msg, "error"));
  bus.on("hid", (m) => !m.cached && clog(`HID report map ${m.map_len} B, ${m.fields} input fields, ${(m.reports || []).length} reports`));
  bus.on("flash", (m) => clog(m.msg, m.error ? "error" : "info"));
  renderScan();
  renderLink();
  renderGatt();
  renderInfo();
  maybeAutoRead();
}

function throttle(fn, ms) {
  let t = null;
  return () => { if (!t) t = setTimeout(() => { t = null; fn(); }, ms); };
}
