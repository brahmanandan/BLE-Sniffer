"""
flash.py — switch the XIAO between the Nordic sniffer and xiao-test-tool images.

    python backend/flash.py tool       # flash firmware/xiao_test_tool/build/xiao_test_tool_full.uf2
    python backend/flash.py sniffer    # flash sniffer_nrf52840dongle_4.1.0.uf2
    python backend/flash.py <file.uf2>

How it gets into the UF2 bootloader (XIAO-BOOT drive):
  * xiao-test-tool running  -> sends {"cmd":"dfu"}; the firmware reboots into the bootloader.
  * Nordic sniffer running  -> it has no DFU hook: double-tap RST on the XIAO.
Then the UF2 is copied onto the drive and the board reboots into the new image.

The tool image is the "_full" UF2 (S140 SoftDevice + app) because the sniffer
image overwrites part of the SoftDevice region.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import tool_proto  # noqa: E402

log = logging.getLogger("flash")

IMAGES = {
    "tool": ROOT / "firmware/xiao_test_tool/build/xiao_test_tool_full.uf2",
    "sniffer": ROOT / "sniffer_nrf52840dongle_4.1.0.uf2",
}
LABEL = "XIAO-BOOT"
LABEL_GLOB = "XIAO*"   # XIAO-BOOT, or XIAO-SENSE on boards shipped with the Sense bootloader


def find_drive() -> Path | None:
    """Mounted XIAO-BOOT volume, mounting it via udisks if it is only attached."""
    found = find_drive_mounted_only()
    if found:
        return found
    devs = glob.glob("/dev/disk/by-label/%s" % LABEL_GLOB)
    dev = devs[0] if devs else ""
    if dev and shutil.which("udisksctl"):
        r = subprocess.run(["udisksctl", "mount", "-b", dev], capture_output=True, text=True)
        log.info("udisksctl: %s", (r.stdout or r.stderr).strip())
        for line in (r.stdout or "").split():
            if line.startswith("/") and os.path.isfile(os.path.join(line.rstrip("."), "INFO_UF2.TXT")):
                return Path(line.rstrip("."))
        return find_drive_mounted_only()
    return None


def find_drive_mounted_only() -> Path | None:
    for cand in (glob.glob("/media/*/%s" % LABEL_GLOB) + glob.glob("/run/media/*/%s" % LABEL_GLOB)
                 + glob.glob("/Volumes/%s" % LABEL_GLOB)):
        if os.path.isfile(os.path.join(cand, "INFO_UF2.TXT")):
            return Path(cand)
    return None


def request_dfu(port: str | None) -> bool:
    """Ask a running xiao-test-tool to reboot into the bootloader."""
    from serial.tools import list_ports
    ports = [port] if port else [p.device for p in list_ports.comports()]
    for p in ports:
        hello = tool_proto.probe(p, timeout=1.0)
        if hello and hello.get("fw") == tool_proto.FW_NAME:
            import serial
            with serial.Serial(p, 1_000_000, timeout=0.2) as s:
                s.write(tool_proto.encode({"cmd": "dfu"}))
            log.info("requested DFU on %s", p)
            return True
    return False


def flash(image: Path, port: str | None = None, wait: float = 60.0,
          status: Callable[[str], None] = print) -> Path:
    if not image.is_file():
        raise FileNotFoundError("%s not found (build the firmware first: firmware/build.sh)" % image)
    drive = find_drive()
    if drive is None:
        if request_dfu(port):
            status("xiao-test-tool rebooting into the bootloader…")
        else:
            status("Double-tap the RST button on the XIAO now (waiting for the %s drive)…" % LABEL)
        deadline = time.monotonic() + wait
        while drive is None and time.monotonic() < deadline:
            time.sleep(0.5)
            drive = find_drive()
        if drive is None:
            raise TimeoutError("%s drive did not appear" % LABEL)
    info = (drive / "INFO_UF2.TXT").read_text(errors="replace")
    if "nRF52840" not in info:
        raise RuntimeError("unexpected bootloader at %s:\n%s" % (drive, info))
    status("Copying %s (%d KB) to %s…" % (image.name, image.stat().st_size // 1024, drive))
    dst = drive / "NEW.UF2"
    try:
        with open(image, "rb") as src, open(dst, "wb") as out:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
    except OSError as e:
        # The bootloader resets as soon as the last block lands; the tail of the
        # write can fail with EIO even though flashing succeeded.
        log.info("copy ended with %s (normal when the board reboots)", e)
    status("Done — board is rebooting into %s." % image.name)
    return drive


async def flash_async(target: str, port: str | None, status: Callable[[str], None]) -> None:
    image = IMAGES.get(target, Path(target))
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: flash(image, port, status=lambda m: loop.call_soon_threadsafe(status, m)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="tool | sniffer | path/to/file.uf2")
    ap.add_argument("--port", help="serial port of a running xiao-test-tool (for automatic DFU)")
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the bootloader drive")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        flash(IMAGES.get(a.target, Path(a.target)), a.port, a.wait)
    except (FileNotFoundError, TimeoutError, RuntimeError) as e:
        print("error:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
