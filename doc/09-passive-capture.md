# 9 · Passive capture (Nordic sniffer + Wireshark)

[← Testing and reports](08-testing-reports.md) · [Manual index](README.md) · [Next: Troubleshooting →](10-troubleshooting.md)

Use this to **watch a real remote ⇄ STB link** without pairing the remote to anything. It is an
occasional diagnostic and **does not use the web app**.

## When to use it — and its limit

Good for: connection set-up, reconnect behaviour, timing and connection parameters, PHY switches,
packet sizes, and — if you capture the pairing — the traffic itself with legacy pairing.

Not good for: reading key presses of an already-bonded remote. Once bonded, the link is **encrypted**.
With LE Secure Connections that can't be undone; with legacy pairing, Wireshark can decrypt only if the
capture contains the pairing. To read keys, use the [jig](05-quick-start.md).

## Flash the sniffer image

```bash
.venv/bin/python backend/flash.py sniffer        # automatic from xiao-test-tool (stop the bridge first)
```

or **Connect ▸ Log ▸ Firmware ▸ Flash Nordic sniffer (for Wireshark)** in the app, or by hand: double-tap
RST → drag `sniffer_nrf52840dongle_4.1.0.uf2` onto `XIAO-BOOT` / `XIAO-SENSE`. See [Ch. 4](04-firmware.md).

If the bridge keeps running, the app shows **sniffer image**; the bridge does not hold the port, so
Wireshark can use it.

## Install the plugin

Once per PC. Wireshark ▸ Help ▸ About ▸ Folders ▸ *Personal Extcap path* shows the folder.

```bash
mkdir -p ~/.local/lib/wireshark/extcap
cp -r extcap/nrf_sniffer_ble.py extcap/nrf_sniffer_ble.sh extcap/SnifferAPI ~/.local/lib/wireshark/extcap/
chmod +x ~/.local/lib/wireshark/extcap/nrf_sniffer_ble.sh ~/.local/lib/wireshark/extcap/nrf_sniffer_ble.py
pip install pyserial      # for the Python that Wireshark runs the plugin with
```

## Capture

1. Restart Wireshark → choose the interface **nRF Sniffer for Bluetooth LE**.
2. **View ▸ Interface Toolbars ▸ nRF Sniffer for Bluetooth LE**: pick the remote's address as the device
   to follow and enable following on all PHYs (the link switches to 2M PHY after connecting).
3. Choose the remote **before** it connects — e.g. select it, then power-cycle the STB or press a key.
4. To capture a pairing (for decryption with legacy pairing): start following, then pair the remote
   with its STB again.

Useful display filters: `btle.advertising_header.pdu_type == 0x05` (CONNECT_IND),
`btle.control_opcode` (link-layer control), `btatt` (GATT), `btsmp` (pairing).

When done, flash xiao-test-tool back ([Ch. 4](04-firmware.md)).

## Terminal alternative: `ble_probe.py`

```bash
.venv/bin/python ble_probe.py                                 # scan, choose a device, follow it
.venv/bin/python ble_probe.py --address 60:74:B1:80:AF:1E
.venv/bin/python ble_probe.py --name RCU --raw
```

Lighter than Wireshark but it does not follow PHY and channel-map updates, so it goes quiet after a
connection is set up. It decodes with `backend/decode.py` (three bugs of the original are fixed: packet
types read from the wrong bytes, byte-reversed addresses, swapped master/slave direction labels).

[← Testing and reports](08-testing-reports.md) · [Manual index](README.md) · [Next: Troubleshooting →](10-troubleshooting.md)
