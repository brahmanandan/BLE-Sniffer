// gatt_info.js — names, explanations and value decoders for what a remote exposes over GATT,
// plus a HID report-map parser. Pure data/functions; tab_connect.js renders them.

export const SERVICES = {
  "1800": ["Generic Access", "Device name, appearance and preferred connection parameters."],
  "1801": ["Generic Attribute", "Tells the host when the GATT table changed (Service Changed)."],
  "180A": ["Device Information", "Manufacturer, model, serial and firmware/hardware versions."],
  "180F": ["Battery", "Remote's own battery level, 0–100 %. Read on connect, then notified on change."],
  "1812": ["Human Interface Device", "HID over GATT: key presses arrive here as input reports."],
  "AB5E0001-5A21-4F05-BC7D-AF01F617B664": ["Android TV Voice", "Google ATV Voice: mic control + ADPCM audio stream."],
};

export const CHARS = {
  "2A00": ["Device Name", "The name the remote reports to the host."],
  "2A01": ["Appearance", "Device category (e.g. remote control, keyboard)."],
  "2A04": ["Peripheral Preferred Connection Parameters", "Connection interval, latency and timeout the remote would like."],
  "2A05": ["Service Changed", "Indication-only: tells a bonded host to re-discover handles."],
  "2AA6": ["Central Address Resolution", "Whether the remote resolves private addresses of the host."],
  "2A19": ["Battery Level", "Battery percentage (0–100)."],
  "2A29": ["Manufacturer Name", ""],
  "2A24": ["Model Number", ""],
  "2A25": ["Serial Number", ""],
  "2A27": ["Hardware Revision", ""],
  "2A26": ["Firmware Revision", ""],
  "2A28": ["Software Revision", ""],
  "2A23": ["System ID", "Manufacturer-defined identifier + OUI."],
  "2A2A": ["IEEE 11073-20601 Regulatory Certification", "Certification data list (raw)."],
  "2A50": ["PnP ID", "Vendor ID source, vendor ID, product ID and product version."],
  "2A4A": ["HID Information", "HID spec version, country code and wake/connectable flags."],
  "2A4B": ["Report Map", "Describes every report: which keys/usages each byte carries."],
  "2A4C": ["HID Control Point", "Write-only: 0 = suspend, 1 = exit suspend."],
  "2A4D": ["Report", "One HID report; the Report Reference descriptor gives its ID and type."],
  "2A4E": ["Protocol Mode", "0 = Boot protocol, 1 = Report protocol."],
  "2A22": ["Boot Keyboard Input Report", "Keyboard report used in Boot protocol mode."],
  "2A32": ["Boot Keyboard Output Report", ""],
  "AB5E0002-5A21-4F05-BC7D-AF01F617B664": ["ATV Voice TX", "Host → remote commands (GET_CAPS, MIC_OPEN, MIC_CLOSE)."],
  "AB5E0003-5A21-4F05-BC7D-AF01F617B664": ["ATV Voice Audio", "Audio frames (IMA-ADPCM) while the mic is open."],
  "AB5E0004-5A21-4F05-BC7D-AF01F617B664": ["ATV Voice Control", "Remote → host events: caps, audio start/stop, search key."],
};

export const PROPS = { R: "Read", W: "Write (with response)", w: "Write without response", N: "Notify", I: "Indicate", B: "Broadcast", X: "Extended properties" };

export const ROLES = {
  hid_report: "Input reports are decoded into key presses (Keys tab).",
  hid_boot_kbd: "Boot-protocol keyboard report.",
  hid_map: "Read once at connect; used to name keys.",
  battery: "Shown as REMOTE BATTERY in the banner.",
  voice_audio: "Notifications are forwarded as voice frames (Voice tab).",
  voice_ctl: "Voice control events (Voice tab ▸ Stream events).",
  voice_tx: "The jig writes voice commands here.",
};

// Bluetooth SIG company IDs and USB vendor IDs commonly seen on TV remotes.
const BT_COMPANY = { 0x0171: "Amazon.com Services", 0x00E0: "Google", 0x005D: "Realtek", 0x0046: "MediaTek",
  0x000D: "Texas Instruments", 0x0059: "Nordic Semiconductor", 0x000A: "Qualcomm", 0x0075: "Samsung", 0x004C: "Apple",
  0x0087: "Garmin", 0x02E5: "Espressif", 0x0131: "Cypress", 0x0002: "Intel", 0x001D: "Qualcomm", 0x038F: "Xiaomi" };
const USB_VENDOR = { 0x1949: "Lab126 (Amazon)", 0x18D1: "Google", 0x0BDA: "Realtek", 0x2717: "Xiaomi", 0x04E8: "Samsung" };
const APPEARANCE = { 0: "Unknown", 0x0180: "Generic Remote Control", 0x03C0: "Generic HID", 0x03C1: "Keyboard",
  0x03C2: "Mouse", 0x03C3: "Joystick", 0x03C4: "Gamepad", 0x0040: "Generic Phone", 0x00C0: "Generic Watch" };

export const shortUuid = (u) => (u.length > 4 ? u.slice(0, 8) + "…" : u);

export function serviceInfo(uuid) {
  return SERVICES[uuid] || (uuid.length > 4
    ? ["Vendor-specific service", "Not a Bluetooth SIG service; its protocol is defined by the remote's maker."]
    : ["Service 0x" + uuid, ""]);
}

export function charInfo(uuid) {
  return CHARS[uuid] || (uuid.length > 4 ? ["Vendor characteristic", ""] : ["0x" + uuid, ""]);
}

export function explainProps(p) {
  return [...(p || "")].map((ch) => PROPS[ch] || ch).join(", ");
}

const bytesOf = (hex) => (hex || "").match(/../g)?.map((x) => parseInt(x, 16)) || [];
const u16 = (b, i) => b[i] | (b[i + 1] << 8);
const hx = (v, n = 4) => "0x" + v.toString(16).toUpperCase().padStart(n, "0");
const utf8 = (b) => { try { return new TextDecoder().decode(new Uint8Array(b)).replace(/\0+$/, ""); } catch { return null; } };

/** Decode a characteristic value. Returns a string, or null to fall back to hex. */
export function decodeValue(uuid, hex) {
  const b = bytesOf(hex);
  if (!b.length) return "(empty)";
  switch (uuid) {
    case "2A00": case "2A24": case "2A25": case "2A26": case "2A27": case "2A28": case "2A29":
      return JSON.stringify(utf8(b));
    case "2A01": {
      const v = u16(b, 0);
      return `${APPEARANCE[v] || "category " + (v >> 6)} (${hx(v)})`;
    }
    case "2A04": {
      if (b.length < 8) return null;
      const [mn, mx, lat, to] = [u16(b, 0), u16(b, 2), u16(b, 4), u16(b, 6)];
      const ms = (v) => (v === 0xFFFF ? "no preference" : `${(v * 1.25).toFixed(2).replace(/\.?0+$/, "")} ms`);
      return `interval ${ms(mn)} – ${ms(mx)}, slave latency ${lat}, supervision timeout ${to === 0xFFFF ? "no preference" : to * 10 + " ms"}`;
    }
    case "2AA6": return b[0] ? "supported" : "not supported";
    case "2A19": return `${b[0]} %`;
    case "2A23": {
      if (b.length < 8) return null;
      const mfr = b.slice(0, 5).reverse().map((x) => x.toString(16).padStart(2, "0")).join("");
      const oui = b.slice(5, 8).reverse().map((x) => x.toString(16).padStart(2, "0")).join(":");
      return `manufacturer id 0x${mfr.toUpperCase()}, OUI ${oui.toUpperCase()}`;
    }
    case "2A50": {
      if (b.length < 7) return null;
      const src = b[0], vid = u16(b, 1), pid = u16(b, 3), ver = u16(b, 5);
      // Some remotes mislabel the source: say so when a "USB" id is really a Bluetooth SIG company id.
      const vname = src === 1 ? BT_COMPANY[vid]
        : src === 2 ? USB_VENDOR[vid] || (BT_COMPANY[vid] && `= Bluetooth SIG id of ${BT_COMPANY[vid]}`) : null;
      return `vendor ${hx(vid)}${vname ? " " + vname : ""} (${src === 1 ? "Bluetooth SIG id" : src === 2 ? "USB id" : "source " + src}), ` +
        `product ${hx(pid)}, version ${hx(ver)}`;
    }
    case "2A4A": {
      if (b.length < 4) return null;
      const bcd = u16(b, 0);
      const flags = [b[3] & 1 && "remote wake", b[3] & 2 && "normally connectable"].filter(Boolean);
      return `HID ${(bcd >> 8).toString(16)}.${(bcd & 0xFF).toString(16).padStart(2, "0")}, country ${b[2] || "none"}` +
        (flags.length ? `, ${flags.join(", ")}` : "");
    }
    case "2A4E": return b[0] === 0 ? "Boot protocol (0)" : b[0] === 1 ? "Report protocol (1)" : `0x${b[0].toString(16)}`;
    default: {
      const t = utf8(b);
      return t && /^[\x20-\x7e]{3,}$/.test(t) ? JSON.stringify(t) : null;
    }
  }
}

// ---------------------------------------------------------------------------
// Advertising data (adv + scan response hex from the firmware's `scan` event)
// ---------------------------------------------------------------------------
const SVC16 = { 0x1812: "HID", 0x180F: "Battery", 0x180A: "Device Info", 0xFE15: "Amazon", 0xFEF3: "Google" };

/** Decode AD structures. Flags come only from the advert (`adv`), never the scan response. */
export function parseAdv(advHex, rspHex = "") {
  const out = { flags: null, discoverable: null, name: "", appearance: null, mfr: null, uuids: [], tx: null };
  for (const [hex, isAdv] of [[advHex, true], [rspHex, false]]) {
    const b = bytesOf(hex);
    for (let i = 0; i + 1 < b.length && b[i];) {
      const len = b[i], type = b[i + 1], d = b.slice(i + 2, i + 1 + len);
      if (type === 0x01 && isAdv && d.length) { out.flags = d[0]; out.discoverable = (d[0] & 0x03) !== 0; }
      else if ((type === 0x08 || type === 0x09) && !out.name) out.name = utf8(d) || "";
      else if (type === 0x19 && d.length >= 2) out.appearance = u16(d, 0);
      else if (type === 0x0A && d.length) out.tx = (d[0] << 24) >> 24;
      else if ((type === 0x02 || type === 0x03)) for (let j = 0; j + 1 < d.length; j += 2) out.uuids.push(u16(d, j));
      else if ((type === 0x06 || type === 0x07) && d.length >= 16) out.uuids.push("128-bit");
      else if (type === 0xFF && d.length >= 2) {
        const id = u16(d, 0);
        out.mfr = { id, name: BT_COMPANY[id] || null, data: d.slice(2).map((x) => x.toString(16).padStart(2, "0")).join("") };
      }
      i += len + 1;
    }
  }
  return out;
}

/** One readable line for a decoded advert. */
export function describeAdv(a) {
  const parts = [];
  if (a.flags !== null) parts.push(a.discoverable ? `discoverable (flags ${hx(a.flags, 2)})` : `not discoverable (flags ${hx(a.flags, 2)})`);
  if (a.appearance !== null) parts.push(APPEARANCE[a.appearance] || `appearance ${hx(a.appearance)}`);
  if (a.mfr) parts.push(`maker ${a.mfr.name || hx(a.mfr.id)}`);
  if (a.uuids.length) parts.push("services " + a.uuids.map((u) => (typeof u === "number" ? SVC16[u] || hx(u) : u)).join(", "));
  if (a.tx !== null) parts.push(`TX ${a.tx} dBm`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// HID report map
// ---------------------------------------------------------------------------
const PAGES = { 0x01: "Generic Desktop", 0x07: "Keyboard", 0x08: "LEDs", 0x09: "Button", 0x0C: "Consumer" };
const DESKTOP = { 0x01: "Pointer", 0x02: "Mouse", 0x04: "Joystick", 0x05: "Gamepad", 0x06: "Keyboard", 0x30: "X", 0x31: "Y", 0x38: "Wheel", 0x80: "System Control" };
const CONSUMER = { 0x01: "Consumer Control" };
const COLL = ["Physical", "Application", "Logical", "Report", "Named Array", "Usage Switch", "Usage Modifier"];
const pageName = (p) => PAGES[p] || (p >= 0xFF00 ? `Vendor ${hx(p)}` : `page ${hx(p, 2)}`);
const usageName = (p, u) => (p === 0x01 && DESKTOP[u]) || (p === 0x0C && CONSUMER[u]) || hx(u, 2);

/**
 * Parse a HID report map (hex). Returns {lines: [{depth, text}], reports: Map<id, [{dir, text}]>}.
 * `reports` summarises, per report ID, which usages each field carries.
 */
export function parseReportMap(hex) {
  const b = bytesOf(hex);
  const lines = [], reports = new Map();
  let depth = 0, page = 0, size = 0, count = 0, id = 0, lmin = 0, lmax = 0;
  let usages = [], umin = null, umax = null;
  for (let i = 0; i < b.length;) {
    const pre = b[i];
    let n = pre & 3; if (n === 3) n = 4;
    const type = (pre >> 2) & 3, tag = pre >> 4;
    let v = 0;
    for (let k = 0; k < n; k++) v |= b[i + 1 + k] << (8 * k);
    v >>>= 0;
    i += 1 + n;
    let text;
    if (type === 0) {               // main
      if (tag === 0x8 || tag === 0x9 || tag === 0xB) {
        const dir = tag === 0x8 ? "Input" : tag === 0x9 ? "Output" : "Feature";
        const flags = `${v & 1 ? "Const" : "Data"},${v & 2 ? "Var" : "Array"},${v & 4 ? "Rel" : "Abs"}`;
        text = `${dir} (${flags})`;
        let what;
        if (v & 1) what = `${count * size} bits padding`;
        else {
          const range = umin !== null
            ? (page === 0x07 && umin === 0xE0 && umax === 0xE7 ? "modifiers (Ctrl/Shift/Alt/GUI)" : `${hx(umin, 2)}–${hx(umax, 2)}`)
            : usages.length ? usages.map((u) => usageName(page, u)).join(", ") : "";
          what = `${pageName(page)}${range ? " " + range : ""}: ${count} × ${size} bit ${v & 2 ? "(one bit/value per usage)" : `(array, values ${hx(lmin, 2)}–${hx(lmax, 2)})`}`;
        }
        if (!reports.has(id)) reports.set(id, []);
        reports.get(id).push({ dir, text: what });
      } else if (tag === 0xA) { text = `Collection (${COLL[v] || hx(v, 2)})`; }
      else if (tag === 0xC) { depth = Math.max(0, depth - 1); text = "End Collection"; }
      else text = `Main ${hx(tag, 1)} ${hx(v, 2)}`;
      if (tag !== 0xC) lines.push({ depth, text });
      else lines.push({ depth, text });
      if (tag === 0xA) depth++;
      usages = []; umin = umax = null;
      continue;
    }
    if (type === 1) {               // global
      switch (tag) {
        case 0x0: page = v; text = `Usage Page (${pageName(v)})`; break;
        case 0x1: lmin = v; text = `Logical Minimum (${v})`; break;
        case 0x2: lmax = v; text = `Logical Maximum (${v})`; break;
        case 0x7: size = v; text = `Report Size (${v})`; break;
        case 0x8: id = v; text = `Report ID (${v})`; break;
        case 0x9: count = v; text = `Report Count (${v})`; break;
        default: text = `Global ${hx(tag, 1)} (${v})`;
      }
    } else if (type === 2) {        // local
      if (tag === 0x0) { usages.push(v); text = `Usage (${usageName(page, v)})`; }
      else if (tag === 0x1) { umin = v; text = `Usage Minimum (${usageName(page, v)})`; }
      else if (tag === 0x2) { umax = v; text = `Usage Maximum (${usageName(page, v)})`; }
      else text = `Local ${hx(tag, 1)} (${v})`;
    } else text = `Reserved ${hx(pre, 2)}`;
    lines.push({ depth, text });
  }
  return { lines, reports };
}
