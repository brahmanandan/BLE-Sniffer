#!/usr/bin/env python3
"""
make_uf2.py — package the xiao_test_tool app (+ optionally the S140 SoftDevice) as UF2.

Why the SoftDevice: the Nordic sniffer UF2 is a bare image linked at 0x1000, so
flashing it overwrites the start of S140 (0x1000-0x27000). An Adafruit-BSP app
needs S140 7.3.0 intact, so the UF2 we drag onto XIAO-BOOT after running the
sniffer must restore it. The XIAO UF2 bootloader (0.6.x) accepts writes there.

    python firmware/make_uf2.py build/xiao_test_tool.ino.hex out.uf2 --softdevice <bootloader_s140.hex>
"""
from __future__ import annotations

import argparse
import struct
import sys

from intelhex import IntelHex

FAMILY_NRF52840 = 0xADA52840
SD_START, SD_END = 0x1000, 0x27000      # S140 7.3.0 (MBR at 0x0 is left alone)
APP_LIMIT = 0xED000                     # app + user data must stay below the bootloader


def uf2_blocks(data: dict[int, bytes]) -> bytes:
    pages = sorted(data)
    out = []
    for n, addr in enumerate(pages):
        payload = data[addr].ljust(256, b"\xff")
        hdr = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, addr, 256, n, len(pages), FAMILY_NRF52840)
        out.append(hdr + payload + b"\x00" * (476 - 256) + struct.pack("<I", 0x0AB16F30))
    return b"".join(out)


def pages_of(ih: IntelHex, lo: int, hi: int) -> dict[int, bytes]:
    pages: dict[int, bytes] = {}
    for start, end in ih.segments():
        start, end = max(start, lo), min(end, hi)
        a = start & ~0xFF
        while a < end:
            pages[a] = ih.tobinstr(start=a, size=256) if hasattr(ih, "tobinstr") else ih.tobinarray(start=a, size=256).tobytes()
            a += 256
    return pages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app_hex")
    ap.add_argument("out_uf2")
    ap.add_argument("--softdevice", help="hex containing S140 (e.g. the Seeed bootloader+s140 hex)")
    a = ap.parse_args()

    app = IntelHex(a.app_hex)
    lo, hi = app.minaddr(), app.maxaddr() + 1
    if lo < SD_END or hi > APP_LIMIT:
        sys.exit("app hex 0x%X-0x%X is outside the application region" % (lo, hi))
    data = pages_of(app, SD_END, APP_LIMIT)
    if a.softdevice:
        sd = IntelHex(a.softdevice)
        sdp = pages_of(sd, SD_START, SD_END)
        if len(sdp) < 0x100:
            sys.exit("softdevice hex has no S140 image at 0x1000")
        data.update(sdp)
    blob = uf2_blocks(data)
    open(a.out_uf2, "wb").write(blob)
    print("%s: %d blocks, app 0x%X-0x%X%s" % (a.out_uf2, len(data), lo, hi, " + S140" if a.softdevice else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
