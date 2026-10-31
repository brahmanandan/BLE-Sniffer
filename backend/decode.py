"""
decode.py — BLE PDU decoders used by ble_probe.py (the passive-capture CLI).

The web app has no sniffer path (passive capture is Wireshark, see README); the
bridge only borrows the address helpers here for pretty-printing.

The lookup tables and decode_* functions are lifted unchanged from the original
ble_probe.py. What is new:

  * pdu_of(pkt)      — rebuild the full link-layer PDU (header, length, payload)
                       from a SnifferAPI packet. SnifferAPI's blePacket.payload
                       starts *after* the 2-byte header and carries the 3-byte
                       CRC at the end, while the decoders below index a full PDU
                       (payload[0] = header, payload[2:8] = AdvA, ...).
  * packet_event()   — one SnifferAPI packet -> a dict (ch, rssi, dir, kind, text, enc).
  * format_packet()  — the one-line terminal rendering used by ble_probe.py.

Every decoder is defensive: a short or odd packet must never crash the loop.
"""
from __future__ import annotations

import time
from typing import Any

# ---------------------------------------------------------------------------
# Lookup tables for decoding
# ---------------------------------------------------------------------------
ADV_PDU = {
    0x0: "ADV_IND", 0x1: "ADV_DIRECT_IND", 0x2: "ADV_NONCONN_IND",
    0x3: "SCAN_REQ", 0x4: "SCAN_RSP", 0x5: "CONNECT_IND",
    0x6: "ADV_SCAN_IND", 0x7: "ADV_EXT_IND", 0x8: "AUX_CONNECT_RSP",
}

AD_TYPE = {
    0x01: "Flags", 0x02: "UUID16(part)", 0x03: "UUID16", 0x06: "UUID128(part)",
    0x07: "UUID128", 0x08: "ShortName", 0x09: "Name", 0x0A: "TxPower",
    0x19: "Appearance", 0x16: "ServiceData16", 0xFF: "ManufData",
}

LL_CONTROL = {
    0x00: "LL_CONNECTION_UPDATE_IND", 0x01: "LL_CHANNEL_MAP_IND",
    0x02: "LL_TERMINATE_IND", 0x03: "LL_ENC_REQ", 0x04: "LL_ENC_RSP",
    0x05: "LL_START_ENC_REQ", 0x06: "LL_START_ENC_RSP", 0x07: "LL_UNKNOWN_RSP",
    0x08: "LL_FEATURE_REQ", 0x09: "LL_FEATURE_RSP", 0x0A: "LL_PAUSE_ENC_REQ",
    0x0B: "LL_PAUSE_ENC_RSP", 0x0C: "LL_VERSION_IND", 0x0D: "LL_REJECT_IND",
    0x12: "LL_PING_REQ", 0x13: "LL_PING_RSP", 0x14: "LL_LENGTH_REQ",
    0x15: "LL_LENGTH_RSP", 0x16: "LL_PHY_REQ", 0x17: "LL_PHY_RSP",
    0x18: "LL_PHY_UPDATE_IND",
}

ATT_OPCODE = {
    0x01: "ERROR_RSP", 0x02: "EXCHANGE_MTU_REQ", 0x03: "EXCHANGE_MTU_RSP",
    0x04: "FIND_INFO_REQ", 0x05: "FIND_INFO_RSP", 0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RSP", 0x0A: "READ_REQ", 0x0B: "READ_RSP",
    0x10: "READ_BY_GROUP_REQ", 0x11: "READ_BY_GROUP_RSP", 0x12: "WRITE_REQ",
    0x13: "WRITE_RSP", 0x1B: "HANDLE_VALUE_NTF", 0x1D: "HANDLE_VALUE_IND",
    0x1E: "HANDLE_VALUE_CFM", 0x52: "WRITE_CMD",
}

ADV_CHANNELS = (37, 38, 39)


def hexs(data):
    return " ".join("%02X" % b for b in data)


def fmt_addr(addr):
    """Device.address is [b0..b5, isRandom]; BLE prints MSB first."""
    try:
        return ":".join("%02X" % b for b in addr[5::-1])
    except Exception:
        return str(addr)


def fmt_dev_addr(addr):
    """SnifferAPI Device.address / blePacket.advAddress: already MSB first ([b5..b0, isRandom]).

    (fmt_addr is for over-the-air byte order; applying it to these lists prints the
    address byte-reversed, which the original ble_probe.py did.)
    """
    try:
        return ":".join("%02X" % b for b in addr[:6])
    except Exception:
        return str(addr)


def parse_addr(text: str) -> list[int]:
    """'1E:AF:80:B1:74:60' -> [0x1E, 0xAF, ...] (MSB first, as printed)."""
    return [int(x, 16) for x in text.upper().replace("-", ":").split(":")]


# ---------------------------------------------------------------------------
# Decoders — all defensive; a short/odd packet must never crash the loop.
# ---------------------------------------------------------------------------
def decode_adv(payload):
    if len(payload) < 2:
        return "adv (short)"
    pdu = ADV_PDU.get(payload[0] & 0x0F, "ADV?%X" % (payload[0] & 0x0F))
    out = [pdu]
    if (payload[0] & 0x0F) == 0x5 and len(payload) >= 14:
        # CONNECT_IND carries InitA then AdvA (the original decoder labelled InitA as AdvA)
        out.append("InitA=%s AdvA=%s" % (fmt_addr(payload[2:8] + [0]), fmt_addr(payload[8:14] + [0])))
        return "  ".join(out)
    if len(payload) >= 8 and (payload[0] & 0x0F) in (0x0, 0x1, 0x2, 0x4, 0x5, 0x6):
        out.append("AdvA=%s" % fmt_addr(payload[2:8] + [0]))
        out.append(decode_ad_structs(payload[8:]))
    return "  ".join(p for p in out if p)


def decode_ad_structs(data):
    i, fields = 0, []
    while i < len(data):
        ln = data[i]
        if ln == 0 or i + ln >= len(data) + 1:
            break
        t = data[i + 1] if i + 1 < len(data) else None
        val = data[i + 2: i + 1 + ln]
        name = AD_TYPE.get(t, "0x%02X" % t if t is not None else "?")
        if t in (0x08, 0x09):
            try:
                fields.append('%s="%s"' % (name, bytes(val).decode("utf-8", "replace")))
            except Exception:
                fields.append("%s=%s" % (name, hexs(val)))
        else:
            fields.append("%s=%s" % (name, hexs(val)))
        i += 1 + ln
    return " ".join(fields)


def decode_data(payload):
    """Data-channel PDU: LL control, or L2CAP/ATT (button presses live here)."""
    if len(payload) < 2:
        return "data (short)"
    llid = payload[0] & 0x03
    body = payload[2:]

    if llid == 0b11:  # LL control PDU
        if not body:
            return "CTRL (empty)"
        op = LL_CONTROL.get(body[0], "LL_?0x%02X" % body[0])
        rest = hexs(body[1:]) if len(body) > 1 else ""
        return "CTRL  %s %s" % (op, rest)

    if llid in (0b01, 0b10):  # L2CAP start / continuation
        if len(body) < 4:
            return "L2CAP %s" % hexs(body) if body else "L2CAP (empty)"
        cid = body[2] | (body[3] << 8)
        l2 = body[4:]
        if cid == 0x0004 and l2:  # ATT
            att = ATT_OPCODE.get(l2[0], "ATT_?0x%02X" % l2[0])
            if l2[0] in (0x1B, 0x1D, 0x12, 0x52) and len(l2) >= 3:  # notify/ind/write -> handle+value
                handle = l2[1] | (l2[2] << 8)
                return "ATT  %-16s handle=0x%04X  value=%s" % (att, handle, hexs(l2[3:]))
            return "ATT  %-16s %s" % (att, hexs(l2[1:]))
        return "L2CAP cid=0x%04X  %s" % (cid, hexs(l2))

    return "data llid=%d  %s" % (llid, hexs(body))


# ---------------------------------------------------------------------------
# SnifferAPI packet helpers
# ---------------------------------------------------------------------------
def pdu_of(pkt) -> list[int]:
    """Full link-layer PDU (header, length, payload) without CRC, or [] if unknown."""
    ble = getattr(pkt, "blePacket", None)
    if ble is None:
        return []
    body = list(getattr(ble, "payload", []) or [])
    length = getattr(ble, "length", None)
    if length is None:
        return body
    # Re-read the header byte from the raw frame: SnifferAPI only keeps parsed bits.
    plist = getattr(pkt, "packetList", None)
    hdr = None
    if plist is not None:
        try:
            from SnifferAPI.Packet import BLEPACKET_POS
            off = BLEPACKET_POS + 4 + (1 if getattr(ble, "coded", False) else 0)
            hdr = plist[off]
        except Exception:
            hdr = None
    if hdr is None:
        if getattr(ble, "type", None) is not None and hasattr(ble, "advType"):
            hdr = (ble.advType & 0x0F) | (getattr(ble, "txAddrType", 0) << 6)
        else:
            hdr = (getattr(ble, "llid", 0) & 3) | (getattr(ble, "sn", 0) << 2) \
                | (getattr(ble, "nesn", 0) << 3) | (getattr(ble, "md", 0) << 4)
    return [hdr, length] + body[:length]


def classify(pdu: list[int], ch: Any, enc: bool) -> str:
    """Coarse packet category (ENC, CONNECT_IND, ADV, LL, ATT, SMP, ...)."""
    if enc:
        return "ENC"
    if not pdu:
        return "EMPTY"
    if ch in ADV_CHANNELS:
        t = pdu[0] & 0x0F
        return {0x5: "CONNECT_IND", 0x1: "ADV_DIRECT"}.get(t, "ADV")
    llid = pdu[0] & 0x03
    if llid == 0b11:
        return "LL"
    if llid in (0b01, 0b10) and len(pdu) >= 7:
        cid = pdu[4] | (pdu[5] << 8)
        if cid == 0x0004:
            return "ATT"
        if cid == 0x0006:
            return "SMP"
        return "L2CAP"
    return "DATA" if len(pdu) > 2 else "EMPTY"


def decode_pdu(pdu: list[int], ch: Any, enc: bool, raw: bool = False) -> str:
    if enc:
        return "[ENCRYPTED]  " + hexs(pdu[2:])
    if raw or not pdu:
        return hexs(pdu) if pdu else "(no payload)"
    if ch in ADV_CHANNELS:
        return decode_adv(pdu)
    return decode_data(pdu)


def _direction(pkt) -> str:
    d = getattr(pkt, "direction", None)
    if d is None:
        ble = getattr(pkt, "blePacket", None)
        d = getattr(ble, "direction", None) if ble is not None else None
    # SnifferAPI flag bit 1: set for master->slave (central -> peripheral).
    return {0: "S2M", 1: "M2S"}.get(int(d), "") if d is not None else ""


def packet_event(pkt, raw: bool = False) -> dict:
    """SnifferAPI packet -> dict; format_packet() renders it for ble_probe.py."""
    ch = getattr(pkt, "channel", None)
    rssi = getattr(pkt, "RSSI", None)
    enc = bool(getattr(pkt, "encrypted", False))
    pdu = pdu_of(pkt)
    ev: dict[str, Any] = {
        "ev": "pkt",
        "ch": ch,
        "rssi": rssi,
        "dir": _direction(pkt) if ch not in ADV_CHANNELS else "",
        "kind": classify(pdu, ch, enc),
        "text": decode_pdu(pdu, ch, enc, raw),
        "enc": enc,
        "crc": bool(getattr(pkt, "crcOK", True)),
        "ts": getattr(pkt, "timestamp", None),
    }
    ble = getattr(pkt, "blePacket", None)
    adv = getattr(ble, "advAddress", None) if ble is not None else None
    if adv:
        ev["addr"] = fmt_dev_addr(adv)
        ev["random"] = bool(adv[6]) if len(adv) > 6 else None
        name = getattr(ble, "name", "")
        if name and name != '""':
            ev["name"] = name.strip('"')
    return ev


def format_packet(pkt, t0: float, raw: bool = False) -> str:
    """Terminal line, same layout as the original ble_probe.print_packet."""
    ev = packet_event(pkt, raw)
    rssi = ev["rssi"]
    rssi_s = "%4ddBm" % rssi if isinstance(rssi, int) else "   ?  "
    dir_s = {"M2S": "M->S", "S2M": "S->M"}.get(ev["dir"], "    ")
    ch = ev["ch"] if ev["ch"] is not None else "?"
    return "[%9.3fs] CH%-2s %s %s  %s" % (time.time() - t0, ch, rssi_s, dir_s, ev["text"])
