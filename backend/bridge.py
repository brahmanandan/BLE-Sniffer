#!/usr/bin/env python3
"""
bridge.py — serial ⇄ WebSocket bridge + static web server for the remote test suite.

    python backend/bridge.py                    # auto-detect board, serve http://0.0.0.0:8000
    python backend/bridge.py --port /dev/ttyACM0
    python backend/bridge.py --simulate         # no hardware: simulated xiao-test-tool
    python backend/bridge.py --https            # also serve HTTPS on :8443 (needed for PWA install on phones)

Single-purpose: the bridge only drives the xiao-test-tool firmware (brief §5). A `ping`
answered by a `hello` from "xiao-test-tool" -> connected. A board running the Nordic
sniffer image is recognised (USB ID / SnifferAPI framing) but not driven: the bridge
reports it with a hint to flash the tool firmware or use Wireshark for passive capture
(README "Passive capture"), and keeps serving the app read-only. The board is
re-detected whenever it disappears (e.g. after reflashing), so the bridge can stay running.

Environment overrides: BRIDGE_PORT, BRIDGE_HTTP_PORT, BRIDGE_BIND, BRIDGE_BAUD,
BRIDGE_REPORTS, BRIDGE_HTTPS_PORT.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiohttp import WSMsgType, web  # noqa: E402
from serial.tools import list_ports  # noqa: E402

from backend import adpcm, hid_usage, lg_magic, reason_codes, tool_proto  # noqa: E402
from backend.report import Report  # noqa: E402
from backend.session_log import SessionLog  # noqa: E402

log = logging.getLogger("bridge")

KNOWN_VIDS = {0x2886: "Seeed", 0x1915: "Nordic", 0x239A: "Adafruit"}
NORDIC_SNIFFER = (0x1915, 0x522A)
CACHED_EVENTS = ("hello", "state", "gatt", "hid", "session_log", "bonds")
SNIFFER_HINT = "flash xiao-test-tool, or use Wireshark for passive capture"


def is_nordic_sniffer(p: Any) -> bool:
    """Blocking. True if the port is the Nordic nRF Sniffer image (USB ID, else SLIP probe)."""
    if (getattr(p, "vid", None), getattr(p, "pid", None)) == NORDIC_SNIFFER:
        return True
    try:
        if str(ROOT / "extcap") not in sys.path:
            sys.path.append(str(ROOT / "extcap"))   # vendored SnifferAPI
        from SnifferAPI import UART
        return UART.find_sniffer_baudrates(p.device, write_data=True) is not None
    except Exception as e:
        log.debug("sniffer probe %s: %s", p.device, e)
        return False


class Client:
    """One WebSocket client with its own bounded send queue (slow phones don't stall others)."""

    def __init__(self, ws: web.WebSocketResponse, peer: str):
        self.ws = ws
        self.peer = peer
        self.q: asyncio.Queue[str] = asyncio.Queue(maxsize=3000)
        self.dropped = 0
        self.task = asyncio.get_running_loop().create_task(self._writer())

    def put(self, text: str) -> None:
        try:
            self.q.put_nowait(text)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _writer(self) -> None:
        try:
            while True:
                text = await self.q.get()
                await self.ws.send_str(text)
        except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
            pass


class Bridge:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.clients: set[Client] = set()
        self.status: dict[str, Any] = {"ev": "bridge", "connected": False, "fw": None, "ver": None,
                                       "port": None, "reason": None, "hint": None,
                                       "simulated": bool(args.simulate)}
        self.cache: dict[str, dict] = {}
        self.link: Any = None          # ToolLink | SimLink
        self.reports = Path(args.reports).resolve()
        self.reports.mkdir(parents=True, exist_ok=True)
        self.session_log = SessionLog(self.reports / "logs")
        # voice
        self.voice_stream: list[bytes] = []
        self.voice_last: list[bytes] = []
        self.voice_rec: list[bytes] | None = None
        self.voice_t: list[float] = []      # arrival times of the current stream (rate estimate)
        self.voice_stop_t = 0.0             # monotonic time of the last stream stop
        self.lg_keys = lg_magic.KeyDecoder()  # LG Magic Remote: keys hidden in its HID data stream
        # usb latency estimate
        self._ping_sent: float | None = None
        self.usb_rtt_ms: float | None = None
        self.flashing = False

    # -- fan-out --------------------------------------------------------------
    def broadcast(self, msg: dict) -> None:
        self.session_log.event(msg)
        if msg.get("ev") in CACHED_EVENTS:
            self.cache[msg["ev"]] = msg
        if msg.get("ev") == "battery":
            self.cache["battery_" + str(msg.get("which", "jig"))] = msg
        text = json.dumps(msg, separators=(",", ":"))
        for c in list(self.clients):
            c.put(text)

    def set_session_log(self, on: bool) -> None:
        """Start (new timestamped file) or stop the background session log; tell every client."""
        if on and not self.session_log.active:
            st = self.cache.get("state", {})
            self.session_log.start({
                "jig": f"{self.status.get('fw') or '-'} {self.status.get('ver') or ''} on {self.status.get('port') or '-'}",
                "remote": f"{st.get('addr', '-')} (connected: {bool(st.get('conn'))})",
                "bridge": f"http port {self.args.http_port}, reports {self.reports}",
            })
        elif not on:
            self.session_log.stop()
        self.broadcast(dict(self.session_log.status(), src="bridge"))

    @staticmethod
    def replay(ev: dict) -> str:
        """A cached event re-sent to a (re)connecting client: marked so it isn't mistaken for new traffic."""
        return json.dumps(dict(ev, cached=True), separators=(",", ":"))

    def set_status(self, **kw: Any) -> None:
        self.status.update(kw)
        self.status["usb_rtt_ms"] = self.usb_rtt_ms
        self.broadcast(dict(self.status))

    def notice(self, level: str, msg: str) -> None:
        log.info("%s: %s", level, msg)
        self.broadcast({"src": "bridge", "ev": "log", "level": level, "msg": msg})

    # -- device discovery ------------------------------------------------------
    def candidate_ports(self) -> list[Any]:
        ports = list_ports.comports()
        if self.args.port:
            return [p for p in ports if p.device == self.args.port] or [type("P", (), {"device": self.args.port, "vid": None, "pid": None})()]
        known = [p for p in ports if p.vid in KNOWN_VIDS]
        return known or [p for p in ports if "ACM" in p.device or "usbmodem" in p.device]

    def detect(self, p: Any) -> str | None:
        """Blocking. Returns 'tool' / 'nordic' / None."""
        nordic = (getattr(p, "vid", None), getattr(p, "pid", None)) == NORDIC_SNIFFER
        if not nordic:
            hello = tool_proto.probe(p.device, self.args.baud, timeout=2.0)
            if hello and hello.get("fw") == tool_proto.FW_NAME:
                self._probe_hello = hello
                return "tool"
        return "nordic" if is_nordic_sniffer(p) else None

    async def device_loop(self) -> None:
        loop = asyncio.get_running_loop()
        waiting_logged = False
        while True:
            if self.flashing:
                await asyncio.sleep(1)
                continue
            if self.args.simulate:
                from backend.sim_device import SimLink
                await self.tool_session(SimLink(), {"fw": "xiao-test-tool"})
                await asyncio.sleep(1)
                continue
            mode = port = None
            for p in self.candidate_ports():
                mode = await loop.run_in_executor(None, self.detect, p)
                if mode:
                    port = p.device
                    break
            if not mode:
                if self.status.get("reason"):
                    self.set_status(reason=None, hint=None, fw=None, port=None)
                if not waiting_logged:
                    log.info("no board detected; waiting (plug in the XIAO or pass --port)")
                    waiting_logged = True
                await asyncio.sleep(2)
                continue
            waiting_logged = False
            log.info("detected %s firmware on %s", mode, port)
            try:
                if mode == "tool":
                    await self.tool_session(tool_proto.ToolLink(port, self.args.baud), self._probe_hello)
                else:
                    await self.nordic_image(port)
            except Exception as e:  # keep serving; re-detect
                log.exception("session on %s ended: %s", port, e)
            self.link = None
            self.cache.clear()
            self.set_status(connected=False, fw=None, ver=None, port=None, reason=None, hint=None)
            await asyncio.sleep(1.5)

    # -- tool mode -------------------------------------------------------------
    async def tool_session(self, link: Any, hello: dict) -> None:
        link.open()
        self.link = link
        self.set_status(connected=True, fw=hello.get("fw"), ver=hello.get("ver"), port=link.port,
                        reason=None, hint=None)
        pinger = asyncio.get_running_loop().create_task(self._pinger(link))
        try:
            link.send({"cmd": "ping"})
            link.send({"cmd": "battery"})
            link.send({"cmd": "info"})       # gatt + hid + state of a link that predates this session
            while True:
                ev = await link.queue.get()
                if ev is None:
                    break
                self.on_tool_event(ev)
        finally:
            pinger.cancel()
            link.close()

    async def _pinger(self, link: Any) -> None:
        while True:
            await asyncio.sleep(5)
            self._ping_sent = time.monotonic()
            try:
                link.send({"cmd": "ping"})
            except Exception:
                return
            if time.monotonic() - link.last_rx > 20:
                log.warning("firmware silent for 20 s; reconnecting")
                link.close()
                return

    def on_tool_event(self, ev: dict) -> None:
        kind = ev.get("ev")
        if kind == "hello":
            if self._ping_sent is not None:
                self.usb_rtt_ms = round((time.monotonic() - self._ping_sent) * 1000, 1)
                self._ping_sent = None
                self.status["usb_rtt_ms"] = self.usb_rtt_ms
            if self.status.get("ver") != ev.get("ver"):
                self.set_status(fw=ev.get("fw"), ver=ev.get("ver"))
        elif kind == "key":
            ev = hid_usage.resolve(ev)
        elif kind == "hid_data" and lg_magic.is_lg(self.cache.get("gatt")):
            ev["src"] = "tool"
            self.broadcast(ev)
            for k in self.lg_keys.feed(ev):
                k["src"] = "tool"
                self.broadcast(k)
            return
        elif kind == "state" and not ev.get("conn"):
            self.lg_keys = lg_magic.KeyDecoder()
        elif kind == "log" and isinstance(ev.get("msg"), str):
            # "disconnected (reason 0x08)" -> "... (reason 0x08 = Connection Timeout: link lost — …)"
            ev["msg"], codes = reason_codes.annotate(ev["msg"])
            if codes:
                ev["codes"] = codes
        elif kind == "voice_state":
            now = time.monotonic()
            if ev.get("active"):
                # Audio packets still in flight after a stop re-open the stream for a moment;
                # treat that as the tail of the previous utterance, not a new one.
                if now - self.voice_stop_t < 2.0 and self.voice_last:
                    self.voice_stream = self.voice_last
                else:
                    self.voice_stream = []
                    self.voice_t = []
            else:
                self.voice_stop_t = now
                if self.voice_stream:
                    self.voice_last = self.voice_stream
        elif kind == "voice":
            try:
                b = bytes.fromhex(ev.get("bytes", ""))
            except ValueError:
                b = b""
            self.voice_stream.append(b)
            self.voice_t.append(ev.get("host_ts", time.time() * 1000))
            if self.voice_rec is not None:
                self.voice_rec.append(b)
            ev["level"] = round(adpcm.energy(b), 3)
        ev["src"] = "tool"
        self.broadcast(ev)

    # -- Nordic sniffer image: recognised, not driven ----------------------------
    async def nordic_image(self, port: str) -> None:
        """Report the Nordic image and idle until the port goes away (reflash / unplug).
        The port is not held open, so Wireshark's extcap can use it meanwhile."""
        log.warning("%s runs the Nordic sniffer image: %s", port, SNIFFER_HINT)
        self.set_status(connected=False, fw="nrf-sniffer", ver=None, port=port,
                        reason="nordic-sniffer-image", hint=SNIFFER_HINT)
        while not self.flashing and any(p.device == port for p in list_ports.comports()):
            await asyncio.sleep(2)

    # -- commands from the web app ----------------------------------------------
    async def handle_cmd(self, client: Client, msg: dict) -> None:
        cmd = msg.get("cmd")
        reply = lambda d: client.put(json.dumps(dict(d, src="bridge"), separators=(",", ":")))
        self.session_log.command(msg)
        try:
            if cmd == "session_log":
                self.set_session_log(bool(msg.get("on", True)))
            elif cmd == "note":
                self.session_log.note(str(msg.get("text", ""))[:500])
            elif cmd == "bridge":
                reply(self.status)
                for ev in self.cache.values():
                    client.put(self.replay(ev))
            elif cmd == "time":
                reply({"ev": "time", "client": msg.get("t"), "server": time.time() * 1000.0})
            elif cmd == "voice_record":
                if msg.get("on", True):
                    self.voice_rec = []
                    reply({"ev": "voice_record", "on": True})
                else:
                    n = len(self.voice_rec or [])
                    reply({"ev": "voice_record", "on": False, "frames": n})
            elif cmd == "voice_save":
                reply(await self.voice_save(msg))
            elif cmd == "report_save":
                reply(self.save_report(msg.get("report") or {}))
            elif cmd == "flash":
                await self.do_flash(str(msg.get("target", "")))
            elif self.status["connected"] and self.link:
                if cmd and cmd.startswith("sim_") and not self.args.simulate:
                    raise tool_proto.ProtocolError("%s only works with --simulate" % cmd)
                # ToolLink validates via tool_proto.encode; SimLink also accepts sim_* commands.
                self.link.send({k: v for k, v in msg.items() if k != "id"})
            elif self.status.get("reason") == "nordic-sniffer-image":
                reply({"ev": "error", "msg": "the board runs the Nordic sniffer image: " + SNIFFER_HINT, "cmd": cmd})
            else:
                reply({"ev": "error", "msg": "no board connected", "cmd": cmd})
        except tool_proto.ProtocolError as e:
            reply({"ev": "error", "msg": str(e), "cmd": cmd})
        except Exception as e:
            log.exception("command %s failed", cmd)
            reply({"ev": "error", "msg": "%s: %s" % (type(e).__name__, e), "cmd": cmd})

    async def voice_save(self, msg: dict) -> dict:
        frames = self.voice_rec if self.voice_rec else (self.voice_stream or self.voice_last)
        if not frames:
            return {"ev": "voice_saved", "ok": False, "msg": "no voice frames captured yet"}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        vdir = self.reports / "voice"
        vdir.mkdir(exist_ok=True)
        raw = b"".join(frames)
        (vdir / ("voice_%s.bin" % stamp)).write_bytes(raw)
        out: dict[str, Any] = {"ev": "voice_saved", "ok": True, "raw_url": "/files/voice/voice_%s.bin" % stamp,
                               "frames": len(frames), "bytes": len(raw)}
        rate = int(msg.get("rate") or self.estimate_voice_rate())
        try:
            wav, info = adpcm.frames_to_wav(frames, msg.get("codec", "auto"), rate)
            (vdir / ("voice_%s.wav" % stamp)).write_bytes(wav)
            out.update(url="/files/voice/voice_%s.wav" % stamp, info=info)
            if msg.get("base64"):
                out["wav_b64"] = base64.b64encode(wav).decode()
        except Exception as e:  # raw capture already saved
            out.update(decode_error=str(e))
        return out

    def estimate_voice_rate(self) -> int:
        """IMA-ADPCM is 4 bits/sample: bytes/s * 2 = sample rate. Snap to 8 or 16 kHz."""
        t = self.voice_t
        if len(t) > 50 and t[-1] > t[0]:
            n = sum(len(b) for b in self.voice_stream[1:len(t)])
            sps = n * 2 / ((t[-1] - t[0]) / 1000.0)
            return 16000 if sps > 12000 else 8000
        return 8000

    def save_report(self, data: dict) -> dict:
        rep = Report.from_dict(data)
        paths = self.save_report_obj(rep)
        self.session_log.note(f"report saved: {paths.get('json', '')} — verdict {rep.verdict.upper()}")
        return {"ev": "report_saved", "ok": True, "verdict": rep.verdict, **paths}

    def save_report_obj(self, rep: Report) -> dict:
        paths = rep.save(self.reports)
        return {k: "/files/" + p.name for k, p in paths.items()}

    async def do_flash(self, target: str) -> None:
        from backend import flash
        if target not in flash.IMAGES:
            raise tool_proto.ProtocolError("flash target must be 'tool' or 'sniffer'")
        if self.flashing:
            raise tool_proto.ProtocolError("already flashing")
        self.flashing = True
        port = self.status.get("port")
        status = lambda m: self.broadcast({"src": "bridge", "ev": "flash", "msg": m})
        try:
            # release the port so the firmware can be asked to enter DFU
            if self.link:
                self.link.close()
            await asyncio.sleep(0.5)
            await flash.flash_async(target, port if self.status.get("connected") else None, status)
            self.broadcast({"src": "bridge", "ev": "flash", "msg": "flashed %s; waiting for the board…" % target, "done": True})
        except Exception as e:
            self.broadcast({"src": "bridge", "ev": "flash", "msg": "flash failed: %s" % e, "error": True})
        finally:
            await asyncio.sleep(2)
            self.flashing = False

    # -- HTTP / WS ----------------------------------------------------------------
    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        client = Client(ws, request.remote or "?")
        self.clients.add(client)
        log.info("ws client %s connected (%d total)", client.peer, len(self.clients))
        client.put(json.dumps(self.status))
        for ev in self.cache.values():
            client.put(self.replay(ev))
        try:
            async for m in ws:
                if m.type == WSMsgType.TEXT:
                    try:
                        msg = json.loads(m.data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict) and "cmd" in msg:
                        await self.handle_cmd(client, msg)
                elif m.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(client)
            client.task.cancel()
            log.info("ws client %s gone", client.peer)
        return ws

    async def api_report(self, request: web.Request) -> web.Response:
        data = await request.json()
        return web.json_response(self.save_report(data))

    async def api_status(self, request: web.Request) -> web.Response:
        return web.json_response(dict(self.status, clients=len(self.clients)))

    def app(self) -> web.Application:
        webroot = Path(self.args.webroot).resolve()

        @web.middleware
        async def no_cache(request, handler):
            resp = await handler(request)
            if request.path.endswith((".html", ".js", ".css", ".webmanifest", "/")):
                resp.headers["Cache-Control"] = "no-cache"
            return resp

        app = web.Application(middlewares=[no_cache], client_max_size=8 * 1024 * 1024)
        app.router.add_get("/ws", self.ws_handler)
        app.router.add_get("/api/status", self.api_status)
        app.router.add_post("/api/report", self.api_report)
        app.router.add_get("/", lambda r: web.FileResponse(webroot / "index.html"))
        certs = ROOT / ".certs"
        if (certs / "ca.crt").exists():
            app.router.add_get("/ca.crt", lambda r: web.FileResponse(certs / "ca.crt", headers={
                "Content-Type": "application/x-x509-ca-cert"}))
        app.router.add_static("/files/", self.reports, show_index=True)
        app.router.add_static("/", webroot)
        return app


# ---------------------------------------------------------------------------
# HTTPS (self-signed local CA so phones can trust it once and install the PWA)
# ---------------------------------------------------------------------------
def lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def ensure_certs() -> ssl.SSLContext:
    d = ROOT / ".certs"
    d.mkdir(exist_ok=True)
    ca_key, ca_crt, key, crt = d / "ca.key", d / "ca.crt", d / "server.key", d / "server.crt"
    ips = lan_ips()
    san = ",".join(["DNS:localhost", "DNS:%s" % socket.gethostname(), "DNS:%s.local" % socket.gethostname(),
                    "IP:127.0.0.1"] + ["IP:%s" % ip for ip in ips])
    stamp = d / "san.txt"
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)
    if not ca_crt.exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-keyout", str(ca_key),
            "-out", str(ca_crt), "-subj", "/CN=BT-Sniffer test-jig local CA",
            "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    if not crt.exists() or not stamp.exists() or stamp.read_text() != san:
        csr = d / "server.csr"
        ext = d / "ext.cnf"
        ext.write_text("subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n" % san)
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(csr),
            "-subj", "/CN=%s" % socket.gethostname())
        run("openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
            "-days", "825", "-sha256", "-extfile", str(ext), "-out", str(crt))
        stamp.write_text(san)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(str(crt), str(key))
    return ctx


# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="XIAO nRF52840 remote test-suite bridge")
    ap.add_argument("--port", default=env("BRIDGE_PORT"), help="serial port (default: auto-detect)")
    ap.add_argument("--http-port", type=int, default=int(env("BRIDGE_HTTP_PORT", 8000)))
    ap.add_argument("--bind", default=env("BRIDGE_BIND", "0.0.0.0"))
    ap.add_argument("--baud", type=int, default=int(env("BRIDGE_BAUD", 1_000_000)))
    ap.add_argument("--simulate", action="store_true", help="no hardware: run a simulated xiao-test-tool")
    ap.add_argument("--https", action="store_true", help="also serve HTTPS (self-signed local CA) for PWA install")
    ap.add_argument("--https-port", type=int, default=int(env("BRIDGE_HTTPS_PORT", 8443)))
    ap.add_argument("--webroot", default=str(ROOT / "webapp"))
    ap.add_argument("--reports", default=env("BRIDGE_REPORTS", str(ROOT / "reports")))
    ap.add_argument("--session-log", action="store_true", default=env("BRIDGE_SESSION_LOG", "") not in ("", "0"),
                    help="start writing reports/logs/session_<date-time>.log right away")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


async def amain(args: argparse.Namespace) -> None:
    bridge = Bridge(args)
    ctx = ensure_certs() if args.https else None   # before app(): it serves /ca.crt if present
    runner = web.AppRunner(bridge.app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, args.bind, args.http_port).start()
    urls = ["http://localhost:%d" % args.http_port] + ["http://%s:%d" % (ip, args.http_port) for ip in lan_ips()]
    if ctx:
        await web.TCPSite(runner, args.bind, args.https_port, ssl_context=ctx).start()
        urls += ["https://%s:%d" % (ip, args.https_port) for ip in lan_ips()]
        log.info("HTTPS on :%d — install http://<this-pc>:%d/ca.crt on the phone once to trust it",
                 args.https_port, args.http_port)
    log.info("serving web app: %s", "  ".join(urls))
    try:
        if args.session_log:
            bridge.set_session_log(True)
        await bridge.device_loop()
    finally:
        bridge.session_log.stop()
        await runner.cleanup()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    if args.log_level.upper() != "DEBUG":
        # SnifferAPI (used only to recognise the Nordic image) logs through the root logger.
        logging.getLogger().handlers[0].addFilter(lambda r: r.name != "root" or r.levelno >= logging.WARNING)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
