"""
tool_proto.py — host side of the xiao-test-tool line protocol (brief §6.1).

One JSON object per line, UTF-8, '\\n'-terminated, over USB CDC.

  encode(cmd)        dict -> bytes (validated)
  parse(line)        bytes -> dict | None
  ToolLink           asyncio wrapper around a pyserial port (reader thread)
  probe(port)        ping the port; return the `hello` event or None
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Callable

import serial

log = logging.getLogger("tool_proto")

FW_NAME = "xiao-test-tool"

# cmd -> required keys
COMMANDS: dict[str, tuple[str, ...]] = {
    "ping": (), "scan": (), "scan_stop": (), "connect": ("addr",), "pair": ("addr",), "lock": (), "unlock": (), "bonds": (),
    "bond_delete": ("addr",), "bond": (), "repair": (), "disconnect": (),
    "subscribe": (), "battery": (), "state": (), "info": (), "mic": (), "gatt_write": ("handle", "bytes"),
    "gatt_read": ("handle",), "clear_bonds": (), "ir_rx": (), "ir_tx": ("addr", "code"),
    "ir_tx_raw": ("timings",), "mode": ("value",), "dfu": (),
}


class ProtocolError(ValueError):
    pass


def encode(cmd: dict[str, Any]) -> bytes:
    name = cmd.get("cmd")
    if name not in COMMANDS:
        raise ProtocolError("unknown command %r" % name)
    missing = [k for k in COMMANDS[name] if k not in cmd]
    if missing:
        raise ProtocolError("%s: missing %s" % (name, ", ".join(missing)))
    if name == "ir_tx_raw":
        t = cmd["timings"]
        if not isinstance(t, list) or not t or not all(isinstance(x, int) and 0 < x < 65536 for x in t):
            raise ProtocolError("ir_tx_raw: timings must be a list of 1..65535 µs ints")
        if len(t) > 200:
            raise ProtocolError("ir_tx_raw: at most 200 timings")
    line = json.dumps(cmd, separators=(",", ":"))
    if len(line) >= 1500:
        raise ProtocolError("command too long for firmware line buffer")
    return line.encode() + b"\n"


def parse(line: bytes | str) -> dict[str, Any] | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and "ev" in obj else None


class ToolLink:
    """Serial link to the firmware. Lines are delivered on the asyncio loop."""

    def __init__(self, port: str, baud: int = 1_000_000):
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wlock = threading.Lock()
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.last_rx = 0.0

    def open(self, ser: serial.Serial | None = None) -> None:
        self.ser = ser or serial.Serial(self.port, self.baud, timeout=0.2)
        loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._reader, args=(loop,), daemon=True, name="tool-rx")
        self._thread.start()

    def _reader(self, loop: asyncio.AbstractEventLoop) -> None:
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = self.ser.read(self.ser.in_waiting or 1)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    ev = parse(line)
                    if ev is not None:
                        ev["host_ts"] = time.time() * 1000.0
                        self.last_rx = time.monotonic()
                        loop.call_soon_threadsafe(self.queue.put_nowait, ev)
                    elif line.strip():
                        log.debug("non-json from fw: %r", line[:120])
        except (serial.SerialException, OSError, TypeError) as e:
            log.warning("serial read ended: %s", e)
        finally:
            loop.call_soon_threadsafe(self.queue.put_nowait, None)  # EOF marker

    def send(self, cmd: dict[str, Any]) -> None:
        data = encode(cmd)
        with self._wlock:
            if not self.ser:
                raise serial.SerialException("port closed")
            self.ser.write(data)

    def close(self) -> None:
        self._stop.set()
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None


def probe(port: str, baud: int = 1_000_000, timeout: float = 2.0,
          opener: Callable[..., serial.Serial] = serial.Serial) -> dict | None:
    """Blocking: send ping, wait for a hello. Returns the hello dict or None."""
    try:
        ser = opener(port, baud, timeout=0.1)
    except (serial.SerialException, OSError) as e:
        log.debug("probe %s: %s", port, e)
        return None
    try:
        ser.reset_input_buffer()
        ser.write(encode({"cmd": "ping"}))
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            buf += ser.read(256)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                ev = parse(line)
                if ev and ev.get("ev") == "hello":
                    return ev
            if len(buf) > 4096:  # binary chatter (e.g. SLIP from the sniffer) — not us
                return None
        return None
    except (serial.SerialException, OSError) as e:  # port vanished mid-probe (reset / re-enumeration)
        log.debug("probe %s: %s", port, e)
        return None
    finally:
        ser.close()
