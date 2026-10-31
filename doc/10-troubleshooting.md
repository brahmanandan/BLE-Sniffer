# 10 · Troubleshooting

[← Passive capture](09-passive-capture.md) · [Manual index](README.md) · [Next: Reference →](11-reference.md)

Messages with a code explain it themselves, e.g. `disconnected (reason 0x3E = Connection Failed to Be
Established: the remote never answered — …)`. Full tables: [Reference ▸ Reason and error codes](11-reference.md#reason-and-error-codes).

## Banner and connection

| Symptom | Cause and fix |
|---|---|
| Banner **offline** | The page can't reach the bridge. Is `backend/bridge.py` running? Is the page from the same host/port (default 8000)? On a phone: same Wi-Fi, PC firewall allows TCP 8000. |
| **no board** | Plug in the XIAO with a data cable; check `ls /dev/ttyACM*`. *Permission denied* → [Installation 3.2](03-installation.md#32-serial-port-permission-linux-once). Just double-tapped RST? The board is in the bootloader — flash it or press RST once. |
| **sniffer image** | The Nordic sniffer is flashed. Press **Flash xiao-test-tool** in the banner and double-tap RST when asked ([Ch. 4](04-firmware.md)), or use Wireshark ([Ch. 9](09-passive-capture.md)). |
| Bridge: *address already in use* | Another bridge is still running on port 8000. Stop it (`pgrep -af backend/bridge.py`, then `kill <pid>`), or use `--http-port 8080`. |
| The page shows an old layout after an update | Reload once. (Versions before this manual cached the UI and needed two reloads.) |

## Pairing and links

| Symptom | Cause and fix |
|---|---|
| Remote not in the scan list | *Remotes only* ticked and the remote advertises no HID info — untick it. Or: not in pairing mode (pairing mode times out after ~30–60 s — retry), or connected to something else. Switch the STB off. |
| Remote listed only with **DIRECT** | It is reconnecting to a host it is bonded with. Put it in pairing mode. |
| **NOT PAIRING** badge / connects then *disconnected (reason 0x3E)* at once | The remote is only reconnecting to its own host and ignores the jig. Unplug that host and use real pairing mode; a Fire TV remote may need a factory reset first ([Ch. 7](07-pairing-remotes.md#pairing-a-remote)). |
| **connect timed out** | The remote stopped advertising, or the advert was directed elsewhere. Retry in pairing mode. |
| **pairing failed** | Put the remote back in pairing mode and press **Pair** (it pairs fresh). |
| *pair: gave up — is the remote in pairing mode?* | Pair tried three connections and the remote answered none. Check for the **PAIRING** badge; pairing mode times out after ~30–60 s. |
| A **passkey** box appears | The remote refused Just Works pairing (0x83). Type the 6 digits on the remote and press OK / Enter. |
| Log: *ignoring bonded remote …: the session is locked to another remote* | Another bonded unit woke up. Intended — the jig stays with the locked remote. To switch, **Pair** the new one, or 🔒 / **Unlock** in Bonded remotes. |
| A remote has no **PAIRING** / **NOT PAIRING** badge | It advertises neither HID nor an appearance and isn't bonded yet. Untick *Remotes only* and look for its name or address. |
| Banner **bonded ✓ · press a key** | The link is up but the jig can't tell by itself if the remote sends (no Android TV voice service). Press a key: it turns **live**. |
| Banner **silent ⚠** / keys stop | The remote only sends on a freshly paired link (Android TV voice remote). Put it in pairing mode (STB off); the jig re-pairs by itself ([Ch. 7](07-pairing-remotes.md#silent-links)). |
| Fire TV remote: keys stop after a while | It dropped the link to save power. Press any key — it reconnects within a second. |
| Log: *bonded remote is advertising directed to XX:… which is not the jig* | The remote is trying to reach another host (normally the STB). Switch the STB off; use pairing mode. |

## Keys, voice, battery

| Symptom | Cause and fix |
|---|---|
| Thousands of "keys" per second (fake key flood) | Fixed in firmware 0.3: reports without key usages (motion streams) are no longer decoded as keys. Re-flash the jig. They now appear under **Keys ▸ Other HID reports**. |
| Key shows as `LG_0x…` | An LG key code not in the table yet. **Map key** binds it; or add it to `LG_KEYS` in `backend/lg_magic.py`. |
| Key shows as `0x…` | A vendor-specific code. **Keys ▸ Map key** to bind it, or add it to `backend/hid_usage.py` and run `firmware/build.sh` to update the firmware's names. |
| No voice | Hold the mic button (don't just tap). **Connect ▸ GATT** must show *Android TV Voice*. Try **Voice ▸ Open mic**. Fire TV remotes: not supported yet ([Ch. 7](07-pairing-remotes.md#fire-tv-voice)). |
| WAV only a fraction of a second long | Fixed in the current bridge (late packets after the 15 s cap used to replace the recording). Update and use **Download raw** for old captures. |
| Remote battery shows "—" | The remote has no Battery Service, or it hasn't been read yet — press **Read values**. |
| Jig battery ~4.2 V with no cell | Normal: the VBAT pin sees the charger output. |
| Jig battery **LOW** / IR send refused | The cell is below 3.4 V / 3.3 V. Charge over USB-C ([Ch. 2](02-hardware.md#battery-and-charging)). |

## IR

| Symptom | Cause and fix |
|---|---|
| Nothing received | Check the wiring (IR_RX → D0, power from 3V3), that **RX on** is ticked, and aim the remote at the unit's receiver. |
| Loopback test fails | Hold a white card 2–5 cm in front of the unit; avoid direct sunlight; check `ir_tx_done` in the IR tab. |

## Flashing

| Symptom | Cause and fix |
|---|---|
| Helper waits forever | Double-tap RST faster (two taps within ~0.5 s). The drive may be named `XIAO-SENSE`; the helper handles both. |
| Board doesn't start after flashing the tool from the sniffer | Use `xiao_test_tool_full.uf2` (with the SoftDevice), not the app-only file. |
| *Write error* at the end of a manual copy | Normal: the board resets as soon as the last block lands. |

## Collecting information for a bug report

1. **Connect ▸ Log ▸ Download** — the session log with remote details.
2. **Connect ▸ Remote info ▸ Copy**.
3. The bridge's console output.
4. If it's about the radio link: a Wireshark capture ([Ch. 9](09-passive-capture.md)).

[← Passive capture](09-passive-capture.md) · [Manual index](README.md) · [Next: Reference →](11-reference.md)
