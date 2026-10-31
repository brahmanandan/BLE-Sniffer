# 11 · Reference

[← Troubleshooting](10-troubleshooting.md) · [Manual index](README.md)

## Bridge command line

```bash
.venv/bin/python backend/bridge.py [options]
```

| Option | Env var | Default | Purpose |
|---|---|---|---|
| `--port` | `BRIDGE_PORT` | auto-detect | serial port of the XIAO |
| `--http-port` | `BRIDGE_HTTP_PORT` | 8000 | web app + WebSocket |
| `--bind` | `BRIDGE_BIND` | 0.0.0.0 | listen address (0.0.0.0 = reachable from the LAN) |
| `--baud` | `BRIDGE_BAUD` | 1000000 | serial speed |
| `--https`, `--https-port` | `BRIDGE_HTTPS_PORT` | off, 8443 | HTTPS with a local CA (for installing on phones) |
| `--reports` | `BRIDGE_REPORTS` | `./reports` | where reports and voice captures go |
| `--simulate` | | off | simulated jig + remote, no hardware |
| `--session-log` | `BRIDGE_SESSION_LOG=1` | off | start the persistent session log at launch (`reports/logs/session_<date-time>.log`) |
| `--log-level` | | INFO | DEBUG shows serial traffic |

Other tools:

| Command | Purpose |
|---|---|
| `backend/flash.py tool \| sniffer \| file.uf2 [--port P] [--wait S]` | flash via the UF2 bootloader ([Ch. 4](04-firmware.md)) |
| `firmware/build.sh` | build the firmware → `firmware/xiao_test_tool/build/*.uf2` |
| `ble_probe.py [--address A] [--name N] [--raw]` | passive capture in a terminal (sniffer image) |
| `backend/hid_usage.py --emit-c` | regenerate `usage_names.h` (done by `build.sh`) |

## HTTP endpoints (bridge)

| Path | What |
|---|---|
| `/` | the web app |
| `/ws` | WebSocket: events out, `{"cmd":…}` in |
| `/api/status` | bridge status JSON |
| `/api/report` (POST) | save a report object → JSON + HTML |
| `/files/…` | `reports/` (reports, voice WAV/raw, `logs/` session logs) |
| `/ca.crt` | the local CA certificate (with `--https`) |

## Serial protocol (xiao-test-tool)

One JSON object per line, UTF-8, `\n`-terminated, 1 000 000 baud. The bridge forwards everything to
the app adding `"src":"tool"`; events it re-sends from its cache to a new page carry `"cached":true`.
Talk to the firmware directly (bridge stopped): `.venv/bin/pyserial-miniterm /dev/ttyACM0 1000000`.

### Commands

| Command | Purpose |
|---|---|
| `{"cmd":"ping"}` | reply `hello` |
| `{"cmd":"scan","sec":6}` / `{"cmd":"scan_stop"}` | scan (`sec: 0` = until stopped) |
| `{"cmd":"connect","addr":"…","atype":"public"}` | connect (`public` / `random`) |
| `{"cmd":"pair","addr":"…","atype":"public"}` | one step: connect → pair fresh → subscribe; retries a missed connection twice; locks the session to this remote |
| `{"cmd":"bond"}` | pair and bond (Just Works; passkey fallback on 0x83) |
| `{"cmd":"lock","addr":"…"}` / `{"cmd":"unlock"}` | auto-reconnect only to this remote / to any bonded remote (persisted) |
| `{"cmd":"bonds"}` | emit the bond list |
| `{"cmd":"bond_delete","addr":"…"}` | forget one remote |
| `{"cmd":"disconnect"}` | drop the link |
| `{"cmd":"subscribe","what":"all"}` | notifications: `hid` / `voice` / `battery` / `all` |
| `{"cmd":"battery"}` | jig battery + the remote's Battery Level |
| `{"cmd":"state"}` | emit `state` |
| `{"cmd":"info"}` | re-send `gatt`, `hid`, `state` for the current link |
| `{"cmd":"gatt_read","handle":"0x0016"}` | read a characteristic |
| `{"cmd":"gatt_write","handle":"…","bytes":"0c0001","rsp":true}` | raw write |
| `{"cmd":"mic","on":true}` | ATV MIC_OPEN / MIC_CLOSE |
| `{"cmd":"repair"}` | pair again on the live link |
| `{"cmd":"clear_bonds"}` | forget all remotes |
| `{"cmd":"ir_rx","on":true}` | IR receiver on/off |
| `{"cmd":"ir_tx","proto":"NEC","addr":"0x04","code":"0x08","repeats":0}` | send NEC / SIRC |
| `{"cmd":"ir_tx_raw","carrier":38000,"timings":[9000,4500,…]}` | send raw mark/space µs (≤ 200) |
| `{"cmd":"mode","value":"both"}` | `ble` / `ir` / `both` |
| `{"cmd":"dfu"}` | reboot into the UF2 bootloader |

### Events

| Event | Content |
|---|---|
| `hello` | `fw`, `ver`, `roles`, jig `addr`, `bonds`, `mode` |
| `scan` | `addr`, `atype`, `rssi`, `name`, `hid`, `appearance`, `bonded`, `directed` (+ `target`, `to_jig`), `connectable`, `adv` (advertising data hex — carries the Flags), `rsp` (scan response hex) |
| `scan_done` | `found` |
| `state` | `conn`, `stage`, `addr`, `bonded`, `secured`, `mtu`, `phy`, `rssi`, `interval_ms`, `services`, `ntf`, `live`, `auto_reconnect`, `lock`, `bonds` |
| `link` | `what`: `conn_params` (`interval_ms`, `latency`, `timeout_ms`) · `phy` (`tx`, `rx`, `status`) · `data_length` (`tx_octets`, `rx_octets`) · `mtu` (`mtu`, `remote_mtu`) |
| `bonds` | `list[]`: `addr` (identity), `atype`, `rpa`, `locked`, `connected` |
| `passkey` | `code` — 6 digits to type on the remote (passkey pairing) |
| `gatt` | services → characteristics: `uuid`, `handle`, `props`, `role`, `report_id`, `report_type` |
| `hid` | `map` (hex), `map_len`, `fields`, `reports` |
| `gatt_read` | `handle`, `ok`, `bytes` |
| `key` | `handle`, `report`, `usage`, `usage_id`, `page`, `rid`, `down`, `t` |
| `battery` | jig: `mv`, `pct`, `low`, `cut` · remote: `pct`, `via` (`read` / `notify`) |
| `voice` | `seq`, `codec`, `bytes` (hex) — one notification |
| `voice_ctl`, `voice_state` | ATV control (`op`: caps / start / stop / search / mic_error) · stream active |
| `notify` | notification from a characteristic with no known role |
| `hid_data` | a HID input report with **no key usages** (vendor data, e.g. motion): `handle`, `rid`, `bytes`. The bridge decodes LG Magic Remote keys from it into `key` events (`page: "lg"`, `decoder: "lg_magic"`) |
| `ir` | `proto`, `addr`, `code`, `repeat`, `raw` |
| `ir_tx_done` | `ok` (`err` when refused, e.g. battery low) |
| `log` | `level`, `msg` |

Bridge-only: `{"ev":"bridge","connected":…,"fw":…,"port":…,"reason":"nordic-sniffer-image","hint":…}` and
`{"ev":"session_log","on":…,"name":…,"url":…,"lines":…}`. Bridge-only command (not sent to the
firmware): `{"cmd":"session_log","on":true|false}` — start / stop the persistent log;
`{"cmd":"note","text":"…"}` — write a marker line into the session log (the test runner uses it).

## Reason and error codes

Firmware messages carry numeric codes. The bridge looks each one up (`backend/reason_codes.py`) and
shows its meaning right after the code, everywhere: Connect ▸ Log, toasts, the session-log file and
downloaded logs. For example:

```
disconnected (reason 0x08 = Connection Timeout: link lost — remote out of range, powered off, reset or batteries low)
```

The event also carries a `codes` list (`kind`, `code`, `name`, `hint`) for scripts. Look a code up
from a terminal with `.venv/bin/python -m backend.reason_codes hci 0x3e` (kinds: `hci`, `smp`, `gatt`, `nrf`).

| Message wording | Code family |
|---|---|
| `disconnected (reason 0x..)` | HCI error / disconnect reason |
| `pairing failed: 0x..` | SMP pairing failure |
| `… (status 0x....)` | GATT/ATT status of a read or write |
| `… err 0x…`, `… failed: 0x…` | nRF SoftDevice API error |

### HCI error / disconnect reason (`hci`)

| Code | Meaning | What it usually means on the jig |
|---|---|---|
| `0x00` | Success |  |
| `0x01` | Unknown HCI Command |  |
| `0x02` | Unknown Connection Identifier | the link was already gone |
| `0x03` | Hardware Failure |  |
| `0x04` | Page Timeout |  |
| `0x05` | Authentication Failure | pairing / encryption was rejected |
| `0x06` | PIN or Key Missing | the remote no longer has the bond — pair it again (pairing mode) |
| `0x07` | Memory Capacity Exceeded |  |
| `0x08` | Connection Timeout | link lost — remote out of range, powered off, reset or batteries low |
| `0x09` | Connection Limit Exceeded |  |
| `0x0A` | Synchronous Connection Limit Exceeded |  |
| `0x0B` | Connection Already Exists |  |
| `0x0C` | Command Disallowed |  |
| `0x0D` | Connection Rejected: Limited Resources |  |
| `0x0E` | Connection Rejected: Security Reasons |  |
| `0x0F` | Connection Rejected: Unacceptable Address |  |
| `0x10` | Connection Accept Timeout Exceeded |  |
| `0x11` | Unsupported Feature or Parameter Value |  |
| `0x12` | Invalid HCI Command Parameters |  |
| `0x13` | Remote User Terminated Connection | the remote closed the link — idle power-save, pairing mode, or it rejected the jig |
| `0x14` | Remote Terminated: Low Resources |  |
| `0x15` | Remote Terminated: Power Off | the remote is switching off |
| `0x16` | Connection Terminated by Local Host | the jig closed the link (Disconnect, or a silent link was dropped) |
| `0x17` | Repeated Attempts |  |
| `0x18` | Pairing Not Allowed | the remote refuses to pair — put it in pairing mode |
| `0x19` | Unknown LMP PDU |  |
| `0x1A` | Unsupported Remote Feature |  |
| `0x1B` | SCO Offset Rejected |  |
| `0x1C` | SCO Interval Rejected |  |
| `0x1D` | SCO Air Mode Rejected |  |
| `0x1E` | Invalid LL Parameters |  |
| `0x1F` | Unspecified Error |  |
| `0x20` | Unsupported LL Parameter Value |  |
| `0x21` | Role Change Not Allowed |  |
| `0x22` | LL Response Timeout | the remote stopped answering a link-layer procedure |
| `0x23` | LL Procedure Collision |  |
| `0x24` | LMP PDU Not Allowed |  |
| `0x25` | Encryption Mode Not Acceptable |  |
| `0x26` | Link Key Cannot Be Changed |  |
| `0x27` | Requested QoS Not Supported |  |
| `0x28` | Instant Passed | a connection update came too late — usually radio interference |
| `0x29` | Pairing With Unit Key Not Supported |  |
| `0x2A` | Different Transaction Collision |  |
| `0x2C` | QoS Unacceptable Parameter |  |
| `0x2D` | QoS Rejected |  |
| `0x2E` | Channel Classification Not Supported |  |
| `0x2F` | Insufficient Security |  |
| `0x30` | Parameter Out of Mandatory Range |  |
| `0x32` | Role Switch Pending |  |
| `0x34` | Reserved Slot Violation |  |
| `0x35` | Role Switch Failed |  |
| `0x36` | Extended Inquiry Response Too Large |  |
| `0x37` | Secure Simple Pairing Not Supported by Host |  |
| `0x38` | Host Busy - Pairing |  |
| `0x39` | Connection Rejected: No Suitable Channel |  |
| `0x3A` | Controller Busy |  |
| `0x3B` | Unacceptable Connection Parameters |  |
| `0x3C` | Advertising Timeout |  |
| `0x3D` | Connection Terminated: MIC Failure | encryption keys don't match — the bond is stale; pair again |
| `0x3E` | Connection Failed to Be Established | the remote never answered — not in pairing mode, or it only accepts its own host (NOT PAIRING) |
| `0x3F` | MAC Connection Failed |  |
| `0x40` | Coarse Clock Adjustment Rejected |  |
| `0x41` | Type0 Submap Not Defined |  |
| `0x42` | Unknown Advertising Identifier |  |
| `0x43` | Limit Reached |  |
| `0x44` | Operation Cancelled by Host |  |
| `0x45` | Packet Too Long |  |

### SMP pairing failure (`smp`)

| Code | Meaning | What it usually means on the jig |
|---|---|---|
| `0x00` | Success |  |
| `0x01` | Timeout | pairing took more than 30 s — the remote stopped responding |
| `0x02` | Invalid PDU |  |
| `0x81` | Passkey Entry Failed |  |
| `0x82` | OOB Not Available |  |
| `0x83` | Authentication Requirements Not Met | the remote wants a stronger pairing method than Just Works |
| `0x84` | Confirm Value Failed |  |
| `0x85` | Pairing Not Supported | the remote refuses to pair now — put it in pairing mode |
| `0x86` | Encryption Key Size Too Short |  |
| `0x87` | SMP Command Not Supported |  |
| `0x88` | Unspecified Reason | often: the remote is not in pairing mode |
| `0x89` | Repeated Attempts | too many tries — wait a few seconds |
| `0x8A` | Invalid Parameters |  |
| `0x8B` | DHKey Check Failed |  |
| `0x8C` | Numeric Comparison Failed |  |
| `0x8D` | BR/EDR Pairing in Progress |  |
| `0x8E` | Cross-transport Key Derivation Not Allowed |  |

### GATT/ATT status (`gatt`)

| Code | Meaning | What it usually means on the jig |
|---|---|---|
| `0x0000` | Success |  |
| `0x0001` | Unknown or not applicable |  |
| `0x0100` | ATT: Invalid Error Code |  |
| `0x0101` | ATT: Invalid Handle |  |
| `0x0102` | ATT: Read Not Permitted |  |
| `0x0103` | ATT: Write Not Permitted |  |
| `0x0104` | ATT: Invalid PDU |  |
| `0x0105` | ATT: Insufficient Authentication | needs a bonded link — press Bond first |
| `0x0106` | ATT: Request Not Supported |  |
| `0x0107` | ATT: Invalid Offset |  |
| `0x0108` | ATT: Insufficient Authorization |  |
| `0x0109` | ATT: Prepare Queue Full |  |
| `0x010A` | ATT: Attribute Not Found |  |
| `0x010B` | ATT: Attribute Not Long |  |
| `0x010C` | ATT: Insufficient Encryption Key Size |  |
| `0x010D` | ATT: Invalid Attribute Value Length |  |
| `0x010E` | ATT: Unlikely Error |  |
| `0x010F` | ATT: Insufficient Encryption | the link is not encrypted yet — press Bond first |
| `0x0110` | ATT: Unsupported Group Type |  |
| `0x0111` | ATT: Insufficient Resources |  |
| `0x0112` | ATT: Database Out of Sync |  |
| `0x0113` | ATT: Value Not Allowed |  |
| `0x01FC` | ATT: Write Request Rejected |  |
| `0x01FD` | ATT: CCCD Improperly Configured |  |
| `0x01FE` | ATT: Procedure Already in Progress |  |
| `0x01FF` | ATT: Out of Range |  |
| `0xFFFF` | No response | the link dropped or the remote didn't answer before the operation finished (jig) |

### nRF SoftDevice error (`nrf`)

| Code | Meaning | What it usually means on the jig |
|---|---|---|
| `0x0000` | NRF_SUCCESS |  |
| `0x0001` | NRF_ERROR_SVC_HANDLER_MISSING |  |
| `0x0002` | NRF_ERROR_SOFTDEVICE_NOT_ENABLED |  |
| `0x0003` | NRF_ERROR_INTERNAL |  |
| `0x0004` | NRF_ERROR_NO_MEM |  |
| `0x0005` | NRF_ERROR_NOT_FOUND |  |
| `0x0006` | NRF_ERROR_NOT_SUPPORTED |  |
| `0x0007` | NRF_ERROR_INVALID_PARAM |  |
| `0x0008` | NRF_ERROR_INVALID_STATE | not possible right now (e.g. already connecting, or not connected) |
| `0x0009` | NRF_ERROR_INVALID_LENGTH |  |
| `0x000A` | NRF_ERROR_INVALID_FLAGS |  |
| `0x000B` | NRF_ERROR_INVALID_DATA |  |
| `0x000C` | NRF_ERROR_DATA_SIZE |  |
| `0x000D` | NRF_ERROR_TIMEOUT |  |
| `0x000E` | NRF_ERROR_NULL |  |
| `0x000F` | NRF_ERROR_FORBIDDEN |  |
| `0x0010` | NRF_ERROR_INVALID_ADDR |  |
| `0x0011` | NRF_ERROR_BUSY | the radio stack is busy with another operation — retry |
| `0x0012` | NRF_ERROR_CONN_COUNT |  |
| `0x0013` | NRF_ERROR_RESOURCES |  |
| `0x3001` | BLE_ERROR_NOT_ENABLED |  |
| `0x3002` | BLE_ERROR_INVALID_CONN_HANDLE | the link was already gone when the jig tried to use it |
| `0x3003` | BLE_ERROR_INVALID_ATTR_HANDLE |  |
| `0x3004` | BLE_ERROR_INVALID_ADV_HANDLE |  |
| `0x3005` | BLE_ERROR_INVALID_ROLE |  |
| `0x3006` | BLE_ERROR_BLOCKED_BY_OTHER_LINKS |  |
| `0x3100` | BLE_ERROR_L2CAP_CID_IN_USE |  |
| `0x3200` | BLE_ERROR_GAP_UUID_LIST_MISMATCH |  |
| `0x3201` | BLE_ERROR_GAP_DISCOVERABLE_WITH_WHITELIST |  |
| `0x3202` | BLE_ERROR_GAP_INVALID_BLE_ADDR |  |
| `0x3203` | BLE_ERROR_GAP_WHITELIST_IN_USE |  |
| `0x3204` | BLE_ERROR_GAP_DEVICE_IDENTITIES_IN_USE |  |
| `0x3205` | BLE_ERROR_GAP_DEVICE_IDENTITIES_DUPLICATE |  |
| `0x3300` | BLE_ERROR_GATTC_PROC_NOT_PERMITTED | another GATT operation is still running |
| `0x3400` | BLE_ERROR_GATTS_INVALID_ATTR_TYPE |  |
| `0x3401` | BLE_ERROR_GATTS_SYS_ATTR_MISSING |  |

## Pins

| Function | XIAO | nRF52840 |
|---|---|---|
| IR receive (Unit IR white) | D0 | P0.02 |
| IR transmit, 38 kHz (Unit IR yellow) | D1 | P0.03 |
| Battery sense | — | P0.31 via 1 MΩ / 510 kΩ; P0.14 held LOW |
| Blue LED | — | P0.06 (on while a remote is connected) |

## Files

| Path | Purpose |
|---|---|
| `backend/bridge.py` | entry point: serial ⇄ WebSocket bridge and web server |
| `backend/tool_proto.py` | firmware protocol: encode/parse, serial link, probe |
| `backend/flash.py` | firmware switching via the UF2 bootloader |
| `backend/sim_device.py` | simulated firmware for `--simulate` |
| `backend/hid_usage.py` | HID usage → key names (source of the firmware's `usage_names.h`) |
| `backend/adpcm.py` | IMA-ADPCM → WAV |
| `backend/report.py` | test report model, JSON + HTML |
| `backend/session_log.py` | persistent session log (`reports/logs/`) |
| `backend/lg_magic.py` | LG Magic Remote key decoder (IR command codes inside the motion stream) |
| `backend/reason_codes.py` | lookup tables for HCI / SMP / GATT / nRF codes in firmware messages |
| `backend/decode.py` | BLE packet decoders for `ble_probe.py` |
| `webapp/` | the web app; `js/gatt_info.js` = GATT names, decoders, report-map parser |
| `firmware/xiao_test_tool/` | firmware source (+ its README) |
| `firmware/build.sh`, `make_uf2.py` | build and UF2 packaging |
| `hardware/` | enclosure STLs, parametric `bt_case.scad`, IR wiring SVG |
| `extcap/` | Nordic Wireshark plugin incl. `SnifferAPI/` |
| `doc/` | this manual; `doc/src/` regenerates its figures and screenshots |
| `reports/` | exported reports and voice captures |

## Glossary

| Term | Meaning |
|---|---|
| BLE | Bluetooth Low Energy |
| Bond | stored pairing keys that let two devices reconnect encrypted without pairing again |
| Central / peripheral | the host (TV, jig) / the device (remote) |
| CCCD | descriptor the host writes to turn notifications on |
| DUT | device under test |
| GATT | the remote's table of services and characteristics |
| HID | Human Interface Device — keyboards, remotes; key presses are HID reports |
| IRK / RPA | identity key / resolvable private address derived from it |
| LESC | LE Secure Connections pairing (keys never cross the air) |
| PWA | progressive web app — the web app installed on a phone |
| STB | set-top box |
| UF2 | the drag-and-drop firmware file format of the XIAO bootloader |

[← Troubleshooting](10-troubleshooting.md) · [Manual index](README.md)
