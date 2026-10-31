"""
session_log.py — persistent session log written by the bridge in the background.

When enabled (web app: Connect ▸ Log ▸ "Save to file", or `bridge.py --session-log`), every event
the bridge passes on — from the firmware and from the bridge itself — and every command sent to the
jig is appended to reports/logs/session_<YYYYmmdd-HHMMSS>.log, one timestamped line each:

    2026-10-29 10:12:01.123  key         0x0028 OK down  report=41 00 00 00
    2026-10-29 10:12:03.410  > cmd       {"cmd":"bond"}

Logging lives in the bridge, so it keeps running with no browser open. To stay readable, the log
leaves out the 5 s keep-alive `hello`, `state` updates that only change RSSI, and individual voice
frames (one summary line per stream instead).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger("session_log")

# state fields worth a log line when they change (RSSI-only updates are skipped)
STATE_KEYS = ("conn", "stage", "addr", "bonded", "secured", "mtu", "phy", "live", "services",
              "interval_ms", "lock", "bonds")
# scan fields that make a new line worth writing (scans arrive ~1/s per device otherwise)
SCAN_KEYS = ("name", "directed", "target", "connectable", "hid", "bonded", "adv", "rsp")
DROP_FIELDS = ("ev", "src", "host_ts", "cached")


def _stamp(ms: float | None = None) -> str:
    t = (ms / 1000.0) if ms else time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + ".%03d" % int((t % 1) * 1000)


class SessionLog:
    def __init__(self, directory: Path):
        self.dir = directory
        self.fh: TextIO | None = None
        self.path: Path | None = None
        self.started: float | None = None
        self.lines = 0
        self._last_state: tuple | None = None
        self._hello_seen = False
        self._voice_frames = 0
        self._scan_seen: dict[str, tuple] = {}
        self._hid_data: dict[str, dict] = {}

    @property
    def active(self) -> bool:
        return self.fh is not None

    def status(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ev": "session_log", "on": self.active}
        if self.path:
            d.update(name=self.path.name, url="/files/logs/" + self.path.name, lines=self.lines,
                     started=self.started)
        return d

    # -- start / stop ----------------------------------------------------------------
    def start(self, header: dict[str, Any] | None = None) -> None:
        if self.fh:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / time.strftime("session_%Y%m%d-%H%M%S.log")
        self.fh = open(self.path, "a", encoding="utf-8", buffering=1)      # line-buffered: survives crashes
        self.started = time.time() * 1000.0
        self.lines = 0
        self._last_state, self._hello_seen, self._voice_frames = None, False, 0
        self._scan_seen = {}
        self._hid_data = {}
        self.fh.write(f"# Remote Jig session log — started {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        for k, v in (header or {}).items():
            self.fh.write(f"# {k}: {v}\n")
        self.fh.write("# columns: date time  event  details\n")
        log.info("session log -> %s", self.path)

    def stop(self) -> None:
        if not self.fh:
            return
        self.fh.write(f"# stopped {time.strftime('%Y-%m-%d %H:%M:%S %Z')} — {self.lines} lines\n")
        self.fh.close()
        self.fh = None
        log.info("session log closed: %s (%d lines)", self.path, self.lines)

    # -- writing ---------------------------------------------------------------------
    def _write(self, ts: str, kind: str, text: str) -> None:
        if not self.fh:
            return
        try:
            self.fh.write(f"{ts}  {kind:<11} {text}\n")
            self.lines += 1
        except OSError as e:          # disk full / removed: stop rather than spam errors
            log.error("session log write failed: %s — logging stopped", e)
            self.fh = None

    def note(self, text: str) -> None:
        """A free-text marker from the app (test run started / finished, DUT label …)."""
        self._write(_stamp(), "note", text)

    def command(self, msg: dict) -> None:
        """A command a web client sent (written as-is, minus client bookkeeping)."""
        if msg.get("cmd") in ("time", "bridge", "ping", "note"):
            return
        body = {k: v for k, v in msg.items() if k != "id"}
        if "report" in body:                   # the whole report object: the saved file has it
            body["report"] = "…"
        self._write(_stamp(), "> cmd", json.dumps(body, separators=(",", ":"), ensure_ascii=False))

    def event(self, ev: dict) -> None:
        if not self.fh:
            return
        kind = str(ev.get("ev", "?"))
        ts = _stamp(ev.get("host_ts"))
        if kind == "voice":
            self._voice_frames += 1
            return
        if kind == "hid_data":                 # vendor streams (e.g. motion at 50+/s): summarise
            h = str(ev.get("handle"))
            now = time.time()
            st = self._hid_data.get(h)
            if st is None:
                self._hid_data[h] = {"n": 0, "since": now}
                self._write(ts, "hid_data", f"new stream {h} (report id {ev.get('rid')}): {ev.get('bytes', '')}")
                return
            st["n"] += 1
            if now - st["since"] >= 10:
                self._write(ts, "hid_data", f"{h} (report id {ev.get('rid')}): {st['n']} reports in "
                            f"{now - st['since']:.0f} s ({st['n'] / (now - st['since']):.0f}/s), last {ev.get('bytes', '')}")
                st["n"], st["since"] = 0, now
            return
        if kind == "bridge":                   # board (re)attached: log its next hello again
            self._hello_seen = False
        if kind == "hello":
            if self._hello_seen:
                return
            self._hello_seen = True
        if kind == "scan":                     # first sighting and changes only, not every second
            sig = tuple(ev.get(k) for k in SCAN_KEYS)
            if self._scan_seen.get(ev.get("addr")) == sig:
                return
            first = ev.get("addr") not in self._scan_seen
            self._scan_seen[ev.get("addr")] = sig
            self._write(ts, "scan", ("new  " if first else "chg  ") + self._details(kind, ev))
            return
        if kind == "state":
            sig = tuple(ev.get(k) for k in STATE_KEYS)
            if sig == self._last_state:
                return
            self._last_state = sig
        if kind == "voice_state" and not ev.get("active") and self._voice_frames:
            self._write(ts, "voice", f"stream ended: {self._voice_frames} audio notifications")
            self._voice_frames = 0
        if kind in ("session_log", "time"):
            return
        self._write(ts, kind, self._details(kind, ev))

    @staticmethod
    def _details(kind: str, ev: dict) -> str:
        if kind == "key":
            return (f"{ev.get('handle', '')} {ev.get('usage', '?')} {'down' if ev.get('down') else 'up'}"
                    f"  report={ev.get('report', '')}  page={ev.get('page', '')}")
        if kind == "log":
            return f"[{ev.get('level', 'info')}] {ev.get('msg', '')}"
        if kind == "link":
            w = ev.get("what")
            if w == "conn_params":
                return f"connection interval {ev.get('interval_ms')} ms, slave latency {ev.get('latency')}, supervision timeout {ev.get('timeout_ms')} ms"
            if w == "phy":
                return f"PHY tx {ev.get('tx')} / rx {ev.get('rx')} (status {ev.get('status')})"
            if w == "data_length":
                return f"data length tx {ev.get('tx_octets')} / rx {ev.get('rx_octets')} octets"
            if w == "mtu":
                return f"MTU {ev.get('mtu')} (remote offered {ev.get('remote_mtu')})"
        if kind == "passkey":
            return f"passkey {ev.get('code')} — to be typed on the remote"
        if kind == "bonds":
            lst = ev.get("list") or []
            return f"{len(lst)} bonded: " + (", ".join(b.get("addr", "?") + (" (locked)" if b.get("locked") else "") for b in lst) or "none")
        if kind == "gatt":                     # the full table is long: count it, keep the services
            svcs = ev.get("services") or []
            return f"{len(svcs)} services, {sum(len(s.get('chars') or []) for s in svcs)} characteristics: " + \
                ", ".join(str(s.get("uuid")) for s in svcs)
        rest = {k: v for k, v in ev.items() if k not in DROP_FIELDS}
        return json.dumps(rest, separators=(",", ":"), ensure_ascii=False)
