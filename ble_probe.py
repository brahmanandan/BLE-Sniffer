#!/usr/bin/env python3
"""
ble_probe.py — probe the BLE link between an set-top box and its remote.

The XIAO nRF52840 (flashed with Nordic's nRF Sniffer for Bluetooth LE firmware)
acts as the radio. This host-side program drives it over USB-serial via Nordic's
SnifferAPI: it scans, lets you lock onto the remote, follows the connection as it
hops, and prints every captured packet decoded on the terminal.

Usage:
    python ble_probe.py                       # scan, pick a device interactively
    python ble_probe.py --name RCU            # auto-follow first device whose name contains "RCU"
    python ble_probe.py --address D4:3A:...   # auto-follow by MAC
    python ble_probe.py --port /dev/ttyACM0   # skip auto-detect
    python ble_probe.py --raw                 # don't decode, just hex-dump

Ctrl-C to stop. See README.md for firmware flashing.
"""

import argparse
import os
import sys
import time

# ---------------------------------------------------------------------------
# SnifferAPI lives in the `extcap/` folder of Nordic's sniffer download.
# Either run this script from there, copy the SnifferAPI/ folder next to it,
# or add it to PYTHONPATH. pyserial must be installed. This repo vendors it in
# extcap/, which is tried automatically.
# ---------------------------------------------------------------------------
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "extcap"))
try:
    from SnifferAPI import Sniffer, UART
except ImportError:
    sys.exit(
        "Cannot import SnifferAPI.\n"
        "  - Download 'nRF Sniffer for Bluetooth LE' from Nordic.\n"
        "  - Copy the extcap/SnifferAPI/ folder next to this script, or add\n"
        "    the extcap/ folder to PYTHONPATH.\n"
        "  - pip install pyserial\n"
    )


# ---------------------------------------------------------------------------
# Decoding lives in backend/decode.py (shared with the web bridge).
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.decode import (  # noqa: E402  (re-exported for existing callers)
    ADV_PDU, AD_TYPE, LL_CONTROL, ATT_OPCODE, hexs, fmt_addr, fmt_dev_addr,
    decode_adv, decode_ad_structs, decode_data, format_packet,
)


def print_packet(pkt, t0, raw=False):
    print(format_packet(pkt, t0, raw=raw))


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------
def scan_and_list(sniffer, scan_time):
    sniffer.scan()
    print("Scanning %ds ..." % scan_time)
    time.sleep(scan_time)
    devs = sniffer.getDevices().asList()
    devs.sort(key=lambda d: getattr(d, "RSSI", 0))
    return devs


def pick_device(sniffer, args):
    while True:
        devs = scan_and_list(sniffer, args.scan_time)

        if args.address:
            want = args.address.upper().replace("-", ":")
            for d in devs:
                if fmt_dev_addr(d.address) == want:
                    return d
            print("  %s not seen yet (press a button on the remote), rescanning..." % want)
            continue

        if args.name:
            for d in devs:
                if args.name.lower() in (d.name or "").lower():
                    return d
            print('  no device matching "%s" yet, rescanning...' % args.name)
            continue

        if not devs:
            print("  nothing found, rescanning...")
            continue

        print("\n  #  RSSI  Address            Name")
        for i, d in enumerate(devs):
            r = getattr(d, "RSSI", 0)
            print("  %2d  %4d  %-17s  %s" % (i, -r, fmt_dev_addr(d.address), d.name or ""))
        sel = input("\nSelect # to follow (Enter to rescan): ").strip()
        if sel.isdigit() and int(sel) < len(devs):
            return devs[int(sel)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="BLE probe for STB <-> remote using XIAO nRF52840 sniffer")
    ap.add_argument("--port", help="serial port of the sniffer (auto-detect if omitted)")
    ap.add_argument("--name", help="follow first device whose advertised name contains this")
    ap.add_argument("--address", help="follow this MAC (e.g. D4:3A:2C:11:22:33)")
    ap.add_argument("--scan-time", type=int, default=6, help="seconds per scan pass (default 6)")
    ap.add_argument("--raw", action="store_true", help="hex-dump payloads instead of decoding")
    args = ap.parse_args()

    port = args.port
    if not port:
        found = UART.find_sniffer()
        if not found:
            sys.exit("No sniffer found. Check the XIAO is plugged in and flashed with the nRF Sniffer firmware.")
        port = found[0]
        print("Using sniffer on %s" % port)

    sniffer = Sniffer.Sniffer(portnum=port)
    sniffer.start()
    time.sleep(1)

    try:
        dev = pick_device(sniffer, args)
        print("\nFollowing %s (%s). Press remote buttons now. Ctrl-C to stop.\n"
              % (dev.name or "<no name>", fmt_dev_addr(dev.address)))
        sniffer.follow(dev, False)

        t0 = time.time()
        n = 0
        while True:
            for pkt in sniffer.getPackets():
                print_packet(pkt, t0, raw=args.raw)
                n += 1
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n\nStopped. Captured %d packets." % n)
        print("packetsInLastConnection: %s" % getattr(sniffer, "packetsInLastConnection", "?"))
    finally:
        try:
            sniffer.doExit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
