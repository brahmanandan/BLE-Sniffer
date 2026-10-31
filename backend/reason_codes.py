"""
reason_codes.py — human-readable text for the numeric codes in firmware messages.

The firmware reports codes as numbers ("disconnected (reason 0x08)"). The bridge passes every firmware
`log` event through annotate(), which appends the meaning right after each code —

    disconnected (reason 0x08 = Connection Timeout: link lost — remote out of range, powered off or reset)

— and adds a structured `codes` list to the event. Four code families, told apart by the wording:

    "reason 0x.."          HCI error / disconnect reason      (Bluetooth Core Vol 1 Part F)
    "pairing failed: 0x.." SMP pairing failure                 (BLE_GAP_SEC_STATUS_*)
    "status 0x...."        GATT/ATT status of a GATT operation (BLE_GATT_STATUS_*)
    "err 0x…", ": 0x…"     nRF SoftDevice API error            (NRF_ERROR_*, BLE_ERROR_*)

    python -m backend.reason_codes            # print every table as Markdown (used for the manual)
    python -m backend.reason_codes hci 0x3e   # look one up
"""
from __future__ import annotations

import re
import sys
from typing import Any

# --- HCI error codes (also the disconnect reasons) -------------------------------------
# (name, hint for this jig — empty if the name says it all)
HCI: dict[int, tuple[str, str]] = {
    0x00: ("Success", ""),
    0x01: ("Unknown HCI Command", ""),
    0x02: ("Unknown Connection Identifier", "the link was already gone"),
    0x03: ("Hardware Failure", ""),
    0x04: ("Page Timeout", ""),
    0x05: ("Authentication Failure", "pairing / encryption was rejected"),
    0x06: ("PIN or Key Missing", "the remote no longer has the bond — pair it again (pairing mode)"),
    0x07: ("Memory Capacity Exceeded", ""),
    0x08: ("Connection Timeout", "link lost — remote out of range, powered off, reset or batteries low"),
    0x09: ("Connection Limit Exceeded", ""),
    0x0A: ("Synchronous Connection Limit Exceeded", ""),
    0x0B: ("Connection Already Exists", ""),
    0x0C: ("Command Disallowed", ""),
    0x0D: ("Connection Rejected: Limited Resources", ""),
    0x0E: ("Connection Rejected: Security Reasons", ""),
    0x0F: ("Connection Rejected: Unacceptable Address", ""),
    0x10: ("Connection Accept Timeout Exceeded", ""),
    0x11: ("Unsupported Feature or Parameter Value", ""),
    0x12: ("Invalid HCI Command Parameters", ""),
    0x13: ("Remote User Terminated Connection", "the remote closed the link — idle power-save, pairing mode, or it rejected the jig"),
    0x14: ("Remote Terminated: Low Resources", ""),
    0x15: ("Remote Terminated: Power Off", "the remote is switching off"),
    0x16: ("Connection Terminated by Local Host", "the jig closed the link (Disconnect, or a silent link was dropped)"),
    0x17: ("Repeated Attempts", ""),
    0x18: ("Pairing Not Allowed", "the remote refuses to pair — put it in pairing mode"),
    0x19: ("Unknown LMP PDU", ""),
    0x1A: ("Unsupported Remote Feature", ""),
    0x1B: ("SCO Offset Rejected", ""),
    0x1C: ("SCO Interval Rejected", ""),
    0x1D: ("SCO Air Mode Rejected", ""),
    0x1E: ("Invalid LL Parameters", ""),
    0x1F: ("Unspecified Error", ""),
    0x20: ("Unsupported LL Parameter Value", ""),
    0x21: ("Role Change Not Allowed", ""),
    0x22: ("LL Response Timeout", "the remote stopped answering a link-layer procedure"),
    0x23: ("LL Procedure Collision", ""),
    0x24: ("LMP PDU Not Allowed", ""),
    0x25: ("Encryption Mode Not Acceptable", ""),
    0x26: ("Link Key Cannot Be Changed", ""),
    0x27: ("Requested QoS Not Supported", ""),
    0x28: ("Instant Passed", "a connection update came too late — usually radio interference"),
    0x29: ("Pairing With Unit Key Not Supported", ""),
    0x2A: ("Different Transaction Collision", ""),
    0x2C: ("QoS Unacceptable Parameter", ""),
    0x2D: ("QoS Rejected", ""),
    0x2E: ("Channel Classification Not Supported", ""),
    0x2F: ("Insufficient Security", ""),
    0x30: ("Parameter Out of Mandatory Range", ""),
    0x32: ("Role Switch Pending", ""),
    0x34: ("Reserved Slot Violation", ""),
    0x35: ("Role Switch Failed", ""),
    0x36: ("Extended Inquiry Response Too Large", ""),
    0x37: ("Secure Simple Pairing Not Supported by Host", ""),
    0x38: ("Host Busy - Pairing", ""),
    0x39: ("Connection Rejected: No Suitable Channel", ""),
    0x3A: ("Controller Busy", ""),
    0x3B: ("Unacceptable Connection Parameters", ""),
    0x3C: ("Advertising Timeout", ""),
    0x3D: ("Connection Terminated: MIC Failure", "encryption keys don't match — the bond is stale; pair again"),
    0x3E: ("Connection Failed to Be Established", "the remote never answered — not in pairing mode, or it only accepts its own host (NOT PAIRING)"),
    0x3F: ("MAC Connection Failed", ""),
    0x40: ("Coarse Clock Adjustment Rejected", ""),
    0x41: ("Type0 Submap Not Defined", ""),
    0x42: ("Unknown Advertising Identifier", ""),
    0x43: ("Limit Reached", ""),
    0x44: ("Operation Cancelled by Host", ""),
    0x45: ("Packet Too Long", ""),
}

# --- SMP pairing failures (SoftDevice BLE_GAP_SEC_STATUS_*) ------------------------------
SMP: dict[int, tuple[str, str]] = {
    0x00: ("Success", ""),
    0x01: ("Timeout", "pairing took more than 30 s — the remote stopped responding"),
    0x02: ("Invalid PDU", ""),
    0x81: ("Passkey Entry Failed", ""),
    0x82: ("OOB Not Available", ""),
    0x83: ("Authentication Requirements Not Met", "the remote wants a stronger pairing method than Just Works"),
    0x84: ("Confirm Value Failed", ""),
    0x85: ("Pairing Not Supported", "the remote refuses to pair now — put it in pairing mode"),
    0x86: ("Encryption Key Size Too Short", ""),
    0x87: ("SMP Command Not Supported", ""),
    0x88: ("Unspecified Reason", "often: the remote is not in pairing mode"),
    0x89: ("Repeated Attempts", "too many tries — wait a few seconds"),
    0x8A: ("Invalid Parameters", ""),
    0x8B: ("DHKey Check Failed", ""),
    0x8C: ("Numeric Comparison Failed", ""),
    0x8D: ("BR/EDR Pairing in Progress", ""),
    0x8E: ("Cross-transport Key Derivation Not Allowed", ""),
}

# --- GATT / ATT status of a GATT operation (SoftDevice BLE_GATT_STATUS_*) -----------------
GATT: dict[int, tuple[str, str]] = {
    0x0000: ("Success", ""),
    0x0001: ("Unknown or not applicable", ""),
    0x0100: ("ATT: Invalid Error Code", ""),
    0x0101: ("ATT: Invalid Handle", ""),
    0x0102: ("ATT: Read Not Permitted", ""),
    0x0103: ("ATT: Write Not Permitted", ""),
    0x0104: ("ATT: Invalid PDU", ""),
    0x0105: ("ATT: Insufficient Authentication", "needs a bonded link — press Bond first"),
    0x0106: ("ATT: Request Not Supported", ""),
    0x0107: ("ATT: Invalid Offset", ""),
    0x0108: ("ATT: Insufficient Authorization", ""),
    0x0109: ("ATT: Prepare Queue Full", ""),
    0x010A: ("ATT: Attribute Not Found", ""),
    0x010B: ("ATT: Attribute Not Long", ""),
    0x010C: ("ATT: Insufficient Encryption Key Size", ""),
    0x010D: ("ATT: Invalid Attribute Value Length", ""),
    0x010E: ("ATT: Unlikely Error", ""),
    0x010F: ("ATT: Insufficient Encryption", "the link is not encrypted yet — press Bond first"),
    0x0110: ("ATT: Unsupported Group Type", ""),
    0x0111: ("ATT: Insufficient Resources", ""),
    0x0112: ("ATT: Database Out of Sync", ""),
    0x0113: ("ATT: Value Not Allowed", ""),
    0x01FC: ("ATT: Write Request Rejected", ""),
    0x01FD: ("ATT: CCCD Improperly Configured", ""),
    0x01FE: ("ATT: Procedure Already in Progress", ""),
    0x01FF: ("ATT: Out of Range", ""),
    0xFFFF: ("No response", "the link dropped or the remote didn't answer before the operation finished (jig)"),
}

# --- nRF SoftDevice API errors (NRF_ERROR_*, BLE_ERROR_*) ---------------------------------
NRF: dict[int, tuple[str, str]] = {
    0x0000: ("NRF_SUCCESS", ""),
    0x0001: ("NRF_ERROR_SVC_HANDLER_MISSING", ""),
    0x0002: ("NRF_ERROR_SOFTDEVICE_NOT_ENABLED", ""),
    0x0003: ("NRF_ERROR_INTERNAL", ""),
    0x0004: ("NRF_ERROR_NO_MEM", ""),
    0x0005: ("NRF_ERROR_NOT_FOUND", ""),
    0x0006: ("NRF_ERROR_NOT_SUPPORTED", ""),
    0x0007: ("NRF_ERROR_INVALID_PARAM", ""),
    0x0008: ("NRF_ERROR_INVALID_STATE", "not possible right now (e.g. already connecting, or not connected)"),
    0x0009: ("NRF_ERROR_INVALID_LENGTH", ""),
    0x000A: ("NRF_ERROR_INVALID_FLAGS", ""),
    0x000B: ("NRF_ERROR_INVALID_DATA", ""),
    0x000C: ("NRF_ERROR_DATA_SIZE", ""),
    0x000D: ("NRF_ERROR_TIMEOUT", ""),
    0x000E: ("NRF_ERROR_NULL", ""),
    0x000F: ("NRF_ERROR_FORBIDDEN", ""),
    0x0010: ("NRF_ERROR_INVALID_ADDR", ""),
    0x0011: ("NRF_ERROR_BUSY", "the radio stack is busy with another operation — retry"),
    0x0012: ("NRF_ERROR_CONN_COUNT", ""),
    0x0013: ("NRF_ERROR_RESOURCES", ""),
    0x3001: ("BLE_ERROR_NOT_ENABLED", ""),
    0x3002: ("BLE_ERROR_INVALID_CONN_HANDLE", "the link was already gone when the jig tried to use it"),
    0x3003: ("BLE_ERROR_INVALID_ATTR_HANDLE", ""),
    0x3004: ("BLE_ERROR_INVALID_ADV_HANDLE", ""),
    0x3005: ("BLE_ERROR_INVALID_ROLE", ""),
    0x3006: ("BLE_ERROR_BLOCKED_BY_OTHER_LINKS", ""),
    0x3100: ("BLE_ERROR_L2CAP_CID_IN_USE", ""),
    0x3200: ("BLE_ERROR_GAP_UUID_LIST_MISMATCH", ""),
    0x3201: ("BLE_ERROR_GAP_DISCOVERABLE_WITH_WHITELIST", ""),
    0x3202: ("BLE_ERROR_GAP_INVALID_BLE_ADDR", ""),
    0x3203: ("BLE_ERROR_GAP_WHITELIST_IN_USE", ""),
    0x3204: ("BLE_ERROR_GAP_DEVICE_IDENTITIES_IN_USE", ""),
    0x3205: ("BLE_ERROR_GAP_DEVICE_IDENTITIES_DUPLICATE", ""),
    0x3300: ("BLE_ERROR_GATTC_PROC_NOT_PERMITTED", "another GATT operation is still running"),
    0x3400: ("BLE_ERROR_GATTS_INVALID_ATTR_TYPE", ""),
    0x3401: ("BLE_ERROR_GATTS_SYS_ATTR_MISSING", ""),
}

TABLES: dict[str, tuple[str, dict[int, tuple[str, str]]]] = {
    "hci": ("HCI error / disconnect reason", HCI),
    "smp": ("SMP pairing failure", SMP),
    "gatt": ("GATT/ATT status", GATT),
    "nrf": ("nRF SoftDevice error", NRF),
}

# wording in the firmware's messages -> which table the following code belongs to
_PATTERNS = [
    ("hci", re.compile(r"\breason (0x[0-9A-Fa-f]+)")),
    ("smp", re.compile(r"\bpairing failed: (0x[0-9A-Fa-f]+)")),
    ("gatt", re.compile(r"\bstatus (0x[0-9A-Fa-f]+)")),
    ("nrf", re.compile(r"\berr (0x[0-9A-Fa-f]+)|(?<!pairing )\bfailed: (0x[0-9A-Fa-f]+)")),
]


def lookup(kind: str, code: int) -> dict[str, Any]:
    title, table = TABLES[kind]
    name, hint = table.get(code, ("", ""))
    if not name and kind == "gatt" and 0x0180 <= code <= 0x019F:
        name = "ATT: Application Error 0x%02X (defined by the remote's own service)" % (code & 0xFF)
    if not name and kind == "hci":
        name = "Reserved / unknown HCI code"
    return {"kind": kind, "family": title, "code": "0x%0*X" % (2 if kind in ("hci", "smp") else 4, code),
            "name": name or "unknown code", "hint": hint}


def describe(kind: str, code: int) -> str:
    d = lookup(kind, code)
    return d["name"] + (f": {d['hint']}" if d["hint"] else "")


def annotate(msg: str) -> tuple[str, list[dict[str, Any]]]:
    """Insert ' = <meaning>' after every recognised code in a firmware message."""
    found: list[dict[str, Any]] = []
    for kind, rx in _PATTERNS:
        def sub(m: re.Match) -> str:
            raw = next(g for g in m.groups() if g)
            code = int(raw, 16)
            if kind == "nrf" and code == 0:          # "err 0x0" just means it worked
                return m.group(0)
            found.append(dict(lookup(kind, code), raw=raw))
            return f"{m.group(0)} = {describe(kind, code)}"
        msg = rx.sub(sub, msg)
    return msg, found


def markdown() -> str:
    out = []
    for kind, (title, table) in TABLES.items():
        out.append(f"### {title} (`{kind}`)\n\n| Code | Meaning | What it usually means on the jig |\n|---|---|---|")
        width = 2 if kind in ("hci", "smp") else 4
        for code, (name, hint) in sorted(table.items()):
            out.append(f"| `0x{code:0{width}X}` | {name} | {hint} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        print(describe(sys.argv[1].lower(), int(sys.argv[2], 16)))
    else:
        print(markdown())
