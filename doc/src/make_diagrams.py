#!/usr/bin/env python3
"""Generate the user-manual diagrams (SVG) into doc/.

    python3 doc/src/make_diagrams.py        # writes doc/*.svg
    doc/src/render.sh                       # renders doc/*.svg -> doc/*.png (Chrome)

All diagrams share one palette and one small drawing kit (boxes, diamonds, arrows,
multi-line text) so they stay consistent when edited.
"""
from __future__ import annotations

import html
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent
FONT = "Inter, Segoe UI, Helvetica, Arial, sans-serif"
MONO = "JetBrains Mono, Consolas, Menlo, monospace"

# role -> (stroke, fill, text)
C = {
    "jig":     ("#1d4ed8", "#dbeafe", "#1e3a8a"),
    "sniffer": ("#b45309", "#fef3c7", "#78350f"),
    "host":    ("#334155", "#f1f5f9", "#0f172a"),
    "remote":  ("#6d28d9", "#ede9fe", "#4c1d95"),
    "ok":      ("#15803d", "#dcfce7", "#14532d"),
    "bad":     ("#b91c1c", "#fee2e2", "#7f1d1d"),
    "q":       ("#c2410c", "#fff7ed", "#7c2d12"),
    "note":    ("#94a3b8", "#f8fafc", "#334155"),
    "plain":   ("#64748b", "#ffffff", "#0f172a"),
}
INK = "#0f172a"
MUTED = "#475569"


class SVG:
    def __init__(self, w: int, h: int, title: str, subtitle: str = ""):
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.title, self.subtitle = title, subtitle

    # -- primitives ---------------------------------------------------------------
    def text(self, x, y, lines, size=13, color=INK, anchor="middle", weight=400, mono=False, lh=1.3):
        if isinstance(lines, str):
            lines = [lines]
        fam = MONO if mono else FONT
        n = len(lines)
        y0 = y - (n - 1) * size * lh / 2
        for i, ln in enumerate(lines):
            w = weight
            if ln.startswith("**") and ln.endswith("**"):
                ln, w = ln[2:-2], 700
            self.parts.append(
                f'<text x="{x}" y="{y0 + i * size * lh:.1f}" font-family="{fam}" font-size="{size}" '
                f'font-weight="{w}" fill="{color}" text-anchor="{anchor}" dominant-baseline="middle">'
                f'{html.escape(ln)}</text>')

    def box(self, x, y, w, h, lines, role="plain", size=13, r=10, dash=False, align="middle"):
        stroke, fill, tc = C[role]
        d = ' stroke-dasharray="6 4"' if dash else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
                          f'stroke="{stroke}" stroke-width="1.6"{d}/>')
        tx = x + w / 2 if align == "middle" else x + 12
        self.text(tx, y + h / 2, lines, size=size, color=tc, anchor=align)
        return (x, y, w, h)

    def diamond(self, cx, cy, w, h, lines, role="q", size=12.5):
        stroke, fill, tc = C[role]
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        self.parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        self.text(cx, cy, lines, size=size, color=tc)

    def arrow(self, pts, color=MUTED, label=None, lpos=None, dash=False, both=False, width=1.6, lsize=12, lcolor=None):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        mk = self.marker(color)
        start = f' marker-start="url(#{mk}s)"' if both else ""
        dd = ' stroke-dasharray="5 4"' if dash else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dd}'
                          f' marker-end="url(#{mk})"{start}/>')
        if label:
            if lpos is None:
                (x1, y1), (x2, y2) = pts[0], pts[1]
                lpos = ((x1 + x2) / 2, (y1 + y2) / 2 - 10)
            self.label(lpos[0], lpos[1], label, color=lcolor or color, size=lsize)

    def label(self, x, y, lines, color=MUTED, size=12, anchor="middle", bg=True, weight=600):
        if isinstance(lines, str):
            lines = [lines]
        if bg:
            wmax = max(len(l) for l in lines) * size * 0.56 + 10
            hh = len(lines) * size * 1.3 + 4
            x0 = x - wmax / 2 if anchor == "middle" else x - 4
            self.parts.append(f'<rect x="{x0:.1f}" y="{y - hh / 2:.1f}" width="{wmax:.1f}" height="{hh:.1f}" '
                              f'rx="4" fill="#ffffff" opacity="0.92"/>')
        self.text(x, y, lines, size=size, color=color, anchor=anchor, weight=weight)

    _markers: dict[str, str] = {}

    def marker(self, color):
        mid = "m" + color.lstrip("#")
        self._markers[mid] = color
        return mid

    def raw(self, s):
        self.parts.append(s)

    def save(self, name):
        defs = "".join(
            f'<marker id="{m}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
            f'<marker id="{m}s" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M10,0 L0,5 L10,10 z" fill="{c}"/></marker>'
            for m, c in self._markers.items())
        head = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" '
                f'height="{self.h}" font-family="{FONT}">',
                f'<title>{html.escape(self.title)}</title>',
                f'<defs>{defs}</defs>',
                f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>']
        t = []
        t.append(f'<text x="28" y="36" font-size="21" font-weight="700" fill="{INK}">{html.escape(self.title)}</text>')
        if self.subtitle:
            t.append(f'<text x="28" y="60" font-size="13.5" fill="{MUTED}">{html.escape(self.subtitle)}</text>')
        out = "\n".join(head + t + self.parts + ["</svg>\n"])
        (DOC / name).write_text(out, encoding="utf-8")
        SVG._markers = {}
        print("wrote", DOC / name)


# =====================================================================================
def architecture():
    s = SVG(1000, 640, "System overview",
            "One XIAO nRF52840, two firmware images. The web app works only with xiao-test-tool; passive capture uses Wireshark.")
    # lane backgrounds
    s.raw('<rect x="20" y="80" width="960" height="330" rx="14" fill="#f8fbff" stroke="#bfdbfe"/>')
    s.text(36, 100, "**A · Test jig — normal use (this app)**", size=14, color="#1e3a8a", anchor="start")
    s.raw('<rect x="20" y="430" width="960" height="190" rx="14" fill="#fffcf2" stroke="#fde68a"/>')
    s.text(36, 450, "**B · Passive capture — occasional diagnostics (no app)**", size=14, color="#78350f", anchor="start")

    s.box(40, 150, 170, 74, ["**TV remote**", "BLE voice remote", "(keys · voice · battery)"], "remote")
    s.box(40, 250, 170, 60, ["**IR remote**", "(any 38 kHz)"], "remote")
    s.box(290, 140, 200, 120, ["**XIAO nRF52840**", "firmware:", "**xiao-test-tool**", "BLE central + IR + VBAT"], "jig")
    s.box(290, 300, 95, 58, ["Unit IR", "U002 · D0/D1"], "note", size=11.5)
    s.box(395, 300, 95, 58, ["LiPo + PCM", "(optional)"], "note", size=11.5)
    s.box(570, 150, 190, 100, ["**PC**", "backend/bridge.py", "aiohttp · port 8000", "(--https :8443)"], "host")
    s.box(570, 300, 190, 58, ["reports/", "JSON · HTML · WAV"], "note", size=12)
    s.box(820, 130, 140, 66, ["**Browser on PC**", "localhost:8000"], "host", size=12.5)
    s.box(820, 214, 140, 66, ["**Phone (PWA)**", "http://PC-IP:8000"], "host", size=12.5)

    s.arrow([(210, 187), (290, 187)], C["remote"][0], "BLE", (250, 175), both=True)
    s.arrow([(210, 280), (250, 280), (250, 230), (290, 230)], C["remote"][0], "IR", (232, 262))
    s.arrow([(337, 300), (337, 260)], MUTED, both=True)
    s.arrow([(442, 300), (442, 260)], MUTED)
    s.arrow([(490, 200), (570, 200)], C["jig"][0], ["USB serial", "JSON lines"], (530, 172), both=True, lsize=11)
    s.arrow([(665, 250), (665, 300)], MUTED)
    s.arrow([(760, 180), (820, 163)], C["host"][0], both=True)
    s.arrow([(760, 220), (820, 247)], C["host"][0], both=True)
    s.label(790, 140, ["HTTP +", "WebSocket"], size=11)
    s.text(500, 385, "Bonds with the remote as a stand-in TV → receives key presses and voice in clear text.",
           size=12.5, color="#1e3a8a")

    s.box(40, 480, 200, 70, ["**Remote ⇄ STB**", "(production link,", "encrypted once bonded)"], "remote")
    s.box(320, 475, 200, 80, ["**XIAO nRF52840**", "firmware:", "**Nordic nRF Sniffer 4.1**"], "sniffer")
    s.box(600, 480, 180, 70, ["**Wireshark**", "+ nRF Sniffer extcap"], "host")
    s.box(820, 480, 140, 70, ["ble_probe.py", "(terminal", "alternative)"], "note", size=12)
    s.arrow([(240, 515), (320, 515)], C["sniffer"][0], "listens", (280, 503), dash=True)
    s.arrow([(520, 515), (600, 515)], C["sniffer"][0], "USB", (560, 503))
    s.arrow([(780, 515), (820, 515)], MUTED, dash=True)
    s.text(500, 590, "The bridge recognises this image and shows “sniffer image” with a one-click flash back to xiao-test-tool.",
           size=12.5, color="#78350f")
    s.save("architecture.svg")


def firmware_decision():
    s = SVG(1000, 860, "Which firmware for which job?",
            "Only one image runs at a time. Switching takes ~20 s (Ch. 4). For both roles at once, use a second XIAO.")
    s.box(370, 82, 260, 46, ["**What do you want to do?**"], "plain", size=14)
    s.arrow([(500, 128), (500, 160)])
    s.diamond(500, 225, 430, 130, ["Read key presses, record voice,", "read battery / GATT / device info,",
                                   "send or learn IR, or run a", "pass/fail test on a remote?"])
    s.arrow([(285, 225), (180, 225), (180, 282)], C["ok"][0], "yes", (230, 213))
    s.arrow([(715, 225), (820, 225), (820, 282)], C["bad"][0], "no", (765, 213))

    # left: tool
    s.box(60, 282, 240, 86, ["**xiao-test-tool**", "BLE central + IR + battery", "→ use the web app"], "jig", size=13.5)
    s.arrow([(180, 368), (180, 400)])
    s.diamond(180, 455, 250, 104, ["Banner shows", "“jig online”?"], size=13)
    s.arrow([(180, 507), (180, 560)], C["ok"][0], "yes", (198, 532))
    s.arrow([(305, 455), (360, 455), (360, 560)], C["bad"][0], "no", (330, 443))
    s.box(290, 560, 160, 104, ["**Flash it**", "app: Flash", "xiao-test-tool", "or flash.py tool", "(double-tap RST)"], "jig", size=11.5)
    s.box(60, 560, 210, 104, ["**Pair the remote**", "STB off · pairing mode", "Connect ▸ Scan ▸", "Connect ▸ Bond (Ch. 7)"], "ok", size=12)
    s.arrow([(290, 612), (270, 612)], MUTED)
    s.arrow([(165, 664), (165, 700)])
    s.box(40, 700, 270, 110, ["Keys · Voice · IR · Test tabs", "GATT, Remote info, battery, log",
                              "Export report.json / .html", "(Ch. 6, 8)"], "ok", size=12)

    # right: sniffer
    s.diamond(820, 342, 300, 120, ["Watch a real remote ⇄ STB", "link without pairing", "the remote to anything?"])
    s.arrow([(820, 402), (820, 450)], C["ok"][0], "yes", (840, 425))
    s.arrow([(670, 342), (612, 342), (612, 470)], MUTED, "no", (640, 330))
    s.box(530, 470, 165, 92, ["Neither image", "fits: see", "Troubleshooting", "(Ch. 10)"], "note", size=12)
    s.box(720, 450, 220, 92, ["**Nordic nRF Sniffer**", "+ Wireshark extcap", "(not the web app)"], "sniffer", size=13)
    s.arrow([(830, 542), (830, 580)])
    s.box(710, 580, 240, 96, ["**Flash it**", "flash.py sniffer, or app:", "Connect ▸ Firmware ▸", "Flash Nordic sniffer"], "sniffer", size=12)
    s.arrow([(830, 676), (830, 712)])
    s.box(700, 712, 260, 98, ["Capture in Wireshark (Ch. 9)", "Encrypted after bonding:", "keys only readable via the jig.",
                              "Flash xiao-test-tool back after."], "note", size=12)
    s.box(380, 712, 280, 98, ["**Need both at the same time?**", "Use a second XIAO that", "stays on the sniffer image."],
          "q", size=12.5)
    s.save("firmware-decision.svg")


def flashing_flow():
    s = SVG(1000, 600, "Flashing and switching firmware",
            "backend/flash.py tool | sniffer — or the Firmware buttons in the app. Bonds and the jig identity survive a reflash.")
    s.box(390, 86, 220, 46, ["**Start: stop the bridge**", "(or use the app buttons)"], "plain", size=12.5)
    s.arrow([(500, 132), (500, 160)])
    s.diamond(500, 215, 330, 104, ["What runs on the board now?"], size=13.5)
    s.arrow([(335, 215), (190, 215), (190, 262)], C["jig"][0], "xiao-test-tool", (262, 203))
    s.arrow([(665, 215), (810, 215), (810, 262)], C["sniffer"][0], "Nordic sniffer / unknown", (738, 203))
    s.box(70, 262, 240, 92, ["**Automatic**", "flash.py sends {\"cmd\":\"dfu\"}", "→ board reboots into", "the UF2 bootloader"], "jig", size=12)
    s.box(690, 262, 240, 92, ["**Manual**", "Double-tap RST", "(two taps within ~0.5 s)", "LED pulses"], "sniffer", size=12)
    s.arrow([(190, 354), (190, 400), (380, 400)], MUTED)
    s.arrow([(810, 354), (810, 400), (620, 400)], MUTED)
    s.box(380, 372, 240, 58, ["USB drive appears:", "XIAO-BOOT or XIAO-SENSE"], "host", size=12.5)
    s.arrow([(500, 430), (500, 462)])
    s.box(330, 462, 340, 58, ["Copy the UF2 → board resets by itself", "(a write error at the very end is normal)"], "host", size=12.5)
    s.arrow([(500, 520), (500, 548)])
    s.box(360, 548, 280, 40, ["Bridge re-detects the board"], "ok", size=13)
    s.box(40, 440, 260, 140, ["**Which file?**", "tool: xiao_test_tool_full.uf2", "(S140 SoftDevice + app — always",
                              "use it after the sniffer, which", "overwrites part of the SoftDevice)",
                              "sniffer: sniffer_nrf52840dongle_4.1.0.uf2"], "note", size=11.5, align="start")
    s.box(700, 440, 260, 140, ["**Kept across a reflash**", "• bonds with remotes", "• jig identity key (/jig_irk.bin)",
                               "  so bonded remotes still", "  recognise the jig", "• reports/ on the PC"], "note", size=11.5, align="start")
    s.save("flashing-flow.svg")


def pairing_flow():
    s = SVG(1000, 975, "How the jig connects, pairs and checks a link",
            "Firmware logic in ble_central.cpp (v0.3). Only the session's locked remote is followed; Pair retries missed connections.")
    s.box(370, 82, 260, 48, ["**Remote advertises**"], "remote", size=14)
    s.arrow([(500, 130), (500, 156)])
    s.diamond(500, 216, 360, 116, ["What kind of advert?", "(DIRECT badge · advert flags)"], size=13)
    # three branches
    s.arrow([(320, 216), (150, 216), (150, 300)], C["remote"][0], "undirected, discoverable", (220, 204), lsize=11)
    s.arrow([(500, 274), (500, 300)], C["remote"][0])
    s.label(578, 286, ["directed at the jig, or", "undirected + not discoverable"], size=10.5, color=C["remote"][0])
    s.arrow([(680, 216), (850, 216), (850, 300)], C["remote"][0], "directed elsewhere / flags 0x04", (772, 204), lsize=11)

    s.box(40, 300, 220, 116, ["**Pairing mode**", "(PAIRING badge, flags 0x05/06)", "bonded remote → delete old bond",
                              "first, connect, pair fresh", "new remote → press Pair"], "ok", size=11.5)
    s.diamond(500, 358, 300, 116, ["Fresh-pair mode?", "(after a silent link)"], size=12.5)
    s.box(745, 300, 215, 116, ["**Not for the jig**", "Reconnecting to its own host,", "or another bonded remote while",
                               "the session is locked. Ignored.", "STB off · pairing mode · Pair"], "bad", size=11.5)
    s.arrow([(500, 416), (500, 446)], C["ok"][0], "no", (516, 431))
    s.arrow([(650, 358), (725, 358), (725, 440)], C["bad"][0], "yes", (690, 346))
    s.box(640, 440, 175, 62, ["Ignore it — wait for", "the pairing-mode advert"], "bad", size=11.5)
    s.box(380, 446, 240, 62, ["Connect + re-encrypt", "with the stored keys"], "jig", size=12.5)
    s.arrow([(150, 416), (150, 560), (380, 560)], C["ok"][0])
    s.arrow([(500, 508), (500, 534)])
    s.box(380, 534, 240, 52, ["Connect · discover GATT", "pair if needed (Just Works)"], "jig", size=12)
    s.arrow([(500, 586), (500, 612)])
    s.box(330, 612, 340, 62, ["Read HID map, Report IDs, battery · subscribe", "send ATV GET_CAPS (if the voice service exists)"],
          "jig", size=12)
    s.arrow([(500, 674), (500, 700)])
    s.diamond(500, 758, 330, 112, ["Remote answers or sends", "anything within 3 s?", "(waits while pairing runs)"], size=12.5)
    s.arrow([(335, 758), (210, 758), (210, 820)], C["ok"][0], "yes", (270, 746))
    s.arrow([(665, 758), (790, 758), (790, 820)], C["bad"][0], "no", (730, 746))
    s.box(70, 820, 280, 130, ["**LIVE**", "state.live = true", "Keys, voice and battery flow.", "Link stays up while in use; the",
                              "Fire TV remote also reconnects fine."], "ok", size=12)
    s.box(620, 820, 320, 130, ["**SILENT**", "banner “silent ⚠” · state.live = false", "Jig drops the link and enters fresh-pair",
                               "mode: ignores directed reconnects until", "the remote shows its pairing-mode advert."], "bad", size=12)
    s.arrow([(940, 885), (978, 885), (978, 471), (815, 471)], C["bad"][0], dash=True)
    s.label(978, 650, ["next", "advert"], size=10.5, color=C["bad"][0])
    s.label(470, 880, ["Android TV voice remote:", "every reconnect ends here →", "use pairing mode per session"],
            size=11, color=C["bad"][0], bg=False)
    s.save("pairing-flow.svg")


def lifecycle():
    s = SVG(960, 1210, "BLE remote lifecycle — remote ⇄ test jig",
            "Arrows show who transmits. The jig (xiao-test-tool) plays the role the TV / STB normally plays.")
    RX, JX = 260, 700           # lifelines
    s.box(150, 80, 220, 58, ["**Remote**", "BLE peripheral"], "remote", size=13)
    s.box(590, 80, 220, 58, ["**Test jig (XIAO)**", "BLE central · stand-in host"], "jig", size=13)
    s.raw(f'<line x1="{RX}" y1="138" x2="{RX}" y2="1120" stroke="#cbd5e1" stroke-width="2" stroke-dasharray="4 5"/>')
    s.raw(f'<line x1="{JX}" y1="138" x2="{JX}" y2="1120" stroke="#cbd5e1" stroke-width="2" stroke-dasharray="4 5"/>')

    def phase(y, h, title, note, role):
        stroke, fill, tc = C[role]
        s.raw(f'<rect x="40" y="{y}" width="880" height="{h}" rx="10" fill="{fill}" fill-opacity="0.55" stroke="{stroke}" stroke-opacity="0.6"/>')
        s.text(56, y + 18, f"**{title}**", size=13.5, color=tc, anchor="start")
        if note:
            s.text(56, y + 37, note, size=11.5, color=tc, anchor="start")

    def msg(y, txt, frm, both=False, color=INK):
        a, b = (RX, JX) if frm == "r" else (JX, RX)
        s.arrow([(a, y), (b - (8 if b > a else -8), y)], color, both=both)
        s.label((RX + JX) / 2, y - 11, txt, size=12, color=color)

    R, J, B = C["remote"][0], C["jig"][0], "#334155"
    phase(160, 210, "1 · First pairing", "Remote in pairing mode, STB off. One time per remote (per session for the Android TV voice remote).", "remote")
    msg(225, "ADV_IND — connectable, LE-discoverable flags (PAIRING)", "r", color=R)
    msg(262, "CONNECT_IND", "j", color=J)
    msg(299, "SMP pairing — Just Works (both tested remotes: legacy pairing)", "j", both=True, color=B)
    msg(336, "key distribution — LTK, IRK → bond stored on both sides", "j", both=True, color=B)

    phase(385, 175, "2 · Link set-up", "Done by the jig on every connection.", "jig")
    msg(442, "GATT discovery + reads (HID report map, Device Information, battery)", "j", color=J)
    msg(479, "write CCCDs = subscribe (keys, battery, voice) · ATV: GET_CAPS", "j", color=J)
    msg(516, "ATV voice caps reply → link is LIVE", "r", color=R)

    phase(575, 95, "3 · Idle", "Remote sleeps between events: interval × (slave latency + 1), e.g. 20 ms × 50 ≈ 1 s.", "host")
    msg(642, "empty link-layer packets keep the connection alive", "j", both=True, color=B)

    phase(685, 95, "4 · Key press", "", "ok")
    msg(745, "HID input report (notification) → Keys tab", "r", color=C["ok"][0])

    phase(795, 160, "5 · Voice (Android TV Voice v1.0, hold-to-talk)", "Mic button held. Audio stops on release or at the 15 s cap.", "ok")
    msg(858, "voice control: AUDIO_START", "r", color=C["ok"][0])
    msg(895, "IMA-ADPCM 16 kHz audio · 20-byte notifications, ~400 /s", "r", color=C["ok"][0])
    msg(932, "voice control: AUDIO_STOP", "r", color=C["ok"][0])

    phase(970, 150, "6 · Reconnect after a drop or a jig reboot", "No pairing: encryption with the stored keys.", "q")
    msg(1032, "ADV_DIRECT_IND — directed at the jig's private address", "r", color=R)
    msg(1069, "CONNECT_IND + LL encryption with the stored LTK", "j", color=J)
    s.text(480, 1104, "Fire TV remote: keys continue.  Android TV voice remote: link stays silent → pairing mode again (Ch. 7).",
           size=11.5, color=C["q"][2])

    s.box(40, 1135, 880, 58, ["Battery: read once after encryption, then the remote notifies on change (Battery Level 0x2A19).",
                              "The Nordic sniffer can only watch such a link passively — once bonded it is encrypted (Ch. 9)."],
          "note", size=12)
    s.save("remote_lifecycle_sequence.svg")


def ir_wiring():
    s = SVG(980, 600, "IR wiring — XIAO nRF52840 + M5Stack Unit IR (U002)",
            "One 4-wire HY2.0 cable. TX and RX in one unit (38 kHz, 940 nm). Firmware makes the 38 kHz on D1 and decodes on D0.")
    s.box(60, 120, 220, 220, ["**XIAO**", "nRF52840"], "host", size=15)
    s.box(660, 120, 260, 220, ["**M5Stack Unit IR**", "U002 — TX + RX", "38 kHz / 940 nm"], "host", size=14)
    left = [("3V3", 160), ("GND", 205), ("D0", 250), ("D1", 295)]
    right = [("GND", 160), ("5V", 205), ("IR_TX", 250), ("IR_RX", 295)]
    for name, y in left:
        s.raw(f'<rect x="272" y="{y - 8}" width="16" height="16" fill="#fff" stroke="{INK}" stroke-width="1.4"/>')
        s.text(262, y, name, size=12.5, anchor="end", weight=600)
    for name, y in right:
        s.raw(f'<rect x="652" y="{y - 8}" width="16" height="16" fill="#fff" stroke="{INK}" stroke-width="1.4"/>')
        s.text(678, y, name, size=12.5, anchor="start", weight=600)
    # Each pair of wires swaps rows; one wire of each pair jogs through a middle lane so no two
    # wires ever share a segment (they only cross).
    wires = [  # (path, colour, legend)
        ("M288,160 L450,160 L450,205 L652,205", "#dc2626", "Red: 5V pin → XIAO 3V3"),
        ("M288,205 L400,205 L400,182 L500,182 L500,160 L652,160", "#111827", "Black: GND → GND"),
        ("M288,250 L450,250 L450,295 L652,295", "#9ca3af", "White: IR_RX → D0"),
        ("M288,295 L400,295 L400,272 L500,272 L500,250 L652,250", "#d97706", "Yellow: IR_TX → D1"),
    ]
    for d, col, _ in wires:
        s.raw(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="4" stroke-linejoin="round"/>')
    s.raw('<path d="M920,150 l50,-30" stroke="#ea580c" stroke-width="2.5" marker-end="url(#mea580c)"/>')
    s.marker("#ea580c")
    s.text(955, 108, "IR", size=13, color="#ea580c", weight=700)
    # legend
    for i, (_, col, lab) in enumerate(wires):
        y = 370 + i * 24
        s.raw(f'<rect x="300" y="{y - 6}" width="26" height="12" rx="2" fill="{col}" stroke="#475569" stroke-width="0.8"/>')
        s.text(336, y, lab, size=12.5, anchor="start")
    s.box(40, 470, 440, 112, ["**Power the unit from 3V3, not 5V**", "The nRF52840 is not 5V-tolerant and IR_RX follows the",
                              "supply. 3V3 is also the only rail on battery.", "Max range (USB only): 5V on red + a divider on IR_RX",
                              "(10 kΩ series, 20 kΩ to GND)."], "bad", size=11.5, align="start")
    s.box(500, 470, 440, 112, ["**Pins and mounting**", "D0 = P0.02 ← IR_RX (demodulated, active low)",
                               "D1 = P0.03 → IR_TX (38 kHz PWM carrier, ~33 % duty)", "No extra parts: the unit drives its own LED. Solder the",
                               "4 wires to the pads; the unit mounts outside the case."], "note", size=11.5, align="start")
    s.save("ir_connection.svg")


def workflow():
    s = SVG(1000, 330, "A test session at a glance", "Typical bench routine with the xiao-test-tool firmware (Ch. 5 Quick start).")
    steps = [
        ("1", "Plug in the XIAO", "USB-C"), ("2", "Start the bridge", "backend/bridge.py"),
        ("3", "Open the app", "PC or phone"), ("4", "Check the banner", "jig online?"),
        ("5", "Pair the remote", "STB off · pairing mode"), ("6", "Explore", "Keys · Voice · GATT · IR"),
        ("7", "Test ▸ Run all", "follow the prompts"), ("8", "Export report", "JSON + printable HTML"),
    ]
    for i, (n, a, b) in enumerate(steps):
        row, col = divmod(i, 4)
        x, y = 40 + col * 240, 92 + row * 120
        role = "jig" if i < 4 else "ok"
        s.box(x, y, 200, 84, [f"**{n} · {a}**", b], role, size=13)
        if col < 3:
            s.arrow([(x + 200, y + 42), (x + 240, y + 42)])
    s.arrow([(860, 176), (860, 194), (140, 194), (140, 212)], MUTED)
    s.save("workflow.svg")


if __name__ == "__main__":
    architecture()
    firmware_decision()
    flashing_flow()
    pairing_flow()
    lifecycle()
    ir_wiring()
    workflow()
