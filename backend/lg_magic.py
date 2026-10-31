"""
lg_magic.py — key decoding for LG Magic Remotes.

An LG Magic Remote declares all its HID reports as opaque Consumer-page byte arrays (no usages), so the
standard report-map decoder rightly sees no keys in them. Its keys travel inside the ~100 Hz motion
stream on report 0xFD (19 bytes, observed on F4:83:2C:6F:C4:98, 2026-10-29):

    byte 0          sequence / timer
    byte 2-3        0x9D 0x02 (constant)
    bytes 4-15      6 x 16-bit motion values (gyro + accelerometer)
    byte 16         bit 7 set while a key is held
    byte 17         key code — LG's TV infrared command code (NEC address 0x04 command set)

The bridge feeds every `hid_data` event of an LG remote through KeyDecoder, which turns press/release
into ordinary `key` events. Codes missing from the table below are reported as "LG_0x56" etc. and can
be bound with Keys ▸ Map key.
"""
from __future__ import annotations

from typing import Any

LG_SERVICES = ("0000D0FF-3C17-D293-8E48-14FE2E4DA212", "0000D1FF-3C17-D293-8E48-14FE2E4DA212")
REPORT_ID = 0xFD

# LG TV IR command codes (well known for LG TVs); names follow backend/hid_usage.py where one exists,
# so the visual remote and the Keys test recognise them without any mapping.
LG_KEYS: dict[int, str] = {
    0x00: "CHANNEL_UP", 0x01: "CHANNEL_DOWN", 0x02: "VOLUME_UP", 0x03: "VOLUME_DOWN",
    0x06: "RIGHT", 0x07: "LEFT", 0x08: "POWER", 0x09: "MUTE", 0x0B: "INPUT_TUNER", 0x0E: "SLEEP",
    0x10: "DIGIT_0", 0x11: "DIGIT_1", 0x12: "DIGIT_2", 0x13: "DIGIT_3", 0x14: "DIGIT_4",
    0x15: "DIGIT_5", 0x16: "DIGIT_6", 0x17: "DIGIT_7", 0x18: "DIGIT_8", 0x19: "DIGIT_9",
    0x1A: "LAST_CHANNEL", 0x1E: "FAVORITES", 0x28: "BACK", 0x39: "CAPTIONS",
    0x40: "UP", 0x41: "DOWN", 0x43: "SETTINGS", 0x44: "OK", 0x45: "MENU", 0x4C: "LIST", 0x5B: "EXIT",
    0x61: "BLUE", 0x63: "YELLOW", 0x71: "GREEN", 0x72: "RED", 0x79: "ASPECT", 0x7C: "HOME",
    0x8E: "FAST_FORWARD", 0x8F: "REWIND", 0xAA: "INFO", 0xAB: "GUIDE",
    0xB0: "PLAY", 0xB1: "STOP", 0xBA: "PAUSE", 0xBD: "RECORD",
}


def is_lg(gatt_event: dict | None) -> bool:
    """True if the remote's GATT table has LG's vendor services."""
    return bool(gatt_event) and any(s.get("uuid") in LG_SERVICES for s in gatt_event.get("services") or [])


class KeyDecoder:
    """Press/release tracking for one LG remote (state per report handle)."""

    def __init__(self) -> None:
        self.held: dict[str, int | None] = {}

    def feed(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        """`hid_data` event -> zero or more `key` events."""
        if ev.get("rid") != REPORT_ID:
            return []
        try:
            b = bytes.fromhex(ev.get("bytes", ""))
        except ValueError:
            return []
        if len(b) < 18:
            return []
        handle = str(ev.get("handle"))
        code = b[17] if b[16] & 0x80 else None
        prev = self.held.get(handle)
        if code == prev:
            return []
        self.held[handle] = code
        out = []
        report = " ".join("%02X" % x for x in b)
        base = {"ev": "key", "handle": handle, "rid": REPORT_ID, "page": "lg", "report": report,
                "t": ev.get("t"), "host_ts": ev.get("host_ts"), "decoder": "lg_magic"}
        if prev is not None:
            out.append(dict(base, usage=LG_KEYS.get(prev, "LG_0x%02X" % prev), usage_id="0x%02X" % prev, down=False))
        if code is not None:
            out.append(dict(base, usage=LG_KEYS.get(code, "LG_0x%02X" % code), usage_id="0x%02X" % code, down=True))
        return out
