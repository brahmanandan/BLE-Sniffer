# 3 · Installation

[← Hardware](02-hardware.md) · [Manual index](README.md) · [Next: Firmware and flashing →](04-firmware.md)

Everything installs **inside the project folder** (`.venv/`, `.toolchain/`); nothing goes into your
home directory's Arduino settings.

## 3.1 Python environment (always needed)

```bash
cd BT-Sniffer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

| Package | Used by |
|---|---|
| `aiohttp` | the bridge: web server + WebSocket |
| `pyserial` | USB serial to the XIAO (also `ble_probe.py` and the Wireshark plugin) |
| `intelhex` | `firmware/make_uf2.py` — builds the full UF2 with the SoftDevice |
| `adafruit-nrfutil` | building the firmware with arduino-cli |

Nordic's **SnifferAPI** is not on PyPI; it is vendored in `extcap/SnifferAPI/` and found automatically.

Check:

```bash
.venv/bin/python backend/bridge.py --simulate
# open http://localhost:8000 — a simulated jig and remote, no hardware needed. Ctrl+C to stop.
```

## 3.2 Serial port permission (Linux, once)

```bash
sudo usermod -aG dialout $USER      # then log out and back in
```

Without it the bridge reports *Permission denied* on `/dev/ttyACM0`.

## 3.3 Firmware toolchain (only to rebuild the firmware)

A ready UF2 is produced in `firmware/xiao_test_tool/build/`. To build it yourself:

```bash
mkdir -p .toolchain/bin && cd .toolchain
curl -fsSL https://github.com/arduino/arduino-cli/releases/download/v1.1.1/arduino-cli_1.1.1_Linux_64bit.tar.gz \
  | tar xz -C bin arduino-cli
cat > arduino-cli.yaml <<EOF
board_manager:
  additional_urls: [https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json]
directories:
  data: $PWD/data
  downloads: $PWD/staging
  user: $PWD/user
EOF
bin/arduino-cli --config-file arduino-cli.yaml core update-index
bin/arduino-cli --config-file arduino-cli.yaml core install Seeeduino:nrf52      # Seeed nRF52 Boards 1.1.x
bin/arduino-cli --config-file arduino-cli.yaml lib install ArduinoJson           # 7.x
cd ..
firmware/build.sh
```

| Output | Use |
|---|---|
| `firmware/xiao_test_tool/build/xiao_test_tool_full.uf2` | **S140 SoftDevice + app — use this one** |
| `firmware/xiao_test_tool/build/xiao_test_tool.uf2` | app only (only if the board already runs xiao-test-tool) |

`build.sh` also regenerates `usage_names.h` from `backend/hid_usage.py`, the single source of key names.
In the Arduino IDE the board is *Seeed nRF52 Boards ▸ Seeed XIAO nRF52840* (not the "mbed-enabled" core).

## 3.4 Wireshark (only for passive capture)

See [Passive capture](09-passive-capture.md#install-the-plugin).

## 3.5 Phone access

- Same Wi-Fi as the PC, open `http://<PC-IP>:8000` — works in any phone browser.
- To **install it as an app** (full screen, offline shell) phones need HTTPS: start the bridge with
  `--https` and install the local CA once — see [Web app ▸ Phone](06-web-app.md#on-a-phone).
- Allow TCP 8000 (and 8443 for HTTPS) through the PC's firewall if the phone can't connect.

[← Hardware](02-hardware.md) · [Manual index](README.md) · [Next: Firmware and flashing →](04-firmware.md)
