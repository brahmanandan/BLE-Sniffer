# xiao-test-tool firmware

Custom firmware for the **Seeed XIAO nRF52840** that turns it into a bench jig for TV remotes:

- **BLE HID central** — scans, connects, **bonds** (Just Works, LESC) to the remote as a stand-in TV,
  discovers the full GATT database, reads the HID report map and Report Reference descriptors,
  subscribes to input reports and emits **decoded, plaintext key presses**. Detects links the remote
  keeps silent (reconnects with stored keys) and re-pairs fresh when the remote enters pairing mode.
- **Voice** — Android TV Voice service (`AB5E0001-…`): answers the mic button with `MIC_OPEN`
  and forwards the audio notifications raw (`voice` events); the host decodes IMA-ADPCM.
- **IR** — M5Stack Unit IR (U002): RX decode (NEC, SIRC, RC5 + raw timings), TX (NEC/SIRC/raw, 38 kHz).
- **Battery** — jig LiPo voltage/percent, with a soft low-battery limit: `"low":true` below 3.4 V,
  and below 3.3 V (`"cut":true`) IR TX is refused until the cell recovers. Readings under 2.5 V mean
  "no cell" and never cut. This is an early warning only; the cell's own PCM is the real protection
  (top-level README ▸ Battery safety).

Host protocol: one JSON object per line over USB CDC at 1 Mbaud; the full command and event list is in
the top-level README ▸ Reference ▸ Protocol. `python backend/bridge.py` talks to it.

## Pins

| Function | XIAO pin | nRF52840 | Notes |
|---|---|---|---|
| IR RX (Unit IR white) | D0 | P0.02 | demodulated, active-low; internal pull-up |
| IR TX (Unit IR yellow) | D1 | P0.03 | 38 kHz carrier from `HwPWM3`, ~33 % duty |
| Unit IR power (red) | 3V3 | — | **not 5 V** (IR_RX follows the supply) |
| VBAT sense | — | P0.31 (`PIN_VBAT`) | via on-board 1 MΩ / 510 kΩ divider (×2.961) |
| VBAT divider enable | — | P0.14 (`VBAT_ENABLE`) | **held LOW permanently** (see below) |
| Blue LED | — | P0.06 | on while a remote is connected |

**Why P0.14 is never released:** with P0.14 floating or high, P0.31 is pulled toward the raw battery
voltage, which is above the nRF52840's VDD-limited pin range. Seeed's guidance is to leave it LOW; it
costs ~2.8 µA. (The brief said "restore after sampling" and "1M/1M" — the schematic divider is
1M/510k and restoring is unsafe, so the firmware deviates deliberately.)

## Build

Toolchain (installed project-locally in `.toolchain/`, nothing in `~/.arduino15`):

```bash
# one-time
mkdir -p .toolchain/bin && cd .toolchain
curl -fsSL https://github.com/arduino/arduino-cli/releases/download/v1.1.1/arduino-cli_1.1.1_Linux_64bit.tar.gz | tar xz -C bin arduino-cli
# arduino-cli.yaml: board_manager.additional_urls = https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json
bin/arduino-cli --config-file arduino-cli.yaml core update-index
bin/arduino-cli --config-file arduino-cli.yaml core install Seeeduino:nrf52     # "Seeed nRF52 Boards" 1.1.x
bin/arduino-cli --config-file arduino-cli.yaml lib install ArduinoJson          # 7.x
cd .. && .venv/bin/pip install -r backend/requirements.txt                      # adafruit-nrfutil, intelhex
```

Board: **Seeed XIAO nRF52840** (`Seeeduino:nrf52:xiaonRF52840`), Adafruit Bluefruit stack, S140 7.3.0.
(In the Arduino IDE: *Seeed nRF52 Boards ▸ Seeed XIAO nRF52840* — not the "mbed-enabled" core.)

```bash
firmware/build.sh
#  -> firmware/xiao_test_tool/build/xiao_test_tool.uf2        app only
#  -> firmware/xiao_test_tool/build/xiao_test_tool_full.uf2   S140 SoftDevice + app  (use this one)
```

`build.sh` regenerates `usage_names.h` from `backend/hid_usage.py` (single source of key names).

## Flash

The **full** UF2 is required whenever the board last ran the Nordic sniffer: the sniffer image is
linked at 0x1000 and overwrites the first 68 KB of the SoftDevice, which `_full.uf2` restores.

```bash
python backend/flash.py tool       # from the sniffer: double-tap RST when asked
python backend/flash.py sniffer    # back to the Nordic sniffer (automatic: the tool firmware enters DFU on request)
```

Manually: **double-tap RST** → the `XIAO-BOOT` drive (named `XIAO-SENSE` on boards shipped with the Sense bootloader) appears → drag the `.uf2` onto it → the board
reboots. Flashing either image **replaces** the other (one image at a time). The web app drives only
this firmware. To go back to passive capture in Wireshark, re-flash `sniffer_nrf52840dongle_4.1.0.uf2`
(top-level README ▸ Passive capture). To have both roles live at once, use a second XIAO that stays on
the sniffer image.

## Protocol extras (beyond the brief)

| Command | Purpose |
|---|---|
| `{"cmd":"state"}` | emit current `state` |
| `{"cmd":"scan_stop"}` | stop scanning |
| `{"cmd":"mic","on":true}` | ATV voice `MIC_OPEN` / `MIC_CLOSE` |
| `{"cmd":"gatt_read","handle":"0x002A"}` | long read → `gatt_read` event |
| `{"cmd":"gatt_write","handle":"0x0052","bytes":"0c0001","rsp":true}` | raw write |
| `{"cmd":"clear_bonds"}` | forget bonded remotes |
| `{"cmd":"dfu"}` | reboot into the UF2 bootloader (used by `flash.py`) |
| `{"cmd":"info"}` | re-send `gatt`, `hid` and `state` of the current link (the bridge sends it on start-up) |
| `{"cmd":"repair"}` | run pairing again on the live link |
| `{"cmd":"pair","addr":"…","atype":"public"}` | connect → pair fresh → subscribe; retries a missed connection twice; locks the session |
| `{"cmd":"lock","addr":"…"}` / `{"cmd":"unlock"}` | auto-reconnect only to this remote / to any bonded remote (`/jig_lock.bin`) |
| `{"cmd":"bonds"}` / `{"cmd":"bond_delete","addr":"…"}` | list bonds (`bonds` event) / forget one remote |

| Event | Meaning |
|---|---|
| `state.stage` | `idle` → `connecting` → `connected` → `discovering` → `discovered` → `pairing` → `reading` → `ready` |
| `state.live` | `true` once the remote sent anything on this link; `false` if the voice handshake got no reply within 3 s (silent link, see Pairing the remote); absent until known |
| `state.ntf` | notifications received on the current link |
| `state.lock`, `state.bonds` | session lock (identity address) and bond count |
| `link` | `conn_params` / `phy` / `data_length` / `mtu` changes |
| `bonds` | bonded remotes (identity address, `rpa`, `locked`, `connected`) |
| `passkey` | passkey-pairing fallback (remote rejected Just Works with 0x83): `code` to type on the remote |
| `hid` | report map (hex), parsed field count, report handles/ids |
| `battery` + `"which":"jig"` / `"remote"` | jig VBAT (mv/pct/low/cut) or the remote's Battery Service level |
| `voice_ctl`, `voice_state` | ATV voice control notifications; stream start/stop |
| `notify` | notification from a characteristic with no known role |
| `scan_done`, `ir_tx_done`, `log` | as named (`ir_tx_done` has `"ok":false,"err":"battery low"` when TX is refused) |

## Pairing the remote

1. Put the remote into **pairing mode**: Android TV voice remotes usually hold **Back + Home** (or
   **OK + Vol−**) for ~5 s until the LED blinks; Fire TV remotes hold **Home** for ~10 s. It now
   advertises *undirected* and LE-discoverable (**PAIRING** badge in the scan list); a remote that only
   shows `DIRECT` or **NOT PAIRING** is still trying to reach its own host.
2. Connect tab → Scan → the remote's row → **Pair** (`pair` command): connect (two retries on 0x3E /
   timeout), delete any old bond **before** connecting (Bluefruit would otherwise re-encrypt with it at
   once), pair (Just Works; passkey display fallback on SMP 0x83), subscribe. The remote becomes the
   session's **locked** remote: auto-reconnect follows only it (`/jig_lock.bin`).
3. Keys appear in the Keys tab. Remotes differ on reconnect (top-level README §6.2): the Fire TV remote
   keeps working with the stored bond, while the Android TV voice remote only sends on a **freshly
   paired** link; links it makes
   by reconnecting with stored keys stay silent. The firmware detects that (no reply to the voice
   `GET_CAPS` within 3 s → `state.live:false`, a warning, link dropped), then ignores directed
   reconnects and **pairs fresh** on the next pairing-mode (undirected) advert, deleting the old bond
   first. So: pairing mode (STB off) at the start of each session. See the top-level README §6.2.
   The jig keeps its identity key (IRK) in flash (`/jig_irk.bin`). After this the remote is bonded
   **to the jig**; re-pair it with the STB afterwards if needed.
