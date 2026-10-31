// profile.js — per-remote test profiles: which buttons a remote model must have, extra
// key codes for vendor keys, and how (or whether) its voice can be tested.
//
// "auto" picks a profile from what the connected remote exposes over GATT; the operator can
// override it on the Test tab. Keys and Test both read currentProfile().

import { state, store, notify } from "./state.js";

const ATV_VOICE = "AB5E0001-5A21-4F05-BC7D-AF01F617B664";
const LG_SVCS = ["0000D0FF-3C17-D293-8E48-14FE2E4DA212", "0000D1FF-3C17-D293-8E48-14FE2E4DA212"];
const AMAZON_SVCS = ["FE151500-5E8D-11E6-8B77-86F30CA893D3", "5DE20000-5E8D-11E6-8B77-86F30CA893D3",
  "CFBFA000-762C-4912-A043-20E3ECDE0A2D"];

export const PROFILES = {
  atv: {
    name: "Android TV voice remote",
    voice: "atv",
    extra: {},
    expect: null,                        // the drawing's defaults (dashed buttons optional)
  },
  firetv: {
    name: "Fire TV remote",
    voice: "none",
    voiceNote: "Fire TV voice uses Amazon's own protocol — not supported yet (the mic arrives as SEARCH)",
    // vendor codes seen on the Fire TV remote: Back = keyboard 0xF1, app keys = vendor report 239
    extra: { back: ["0xF1"], app1: ["0xA1"], app2: ["0xA2"], app3: ["0xA3"], app4: ["0xA4"] },
    expect: ["voice", "up", "down", "left", "right", "ok", "back", "home", "menu",
      "rew", "play", "ff", "volup", "voldn", "mute"],
  },
  lg: {
    // Keys are decoded by the bridge (backend/lg_magic.py): LG TV IR command codes inside the
    // motion stream of report 0xFD. Unknown codes arrive as LG_0x.. — bind them with Map key.
    name: "LG Magic Remote",
    voice: "none",
    voiceNote: "LG voice uses its own protocol — not supported yet",
    extra: {},
    expect: ["up", "down", "left", "right", "ok", "back", "home", "volup", "voldn", "chup", "chdn"],
  },
  generic: {
    name: "Generic BLE HID remote (keys only)",
    voice: "none",
    voiceNote: "no Android TV Voice service on this remote",
    extra: {},
    expect: ["up", "down", "left", "right", "ok", "back", "home", "volup", "voldn"],
  },
};

/** Best guess from the connected remote's GATT table (falls back to the Android TV layout). */
export function detectProfile() {
  const uuids = (state.gatt?.services || []).map((s) => s.uuid);
  if (!uuids.length) return "atv";
  if (uuids.includes(ATV_VOICE)) return "atv";
  if (uuids.some((u) => AMAZON_SVCS.includes(u))) return "firetv";
  if (uuids.some((u) => LG_SVCS.includes(u))) return "lg";
  const info = state.remoteInfo || {};
  if (/amazon/i.test(`${info.Manufacturer || ""} ${info["PnP ID"] || ""}`)) return "firetv";
  return "generic";
}

export const profileChoice = () => store.get("profile", "auto");

/** {id, auto, name, voice, voiceNote, extra, expect} */
export function currentProfile() {
  const choice = profileChoice();
  const id = choice === "auto" || !PROFILES[choice] ? detectProfile() : choice;
  return { id, auto: choice === "auto" || !PROFILES[choice], ...PROFILES[id] };
}

export function setProfileChoice(choice) {
  store.set("profile", choice);
  notify("profile");
}
