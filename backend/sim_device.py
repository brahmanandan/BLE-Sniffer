"""
sim_device.py — software stand-in for the xiao-test-tool firmware.

`python backend/bridge.py --simulate` uses this instead of a serial port so the
web app and test runner can be exercised without hardware. It speaks the same
event schema as the firmware and adds a few sim-only commands:

    {"cmd":"sim_key","usage":"VOLUME_UP"}      press + release one key
    {"cmd":"sim_keys","usages":[...],"gap":0.3} press a sequence
    {"cmd":"sim_voice","sec":2}                 stream a voice burst (as if the mic button was pressed)
    {"cmd":"sim_ir","proto":"NEC","addr":"0x04","code":"0x08"}   inject a received IR frame
"""
from __future__ import annotations

import asyncio
import math
import random
import struct
import time
from typing import Any

from backend import adpcm, hid_usage

REMOTE_ADDR = "60:74:B1:80:AF:1E"

# A typical TV-remote report map: keyboard (ID 1), consumer control (ID 2), vendor (ID 0xE9).
SIM_REPORT_MAP = ("05010906a1018501050719e029e71500250175019508810295017508810195057501050819012905910295017503"
                  "910195067508150025f10507190029f18100c0050c0901a1018502751095021501268c0219012a8c028100c006"
                  "00ff0901a10185e9150026ff0019002aff00750895148100c0")


def _txt(s: str) -> str:
    return s.encode().hex()


# Values the simulated remote returns for gatt_read (clearly marked as simulated).
SIM_VALUES = {
    "0x0003": _txt("Sim Remote"),            # Device Name
    "0x0005": "8001",                         # Appearance: Generic Remote Control
    "0x0007": "0c00180000009001",             # PPCP: 15-30 ms, latency 0, 4 s
    "0x0011": _txt("Simulated"),             # Manufacturer
    "0x0012": "02e00000010001",               # PnP ID
    "0x0013": _txt("SIM-RCU-1"),             # Model
    "0x0014": _txt("SIM0000001"),            # Serial
    "0x0015": _txt("1.0.0"),                 # Firmware revision
    "0x0016": _txt("rev A"),                 # Hardware revision
    "0x0022": "4d",                           # Battery 77 %
    "0x0029": "11010003",                     # HID information
    "0x002B": "01",                           # Protocol mode: report
}

# usage name -> (page, id) for consumer-page keys, else keyboard page
_REVERSE: dict[str, tuple[int, int]] = {}
for page in (hid_usage.PAGE_KEYBOARD, hid_usage.PAGE_CONSUMER):
    for uid, nm in hid_usage.TABLES[page].items():
        _REVERSE.setdefault(nm, (page, uid))
for nm in ("UP", "DOWN", "LEFT", "RIGHT", "OK", "BACK", "HOME", "MENU", "POWER", "MUTE"):
    _REVERSE[nm] = (hid_usage.PAGE_CONSUMER, {v: k for k, v in hid_usage.CONSUMER.items()}[nm])


def _nec_timings(addr: int, code: int) -> list[int]:
    v = (addr & 0xFF) | ((~addr & 0xFF) << 8) if addr <= 0xFF else addr & 0xFFFF
    v |= (code << 16) | ((~code & 0xFF) << 24)
    t = [9000, 4500]
    for b in range(32):
        t += [560, 1690 if (v >> b) & 1 else 560]
    return t + [560]


def _nec_decode(t: list[int]) -> tuple[int, int] | None:
    near = lambda v, w, p: abs(v - w) <= w * p
    if len(t) < 67 or not near(t[0], 9000, .25) or not near(t[1], 4500, .25):
        return None
    v = 0
    for b in range(32):
        if near(t[3 + 2 * b], 1690, .35):
            v |= 1 << b
    a0, a1, c0, c1 = v & 0xFF, v >> 8 & 0xFF, v >> 16 & 0xFF, v >> 24 & 0xFF
    if c0 ^ c1 != 0xFF:
        return None
    return (a0 if a0 ^ a1 == 0xFF else a0 | a1 << 8), c0


def _ima_encode(samples: list[int], pred: int = 0, idx: int = 0) -> tuple[bytes, int, int]:
    out, hi = bytearray(), None
    for s in samples:
        step = adpcm.STEP[idx]
        diff = s - pred
        nib = 8 if diff < 0 else 0
        diff = abs(diff)
        delta = step >> 3
        if diff >= step: nib |= 4; diff -= step; delta += step
        if diff >= step >> 1: nib |= 2; diff -= step >> 1; delta += step >> 1
        if diff >= step >> 2: nib |= 1; delta += step >> 2
        pred = max(-32768, min(32767, pred - delta if nib & 8 else pred + delta))
        idx = max(0, min(88, idx + adpcm.INDEX[nib]))
        if hi is None:
            hi = nib
        else:
            out.append(hi << 4 | nib)
            hi = None
    return bytes(out), pred, idx


class SimLink:
    """Drop-in for tool_proto.ToolLink."""

    port = "sim"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.conn = False
        self.bonded = False
        self.secured = False
        self.ir_rx = True
        self.t0 = time.monotonic()
        self.tasks: set[asyncio.Task] = set()
        self.last_rx = time.monotonic()
        self.mv = 3980
        self.lock: str | None = None       # session lock (identity address)
        self.live: bool | None = None

    # -- plumbing -------------------------------------------------------------
    def _ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def emit(self, ev: dict[str, Any]) -> None:
        ev["host_ts"] = time.time() * 1000.0
        self.last_rx = time.monotonic()
        self.queue.put_nowait(ev)

    def log(self, msg: str, level: str = "info") -> None:
        self.emit({"ev": "log", "level": level, "msg": msg})

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def open(self, ser=None) -> None:
        self._spawn(self._ticker())
        self._hello()
        self._battery()

    def close(self) -> None:
        for t in list(self.tasks):
            t.cancel()
        self.queue.put_nowait(None)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(2)
            if self.conn:
                self._state("ready" if self.secured else "discovered")
            if int(time.monotonic()) % 30 < 2:
                self._battery()

    # -- events ---------------------------------------------------------------
    def _hello(self) -> None:
        self.emit({"ev": "hello", "fw": "xiao-test-tool", "ver": "0.1-sim",
                   "roles": ["ble_central", "ir", "battery"], "addr": "C0:FF:EE:00:00:01",
                   "bonds": 1 if self.bonded else 0, "mode": "both"})

    def _battery(self) -> None:
        self.mv += random.randint(-6, 4)
        self.emit({"ev": "battery", "which": "jig", "mv": self.mv, "pct": min(100, max(0, (self.mv - 3300) // 9)),
                   "low": self.mv < 3400, "cut": self.mv < 3300})

    def _state(self, stage: str) -> None:
        ev = {"ev": "state", "conn": self.conn, "stage": stage, "addr": REMOTE_ADDR, "auto_reconnect": self.secured,
              "services": 5 if self.conn else 0}
        if self.conn:
            ev.update(bonded=self.bonded, secured=self.secured, mtu=247, phy="2M",
                      rssi=random.randint(-52, -40), interval_ms=7.5)
            if self.live is not None:
                ev["live"] = self.live
        if self.lock:
            ev["lock"] = self.lock
        ev["bonds"] = 1 if self.bonded else 0
        self.emit(ev)

    def _bonds(self) -> None:
        lst = [{"addr": REMOTE_ADDR, "atype": "rpa", "rpa": True, "locked": self.lock == REMOTE_ADDR,
                "connected": self.conn}] if self.bonded else []
        self.emit({"ev": "bonds", "list": lst})

    def _gatt(self) -> None:
        self.emit({"ev": "gatt", "services": [
            {"uuid": "1800", "start": "0x0001", "end": "0x0007", "chars": [
                {"uuid": "2A00", "handle": "0x0003", "props": "R"},
                {"uuid": "2A01", "handle": "0x0005", "props": "R"},
                {"uuid": "2A04", "handle": "0x0007", "props": "R"}]},
            {"uuid": "180A", "start": "0x0010", "end": "0x0016", "chars": [
                {"uuid": "2A29", "handle": "0x0011", "props": "R"},
                {"uuid": "2A50", "handle": "0x0012", "props": "R"},
                {"uuid": "2A24", "handle": "0x0013", "props": "R"},
                {"uuid": "2A25", "handle": "0x0014", "props": "R"},
                {"uuid": "2A26", "handle": "0x0015", "props": "R"},
                {"uuid": "2A27", "handle": "0x0016", "props": "R"}]},
            {"uuid": "180F", "start": "0x0020", "end": "0x0024", "chars": [
                {"uuid": "2A19", "handle": "0x0022", "props": "RN", "role": "battery"}]},
            {"uuid": "1812", "start": "0x0028", "end": "0x0040", "chars": [
                {"uuid": "2A4A", "handle": "0x0029", "props": "R"},
                {"uuid": "2A4B", "handle": "0x002A", "props": "R", "role": "hid_map"},
                {"uuid": "2A4E", "handle": "0x002B", "props": "Rw"},
                {"uuid": "2A4D", "handle": "0x002D", "props": "RN", "role": "hid_report", "report_id": 1, "report_type": "input"},
                {"uuid": "2A4D", "handle": "0x0031", "props": "RN", "role": "hid_report", "report_id": 2, "report_type": "input"}]},
            {"uuid": "AB5E0001-5A21-4F05-BC7D-AF01F617B664", "start": "0x0050", "end": "0x005A", "chars": [
                {"uuid": "AB5E0002-5A21-4F05-BC7D-AF01F617B664", "handle": "0x0052", "props": "W", "role": "voice_tx"},
                {"uuid": "AB5E0003-5A21-4F05-BC7D-AF01F617B664", "handle": "0x0054", "props": "N", "role": "voice_audio"},
                {"uuid": "AB5E0004-5A21-4F05-BC7D-AF01F617B664", "handle": "0x0057", "props": "N", "role": "voice_ctl"}]},
        ]})

    async def _press(self, usage: str, hold: float = 0.12) -> None:
        page, uid = _REVERSE.get(usage, (hid_usage.PAGE_CONSUMER, 0))
        if not uid and usage.lower().startswith("0x"):
            uid = int(usage, 16)
        if page == hid_usage.PAGE_CONSUMER:
            handle, rid, rep = "0x0031", 2, "%02x %02x" % (uid & 0xFF, uid >> 8)
        else:
            handle, rid, rep = "0x002D", 1, "00 00 %02x 00 00 00 00 00" % uid
        zero = " ".join("00" for _ in rep.split())
        base = dict(ev="key", handle=handle, usage=hid_usage.usage_name(page, uid), usage_id="0x%X" % uid,
                    page=hid_usage.PAGE_NAMES[page], rid=rid)
        # Firmware-side timestamp jitter: the radio delivers on a connection event.
        await asyncio.sleep(random.uniform(0, 0.0075))
        self.emit(dict(base, t=self._ms(), report=rep, down=True))
        await asyncio.sleep(hold)
        self.emit(dict(base, t=self._ms(), report=zero, down=False))

    async def _voice(self, sec: float) -> None:
        self.emit({"ev": "voice_ctl", "op": "search", "bytes": "08", "t": self._ms()})
        await asyncio.sleep(0.05)
        self.emit({"ev": "voice_ctl", "op": "start", "bytes": "04", "t": self._ms()})
        self.emit({"ev": "voice_state", "active": True, "t": self._ms()})
        # 8 kHz, 256 samples per 134-byte ATVV frame -> 31.25 frames/s, each split into 20-byte notifications
        nframes = int(sec * 8000 / 256)
        phase = 0.0
        seq = 0
        pred = idx = 0
        for f in range(nframes):
            samples = []
            for _ in range(256):
                f0 = 300 + 200 * math.sin(f / 6)
                phase += 2 * math.pi * f0 / 8000
                samples.append(int(9000 * math.sin(phase) * (0.5 + 0.5 * math.sin(f / 3))))
            # ATVV v0.4 header carries the codec state at the start of the frame
            hdr = struct.pack(">HBhB", f & 0xFFFF, 0, pred, idx)
            data, pred, idx = _ima_encode(samples, pred, idx)
            frame = hdr + data
            for off in range(0, len(frame), 20):
                self.emit({"ev": "voice", "seq": seq, "codec": "adpcm", "handle": "0x0054",
                           "bytes": frame[off:off + 20].hex(), "t": self._ms()})
                seq += 1
            await asyncio.sleep(256 / 8000)
        self.emit({"ev": "voice_ctl", "op": "stop", "bytes": "00", "t": self._ms()})
        self.emit({"ev": "voice_state", "active": False, "t": self._ms()})

    async def _scan(self, sec: float) -> None:
        self.log("scanning %ds" % sec)
        # (addr, atype, name, hid, directed, flags): flags 0x06 = LE General Discoverable (pairing mode),
        # 0x04 = not discoverable (a remote reconnecting to its own host -> app shows NOT PAIRING).
        devs = [
            (REMOTE_ADDR, "rpa", "RCU", True, False, 0x06),
            ("30:4D:1F:13:82:8B", "public", "AR", True, False, 0x04),
            ("1E:AF:80:B1:74:60", "nrpa", "", False, True, 0x06),
            ("7B:24:8A:C3:58:06", "random", "", False, False, 0x06),
            ("D4:3A:2C:11:22:33", "public", "Living Room TV", False, False, 0x06),
        ]
        end = time.monotonic() + sec
        while time.monotonic() < end:
            for addr, at, name, hid, directed, flags in devs:
                adv = "0201%02x" % flags + ("03031218" if hid else "")
                if name:
                    adv += "%02x09" % (len(name) + 1) + name.encode().hex()
                self.emit({"ev": "scan", "addr": addr, "atype": at, "rssi": random.randint(-80, -38) if not hid else random.randint(-50, -40),
                           "name": name, "directed": directed, "connectable": True, "hid": hid, "adv": adv,
                           "rsp": "", "bonded": self.bonded and addr == REMOTE_ADDR})
            await asyncio.sleep(1.0)
        self.emit({"ev": "scan_done", "found": len(devs)})

    async def _connect(self, addr: str) -> None:
        self._state("connecting")
        await asyncio.sleep(0.4)
        self.conn = True
        self.live = None
        self.log("connected")
        self.emit({"ev": "link", "what": "conn_params", "interval_ms": 7.5, "latency": 0, "timeout_ms": 4000})
        self._state("connected")
        await asyncio.sleep(0.05)
        self.emit({"ev": "link", "what": "mtu", "mtu": 247, "remote_mtu": 247})
        self.emit({"ev": "link", "what": "phy", "tx": "2M", "rx": "2M", "status": 0})
        self.emit({"ev": "link", "what": "data_length", "tx_octets": 251, "rx_octets": 251})
        await asyncio.sleep(0.1)
        self._state("discovering")
        await asyncio.sleep(0.5)
        self._gatt()
        self._state("discovered")
        if self.bonded:
            await self._secure()

    async def _secure(self) -> None:
        await asyncio.sleep(0.6)
        self.bonded = self.secured = True
        self.log("pairing complete, bonded")
        self.log("link encrypted")
        self._state("reading")
        self.emit({"ev": "hid", "map_len": len(SIM_REPORT_MAP) // 2, "fields": 4, "uses_ids": True, "map": SIM_REPORT_MAP,
                   "reports": [{"handle": "0x002D", "id": 1, "type": "input"}, {"handle": "0x0031", "id": 2, "type": "input"}]})
        self.emit({"ev": "battery", "which": "remote", "pct": 77, "via": "read"})
        self.log("subscribed all: 4 ok, 0 failed")
        self._state("ready")
        self._bonds()
        await asyncio.sleep(0.3)               # the ATV voice handshake answers -> live
        self.emit({"ev": "voice_ctl", "op": "caps", "bytes": "0b0100020300140000", "t": self._ms()})
        self.live = True
        self._state("ready")

    async def _ir(self, timings: list[int], decoded: tuple[str, int, int] | None) -> None:
        await asyncio.sleep(sum(timings) / 1e6 + 0.005)
        self.emit({"ev": "ir_tx_done", "ok": True, "t": self._ms()})
        if not self.ir_rx:
            return
        await asyncio.sleep(0.012)  # end-of-frame gap
        rx = [max(1, t + random.randint(-40, 40)) for t in timings]
        ev = {"ev": "ir", "repeat": False, "raw": rx, "t": self._ms()}
        dec = _nec_decode(rx)
        if dec:
            a, c = dec
            ev.update(proto="NEC", addr=("0x%04X" if a > 0xFF else "0x%02X") % a, code="0x%02X" % c)
        elif decoded:
            ev.update(proto=decoded[0], addr="0x%02X" % decoded[1], code="0x%02X" % decoded[2])
        else:
            ev.update(proto="UNKNOWN")
        self.emit(ev)

    # -- commands -------------------------------------------------------------
    def send(self, cmd: dict[str, Any]) -> None:
        c = cmd.get("cmd")
        num = lambda v: int(v, 0) if isinstance(v, str) else int(v)
        if c == "ping":
            self._hello()
        elif c == "scan":
            self._spawn(self._scan(cmd.get("sec", 6)))
        elif c == "connect":
            self.lock = str(cmd.get("addr", "")).upper() or None
            self._spawn(self._connect(cmd.get("addr", "")))
        elif c == "pair":
            self.lock = str(cmd.get("addr", "")).upper() or None
            self.log("pairing %s: connect → pair → subscribe" % cmd.get("addr", ""))
            async def pair() -> None:
                if not self.conn:
                    await self._connect(cmd.get("addr", ""))
                self._state("pairing")
                await self._secure()
            self._spawn(pair())
        elif c in ("lock", "unlock"):
            self.lock = (str(cmd.get("addr", "")).upper() or None) if c == "lock" else None
            self.log("session locked to %s" % self.lock if self.lock else "session unlocked: the jig follows any bonded remote")
            self._state("ready" if self.secured else "idle")
            self._bonds()
        elif c == "bonds":
            self._bonds()
        elif c == "bond_delete":
            if str(cmd.get("addr", "")).upper() == REMOTE_ADDR and self.bonded:
                self.bonded = False
                if self.lock == REMOTE_ADDR:
                    self.lock = None
                self.log("bond with %s deleted" % REMOTE_ADDR)
            else:
                self.log("no bond with %s" % cmd.get("addr"), "warn")
            self._bonds()
        elif c == "bond":
            if not self.conn:
                self.log("bond: not connected", "warn")
            else:
                self._state("pairing")
                self._spawn(self._secure())
        elif c == "disconnect":
            self.conn = self.secured = False
            self.log("disconnected (reason 0x16)")
            self._state("idle")
        elif c == "clear_bonds":
            self.bonded = False
            self.lock = None
            self.log("bonds cleared")
            self._bonds()
        elif c in ("subscribe", "scan_stop"):
            self.log("%s ok" % c)
        elif c == "battery":
            self._battery()
            if self.conn:
                self.emit({"ev": "battery", "which": "remote", "pct": 77, "via": "read"})
        elif c == "state":
            self._state("ready" if self.secured else "idle")
        elif c == "info":
            if self.conn:
                self._gatt()
            self._bonds()
            self._state("ready" if self.secured else "idle")
        elif c == "ir_rx":
            self.ir_rx = bool(cmd.get("on", True))
            self.log("ir rx %s" % ("on" if self.ir_rx else "off"))
        elif c == "ir_tx":
            a, k = num(cmd["addr"]), num(cmd["code"])
            if str(cmd.get("proto", "NEC")).upper() == "NEC":
                self._spawn(self._ir(_nec_timings(a, k), None))
            else:
                self._spawn(self._ir([2400, 600] + [600, 600] * 12, (cmd["proto"].upper(), a, k)))
        elif c == "ir_tx_raw":
            self._spawn(self._ir(list(cmd["timings"]), None))
        elif c == "mic" or c == "sim_voice":
            if cmd.get("on", True):
                self._spawn(self._voice(float(cmd.get("sec", 2.5))))
        elif c == "sim_key":
            self._spawn(self._press(cmd.get("usage", "OK")))
        elif c == "sim_keys":
            async def seq():
                for u in cmd.get("usages", []):
                    await self._press(u)
                    await asyncio.sleep(float(cmd.get("gap", 0.25)))
            self._spawn(seq())
        elif c == "sim_ir":
            a, k = num(cmd.get("addr", 4)), num(cmd.get("code", 8))
            self._spawn(self._ir(_nec_timings(a, k), None))
        elif c == "mode":
            self._hello()
        elif c in ("gatt_write", "gatt_read"):
            self.emit({"ev": "gatt_read", "handle": cmd.get("handle"), "ok": True,
                       "bytes": SIM_VALUES.get(str(cmd.get("handle", "")).upper().replace("0X", "0x"), "")} if c == "gatt_read"
                      else {"ev": "log", "level": "info", "msg": "write ok"})
        elif c == "dfu":
            self.log("dfu not available in simulator", "warn")
        else:
            self.log("unknown cmd %s" % c, "warn")
