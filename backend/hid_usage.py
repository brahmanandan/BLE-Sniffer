"""
hid_usage.py — HID usage-ID -> canonical key name tables, plus a small HID
report-descriptor parser used as the host-side fallback decoder.

This module is the single source of truth for key names. The firmware's
`usage_names.h` is generated from it:

    python backend/hid_usage.py --emit-c > firmware/xiao_test_tool/usage_names.h

Canonical names are remote-oriented (UP, OK, VOLUME_UP, DIGIT_1 ...), so the
same physical button gets the same name whether the remote sends it on the
consumer page or the keyboard page.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

PAGE_GENERIC_DESKTOP = 0x01
PAGE_KEYBOARD = 0x07
PAGE_CONSUMER = 0x0C

PAGE_NAMES = {PAGE_GENERIC_DESKTOP: "desktop", PAGE_KEYBOARD: "keyboard", PAGE_CONSUMER: "consumer"}

CONSUMER: dict[int, str] = {
    0x30: "POWER", 0x31: "RESET", 0x32: "SLEEP", 0x34: "SLEEP_MODE",
    0x40: "MENU", 0x41: "OK", 0x42: "UP", 0x43: "DOWN", 0x44: "LEFT", 0x45: "RIGHT",
    0x46: "MENU_ESCAPE", 0x47: "MENU_VALUE_UP", 0x48: "MENU_VALUE_DOWN",
    0x60: "INFO", 0x61: "CAPTIONS", 0x63: "VCR_PLUS", 0x65: "SNAPSHOT",
    0x69: "RED", 0x6A: "GREEN", 0x6B: "BLUE", 0x6C: "YELLOW",
    0x6F: "BRIGHTNESS_UP", 0x70: "BRIGHTNESS_DOWN",
    0x80: "SELECTION", 0x81: "ASSIGN_SELECTION", 0x82: "MODE_STEP", 0x83: "LAST_CHANNEL",
    0x84: "ENTER_CHANNEL", 0x88: "INPUT_COMPUTER", 0x89: "TV", 0x8A: "WWW",
    0x8D: "GUIDE", 0x8E: "INPUT_PHONE", 0x8F: "INPUT_PROGRAM_GUIDE", 0x94: "QUIT",
    0x96: "INPUT_TUNER", 0x9C: "CHANNEL_UP", 0x9D: "CHANNEL_DOWN",
    0xA0: "VCR_PLUS_ONE", 0xB0: "PLAY", 0xB1: "PAUSE", 0xB2: "RECORD",
    0xB3: "FAST_FORWARD", 0xB4: "REWIND", 0xB5: "NEXT", 0xB6: "PREVIOUS",
    0xB7: "STOP", 0xB8: "EJECT", 0xB9: "SHUFFLE", 0xBC: "REPEAT",
    0xCD: "PLAY_PAUSE", 0xCF: "VOICE", 0xE2: "MUTE", 0xE9: "VOLUME_UP", 0xEA: "VOLUME_DOWN",
    0x173: "SUBTITLE", 0x174: "AUDIO_TRACK",
    0x183: "APP_CONFIG", 0x18A: "MAIL", 0x192: "CALCULATOR", 0x194: "FILE_BROWSER",
    0x196: "BROWSER", 0x19F: "SETTINGS", 0x1A2: "APPS", 0x1BD: "HELP",
    0x201: "NEW", 0x207: "SAVE", 0x209: "SETTINGS", 0x221: "SEARCH", 0x223: "HOME",
    0x224: "BACK", 0x225: "FORWARD", 0x226: "STOP_LOAD", 0x227: "REFRESH",
    0x22A: "BOOKMARKS", 0x22D: "ZOOM_IN", 0x22E: "ZOOM_OUT",
}

KEYBOARD: dict[int, str] = {
    **{0x04 + i: "KEY_" + chr(ord("A") + i) for i in range(26)},
    **{0x1E + i: "DIGIT_%d" % ((i + 1) % 10) for i in range(10)},
    0x28: "OK", 0x29: "BACK", 0x2A: "BACKSPACE", 0x2B: "TAB", 0x2C: "SPACE",
    0x2D: "MINUS", 0x2E: "EQUAL",
    **{0x3A + i: "F%d" % (i + 1) for i in range(12)},
    0x46: "PRINT_SCREEN", 0x48: "PAUSE", 0x49: "INSERT", 0x4A: "KB_HOME",
    0x4B: "PAGE_UP", 0x4C: "DELETE", 0x4D: "END", 0x4E: "PAGE_DOWN",
    0x4F: "RIGHT", 0x50: "LEFT", 0x51: "DOWN", 0x52: "UP",
    0x58: "OK", 0x59: "DIGIT_1", 0x5A: "DIGIT_2", 0x5B: "DIGIT_3", 0x5C: "DIGIT_4",
    0x5D: "DIGIT_5", 0x5E: "DIGIT_6", 0x5F: "DIGIT_7", 0x60: "DIGIT_8", 0x61: "DIGIT_9",
    0x62: "DIGIT_0", 0x65: "MENU", 0x66: "POWER", 0x76: "MENU", 0x7F: "MUTE",
    0x80: "VOLUME_UP", 0x81: "VOLUME_DOWN",
    **{0x68 + i: "F%d" % (13 + i) for i in range(12)},
    0xE0: "LCTRL", 0xE1: "LSHIFT", 0xE2: "LALT", 0xE3: "LGUI",
    0xE4: "RCTRL", 0xE5: "RSHIFT", 0xE6: "RALT", 0xE7: "RGUI",
}

DESKTOP: dict[int, str] = {
    0x81: "POWER", 0x82: "SLEEP", 0x83: "WAKE",
    0x90: "UP", 0x91: "DOWN", 0x92: "RIGHT", 0x93: "LEFT",
}

TABLES = {PAGE_CONSUMER: CONSUMER, PAGE_KEYBOARD: KEYBOARD, PAGE_GENERIC_DESKTOP: DESKTOP}
PAGE_BY_NAME = {v: k for k, v in PAGE_NAMES.items()}


def usage_name(page: int | str | None, usage: int) -> str:
    """Canonical name for (page, usage); '0x<id>' if unknown."""
    if isinstance(page, str):
        page = PAGE_BY_NAME.get(page)
    if page is None:
        # Unknown page: consumer is by far the most common for TV remotes.
        for p in (PAGE_CONSUMER, PAGE_KEYBOARD):
            if usage in TABLES[p]:
                return TABLES[p][usage]
        return "0x%X" % usage
    return TABLES.get(page, {}).get(usage, "0x%X" % usage)


def resolve(ev: dict) -> dict:
    """Fill in a firmware `key` event whose usage came through as '0x<id>'."""
    u = ev.get("usage")
    if isinstance(u, str) and u.lower().startswith("0x"):
        try:
            name = usage_name(ev.get("page"), int(u, 16))
        except ValueError:
            return ev
        if not name.startswith("0x"):
            ev = dict(ev, usage=name, usage_id=u)
    return ev


# ---------------------------------------------------------------------------
# Report descriptor parser (mirror of firmware hid_map.cpp)
# ---------------------------------------------------------------------------
@dataclass
class Field:
    report_id: int
    bit_offset: int
    size: int
    count: int
    page: int
    variable: bool
    usages: list[int] = field(default_factory=list)
    usage_min: int | None = None
    usage_max: int | None = None
    logical_min: int = 0


def parse_report_map(desc: bytes) -> list[Field]:
    """Input fields of a HID report descriptor (short items only)."""
    fields: list[Field] = []
    g = {"page": 0, "lmin": 0, "size": 0, "count": 0, "rid": 0}
    stack: list[dict] = []
    usages: list[int] = []
    umin = umax = None
    offsets: dict[int, int] = {}
    i = 0
    while i < len(desc):
        p = desc[i]
        if p == 0xFE:  # long item
            i += 3 + (desc[i + 1] if i + 1 < len(desc) else 0)
            continue
        sz = (0, 1, 2, 4)[p & 3]
        typ, tag = (p >> 2) & 3, p >> 4
        raw = desc[i + 1:i + 1 + sz]
        val = int.from_bytes(raw, "little") if raw else 0
        sval = int.from_bytes(raw, "little", signed=True) if raw else 0
        i += 1 + sz
        if typ == 1:  # global
            if tag == 0x0: g["page"] = val
            elif tag == 0x1: g["lmin"] = sval
            elif tag == 0x7: g["size"] = val
            elif tag == 0x8: g["rid"] = val
            elif tag == 0x9: g["count"] = val
            elif tag == 0xA: stack.append(dict(g))
            elif tag == 0xB and stack: g = stack.pop()
        elif typ == 2:  # local
            if sz == 4 and tag in (0, 1, 2):
                val &= 0xFFFF  # page in high word; we key by the global page
            if tag == 0x0: usages.append(val)
            elif tag == 0x1: umin = val
            elif tag == 0x2: umax = val
        elif typ == 0:  # main
            if tag == 0x8:  # Input
                rid = g["rid"]
                off = offsets.get(rid, 0)
                if not (val & 0x01):  # skip constant padding
                    fields.append(Field(rid, off, g["size"], g["count"], g["page"],
                                        bool(val & 0x02), list(usages), umin, umax, g["lmin"]))
                offsets[rid] = off + g["size"] * g["count"]
            usages, umin, umax = [], None, None
    return fields


def _bits(data: bytes, off: int, n: int) -> int:
    v = 0
    for k in range(n):
        b = off + k
        if b // 8 < len(data) and data[b // 8] >> (b % 8) & 1:
            v |= 1 << k
    return v


def decode_report(fields: list[Field], data: bytes, report_id: int = 0) -> list[tuple[int, int]]:
    """Active (page, usage) pairs in one input report (data excludes the report ID)."""
    out = []
    for f in fields:
        if f.report_id != report_id:
            continue
        for k in range(f.count):
            v = _bits(data, f.bit_offset + k * f.size, f.size)
            if f.variable:
                if f.size == 1 and v:
                    if f.usages:
                        u = f.usages[min(k, len(f.usages) - 1)]
                    elif f.usage_min is not None:
                        u = f.usage_min + k
                    else:
                        continue
                    out.append((f.page, u))
            elif v:
                idx = v - f.logical_min
                # The value indexes the field's usage range / list. No usages declared (e.g. LG
                # Magic Remote vendor streams) = opaque data, not key codes.
                if f.usage_min is not None:
                    u = f.usage_min + idx
                elif f.usages and 0 <= idx < len(f.usages):
                    u = f.usages[idx]
                else:
                    continue
                if u:
                    out.append((f.page, u))
    return out


# ---------------------------------------------------------------------------
# C header generation for the firmware
# ---------------------------------------------------------------------------
def emit_c() -> str:
    lines = [
        "// Generated by backend/hid_usage.py --emit-c. Do not edit by hand.",
        "#pragma once",
        "#include <stdint.h>",
        "",
        "struct UsageName { uint8_t page; uint16_t usage; const char* name; };",
        "",
        "static const UsageName USAGE_NAMES[] = {",
    ]
    for page, table in sorted(TABLES.items()):
        for u, n in sorted(table.items()):
            lines.append('  {0x%02X, 0x%04X, "%s"},' % (page, u, n))
    lines += ["};", "", "static const unsigned USAGE_NAMES_COUNT = sizeof(USAGE_NAMES) / sizeof(USAGE_NAMES[0]);", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    if "--emit-c" in sys.argv:
        sys.stdout.write(emit_c())
    else:
        print(__doc__)
