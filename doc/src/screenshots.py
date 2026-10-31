#!/usr/bin/env python3
"""Capture the user-manual screenshots (doc/images/*.png) by driving the real web app in Chrome.

Needs no hardware: it runs against two local bridges that you start first —

    .venv/bin/python backend/bridge.py --simulate --http-port 8014 --reports /tmp/simreports &
    .venv/bin/python -I doc/src/fake_nordic.py "$PWD" /tmp/simreports &      # "sniffer image" screen, port 8015
    .venv/bin/python doc/src/screenshots.py doc/images /tmp/chrome-doc-profile

then stop both bridges. Uses google-chrome (DevTools protocol) and aiohttp from the project venv.
"""
import asyncio, base64, json, os, subprocess, sys, time
import aiohttp

OUT = sys.argv[1]                  # doc/images
SIM = "http://localhost:8014"
PROF = sys.argv[2]

chrome = subprocess.Popen(["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9335",
                           f"--user-data-dir={PROF}", "--window-size=1400,1000", "--hide-scrollbars", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Page:
    def __init__(self, ws): self.ws, self.n, self.events = ws, 0, []

    async def cmd(self, method, **params):
        self.n += 1; my = self.n
        await self.ws.send_str(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            m = json.loads((await self.ws.receive()).data)
            if m.get("id") == my:
                if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            if m.get("method") == "Runtime.exceptionThrown":
                print("  JS EXCEPTION:", json.dumps(m["params"]["exceptionDetails"])[:300])

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def wait(self, sec):
        end = time.time() + sec
        while time.time() < end:
            await self.js("1"); await asyncio.sleep(0.25)

    async def click(self, sel): await self.js(f"document.querySelector({json.dumps(sel)}).click()")
    async def tab(self, name): await self.click(f'.tabs button[data-tab="{name}"]'); await self.wait(0.6)

    async def shot(self, name, sel=None, pad=0, max_h=4000):
        clip = None
        if sel:
            r = await self.js(f"""(() => {{ const e = document.querySelector({json.dumps(sel)});
                const b = e.getBoundingClientRect(); return [b.x, b.y + window.scrollY, b.width, b.height]; }})()""")
            x, y, w, h = r
            clip = {"x": max(0, x - pad), "y": max(0, y - pad), "width": w + 2 * pad, "height": min(h + 2 * pad, max_h), "scale": 1}
        r = await self.cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True, **({"clip": clip} if clip else {}))
        path = os.path.join(OUT, name)
        open(path, "wb").write(base64.b64decode(r["data"]))
        print("  saved", name)


async def sim_send(session, *cmds):
    async with session.ws_connect(SIM + "/ws") as ws:
        for c in cmds: await ws.send_str(json.dumps(c))
        await asyncio.sleep(0.2)


async def main():
    async with aiohttp.ClientSession() as s:
        for _ in range(50):
            try: tabs = await (await s.get("http://127.0.0.1:9335/json")).json(); break
            except Exception: await asyncio.sleep(0.2)
        url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
        async with s.ws_connect(url, max_msg_size=64 * 1024 * 1024) as ws:
            p = Page(ws)
            await p.cmd("Runtime.enable"); await p.cmd("Page.enable")
            await p.cmd("Emulation.setDeviceMetricsOverride", width=1400, height=1000, deviceScaleFactor=1, mobile=False)

            print("connect / scan")
            await p.cmd("Page.navigate", url=SIM + "/#connect"); await p.wait(2.5)
            await p.click("#btn-scan"); await p.wait(4)
            await p.shot("app-connect-scan.png", "#tab-connect .grid-2", pad=8)

            print("connect / bond / gatt")
            await p.js("const a=document.querySelector('#conn-addr'); a.value='60:74:B1:80:AF:1E';")
            await p.click("#btn-pair"); await p.wait(9)
            await p.js("document.querySelector('#bonds-box').open = true")
            await p.shot("app-banner.png", "#banner")
            await p.shot("app-connect-ready.png", "#tab-connect", pad=8, max_h=2600)
            await p.shot("app-remote-info.png", "#remote-info", pad=40)
            await p.js("document.querySelector('#gatt-tree').style.maxHeight='none'")
            await p.js("document.querySelector('.hidmap')?.setAttribute('open','')")
            await p.shot("app-gatt.png", "#gatt-tree", pad=12, max_h=3000)
            await p.js("document.querySelector('#gatt-tree').style.maxHeight=''")

            print("keys")
            usages = await p.js("import('/js/remote_layout.js').then(m => m.buttons().filter(b => b.expect && b.usages.length).map(b => b.usages[0]))")
            await sim_send(s, {"cmd": "sim_keys", "usages": usages[:14] + ["0x68"], "gap": 0.15})
            await p.tab("keys"); await p.wait(len(usages[:14]) * 0.3 + 2)
            await p.shot("app-keys.png", "#tab-keys", pad=8)

            print("voice")
            await p.tab("voice"); await p.click("#btn-rec"); await p.wait(0.5)
            await sim_send(s, {"cmd": "sim_voice", "sec": 3})
            await p.wait(1.8); await p.shot("app-voice-streaming.png", "#tab-voice", pad=8)
            await p.wait(2.5); await p.click("#btn-rec"); await p.wait(0.5)
            await p.click("#btn-save"); await p.wait(2.5)
            await p.shot("app-voice.png", "#tab-voice", pad=8)

            print("ir")
            await p.tab("ir"); await p.click("#btn-ir-learn"); await p.wait(0.4)
            await sim_send(s, {"cmd": "sim_ir", "addr": "0x04", "code": "0x08"})
            await p.wait(1.5)
            await sim_send(s, {"cmd": "sim_ir", "addr": "0x20", "code": "0x45"})
            await p.wait(1.5)
            await p.shot("app-ir.png", "#tab-ir", pad=8)

            print("test runner")
            await p.tab("test")
            await p.js("document.querySelector('#t-dut').value='SIM-RCU-1 #0001'; document.querySelector('#t-operator').value='bench 1'")
            await p.shot("app-test-idle.png", "#tab-test", pad=8)
            await p.click("#btn-t-run"); await p.wait(2.5)
            await p.shot("app-test-keys-step.png", "#tab-test", pad=8)
            await sim_send(s, {"cmd": "sim_keys", "usages": usages, "gap": 0.12})
            await p.wait(len(usages) * 0.27 + 3)
            await sim_send(s, {"cmd": "sim_voice", "sec": 2})
            await p.wait(12)
            await p.click("#btn-t-export"); await p.wait(2.5)
            await p.shot("app-test-done.png", "#tab-test", pad=8)

            print("connect log after session")
            await p.tab("connect"); await p.wait(1)
            await p.shot("app-log.png", ".log-card", pad=8, max_h=900)

            print("phone width")
            await p.cmd("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
            for tab in ("connect", "keys", "test"):
                await p.cmd("Page.navigate", url="about:blank"); await p.wait(0.3)
                await p.cmd("Page.navigate", url=SIM + "/#" + tab); await p.wait(3)
                await p.wait(2); await p.js("window.scrollTo(0,0); document.querySelectorAll('.toast').forEach(t => t.remove())")
                r = await p.cmd("Page.captureScreenshot", format="png")
                open(os.path.join(OUT, f"phone-{tab}.png"), "wb").write(base64.b64decode(r["data"])); print("  saved phone-" + tab)

            print("sniffer image detected (fake Nordic port on :8015)")
            await p.cmd("Emulation.setDeviceMetricsOverride", width=1400, height=420, deviceScaleFactor=1, mobile=False)
            await p.cmd("Page.navigate", url="about:blank"); await p.wait(0.3)
            await p.cmd("Page.navigate", url="http://localhost:8015/#connect"); await p.wait(3)
            await p.js("document.querySelectorAll('.toast').forEach(t => t.remove())")
            r = await p.cmd("Page.captureScreenshot", format="png")
            open(os.path.join(OUT, "app-sniffer-image.png"), "wb").write(base64.b64decode(r["data"])); print("  saved app-sniffer-image.png")

try:
    asyncio.run(main())
finally:
    chrome.terminate()
