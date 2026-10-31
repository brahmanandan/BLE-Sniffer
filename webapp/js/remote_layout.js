// remote_layout.js — data for the visual remote and the "press every button" test.
//
// Each button lists the canonical usage names (backend/hid_usage.py) that light it.
// `expect: false` buttons are drawn but not required by the Keys test.
// Rows are drawn top to bottom; `cols` sets the grid for that row.
//
// Unknown vendor keys (e.g. app shortcuts) can be bound at runtime with
// Keys ▸ "Map key"; those bindings are stored per browser and merged in here.

export const REMOTE_NAME = "Android TV / Amazon Firestick style BLE remote";

export const ROWS = [
  { cols: 3, keys: [
    { id: "power", label: "⏻", usages: ["POWER", "SLEEP"], cls: "round" },
    { id: "input", label: "INPUT", usages: ["INPUT_TUNER", "MODE_STEP", "TV"], expect: false },
    { id: "voice", label: "🎤", usages: ["VOICE", "SEARCH"], cls: "round" },
  ]},
  { cols: 3, keys: [
    { id: "d1", label: "1", usages: ["DIGIT_1"], expect: false },
    { id: "d2", label: "2", usages: ["DIGIT_2"], expect: false },
    { id: "d3", label: "3", usages: ["DIGIT_3"], expect: false },
    { id: "d4", label: "4", usages: ["DIGIT_4"], expect: false },
    { id: "d5", label: "5", usages: ["DIGIT_5"], expect: false },
    { id: "d6", label: "6", usages: ["DIGIT_6"], expect: false },
    { id: "d7", label: "7", usages: ["DIGIT_7"], expect: false },
    { id: "d8", label: "8", usages: ["DIGIT_8"], expect: false },
    { id: "d9", label: "9", usages: ["DIGIT_9"], expect: false },
    { id: "guide", label: "GUIDE", usages: ["GUIDE", "INPUT_PROGRAM_GUIDE"], expect: false },
    { id: "d0", label: "0", usages: ["DIGIT_0"], expect: false },
    { id: "info", label: "INFO", usages: ["INFO"], expect: false },
  ]},
  { cols: 3, keys: [
    { id: "spacer1", spacer: true },
    { id: "up", label: "▲", usages: ["UP"], cls: "dpad" },
    { id: "spacer2", spacer: true },
    { id: "left", label: "◀", usages: ["LEFT"], cls: "dpad" },
    { id: "ok", label: "OK", usages: ["OK", "SELECTION", "ENTER_CHANNEL"], cls: "round ok" },
    { id: "right", label: "▶", usages: ["RIGHT"], cls: "dpad" },
    { id: "spacer3", spacer: true },
    { id: "down", label: "▼", usages: ["DOWN"], cls: "dpad" },
    { id: "spacer4", spacer: true },
  ]},
  { cols: 3, keys: [
    { id: "back", label: "↩ BACK", usages: ["BACK", "MENU_ESCAPE", "BACKSPACE"] },
    { id: "home", label: "⌂ HOME", usages: ["HOME", "KB_HOME"] },
    { id: "menu", label: "☰ MENU", usages: ["MENU", "SETTINGS", "APPS"] },
  ]},
  { cols: 3, keys: [
    { id: "volup", label: "VOL +", usages: ["VOLUME_UP"] },
    { id: "mute", label: "MUTE", usages: ["MUTE"] },
    { id: "chup", label: "CH +", usages: ["CHANNEL_UP", "PAGE_UP"], expect: false },
    { id: "voldn", label: "VOL −", usages: ["VOLUME_DOWN"] },
    { id: "play", label: "⏯", usages: ["PLAY_PAUSE", "PLAY", "PAUSE"], expect: false },
    { id: "chdn", label: "CH −", usages: ["CHANNEL_DOWN", "PAGE_DOWN"], expect: false },
  ]},
  { cols: 3, keys: [
    { id: "rew", label: "⏪", usages: ["REWIND", "SCAN_PREVIOUS"], expect: false },
    { id: "spacer5", spacer: true },
    { id: "ff", label: "⏩", usages: ["FAST_FORWARD", "SCAN_NEXT"], expect: false },
  ]},
  { cols: 2, keys: [
    { id: "app1", label: "APP 1", usages: [], expect: false },
    { id: "app2", label: "APP 2", usages: [], expect: false },
    { id: "app3", label: "APP 3", usages: [], expect: false },
    { id: "app4", label: "APP 4", usages: [], expect: false },
  ]},
];

/** All real buttons (no spacers), with runtime mappings and the remote profile merged in.
 *  A profile (profile.js) may list the expected button ids and extra usages per button. */
export function buttons(extraMap = {}, profile = null) {
  const out = [];
  for (const r of ROWS) for (const k of r.keys) {
    if (k.spacer) continue;
    const extra = [...(extraMap[k.id] || []), ...(profile?.extra?.[k.id] || [])];
    const expect = profile?.expect ? profile.expect.includes(k.id) : k.expect !== false;
    out.push({ ...k, expect, usages: [...k.usages, ...extra] });
  }
  return out;
}

/** usage name -> button id */
export function usageIndex(extraMap = {}, profile = null) {
  const idx = new Map();
  for (const b of buttons(extraMap, profile)) for (const u of b.usages) if (!idx.has(u)) idx.set(u, b.id);
  return idx;
}
