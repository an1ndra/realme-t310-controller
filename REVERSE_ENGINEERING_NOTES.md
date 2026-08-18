# Realme Buds T310 — Reverse Engineering Notes

Device: **realme Buds T310**  
MAC used in this project: `84:9D:4B:82:9C:98`  
Host system: Fedora KDE Plasma (Linux)  
Transport: Bluetooth Classic BR/EDR, RFCOMM channel 1 (Serial Port Profile)

---

## 1. How we discovered the protocol

1. **Bluetooth service inspection** via BlueZ D-Bus showed two vendor-specific UUIDs:
   - `0000079a-d102-11e1-9b23-00025b00a5a5`
   - `df21fe2c-2515-4fdb-8886-f12c4d67927c`
   and the standard SPP UUID `00001101-0000-1000-8000-00805f9b34fb`.

2. **Protocol reference**: the `earFrame` project documented a very similar protocol for the realme Buds Air 7 Pro (RFCOMM SPP, `aa`-framed packets). That gave us the initial command structure.

3. **Live probing** on the connected earbuds confirmed which command IDs are accepted and which return ACKs.

4. **Android HCI snoop captures** from the official **realme Link** app provided the authoritative bytes for game mode and EQ changes.

---

## 2. Protocol frame format

All proprietary commands use the same `aa` frame:

```
aa [len-2: 2B LE] [flags: 1B] [cmd: 2B LE] [seq: 1B] [data_len: 2B LE] [data]
```

- `len-2` is the total frame size minus 2.
- `flags` is always `0x00` for host-to-earbud commands.
- `cmd` is little-endian; responses have bit 7 set in the high byte (e.g. `0x0404` → `0x8404`).
- `seq` increments per command; starting at `0x01` works fine.
- `data_len` is little-endian.

---

## 3. Confirmed working commands

### ANC / transparency (fully confirmed)

| Mode | Command | Data |
|---|---|---|
| Normal (ANC off) | `0x0404` | `01 01 01` |
| Transparent | `0x0404` | `01 01 02` |
| Noise Cancellation | `0x0404` | `01 01 08` |

- Works without any initialization handshake.
- Earbuds reply with `0x8404` ACK, status `0x00`.
- Verified audibly by the user.

### Game mode / low-latency mode (confirmed from Android capture)

| Action | Command | Data |
|---|---|---|
| Toggle game mode | `0x0304` | `06 00` |
| Query state | `0x010d` | `01 06` |

- The app toggles game mode with `0x0304 06 00` and then reads state with `0x010d 01 06`.
- The toggle command ACKs reliably on Linux.
- The **query** command only responds inside the realme Link session handshake; on a fresh Linux RFCOMM connection it times out.

### EQ presets (confirmed from Android capture)

| Action | Command | Data |
|---|---|---|
| Cycle EQ preset | `0x0604` | `01` |
| Query current preset | `0x010f` | (empty) |

- `0x0604 01` cycles through EQ presets.
- `0x010f` query responds with `00 [preset]` (presets 0–3 observed).
- The query also requires the realme Link handshake and does not answer on a fresh Linux connection.

### Battery level (RFCOMM 0x0601)

After re-examining the Android btsnoop captures, command `0x0601` returns a detailed battery response:

| Byte(s) | Meaning |
|---|---|
| `00` | status |
| `03` | device count (left, right, case) |
| `01 XX` | left earbud level (XX & 0x7F) |
| `02 YY` | right earbud level (YY & 0x7F) |
| `03 ZZ` | case level (ZZ & 0x7F) |

The high bit of each level byte is a charging flag.

Response example: `aa 0f 00 00 06 81 01 08 00 00 03 01 64 02 5a 03 00`
→ left 100%, right 90%, case 0%.

BlueZ `Battery1` is kept as a fallback when RFCOMM is unavailable; it only reports one combined percentage.

---

## 4. What we guessed vs. what we proved

### Guessed initially

- We thought game mode might use command `0x0504` or `0x0604` based on live probing.
- We thought EQ might use `0x0406` (from the Air 7 Pro docs) or `0x0504`/`0x0604`.

### Proven by capture

- Game mode is `0x0304 06 00`.
- EQ cycle is `0x0604 01`.

### Still uncertain

- **Explicit EQ preset selection**: the capture only ever shows `0x0604 01` (cycle). We do not have evidence of a command like `0x0604 [preset]` that sets a specific preset directly.
- **Battery level command**: the app receives battery data, but we have not extracted a reliable Linux-readable command.
- **Find-my-earbuds, dual-device connection, touch controls, wear detection**: not investigated yet.

---

## 5. Linux limitations

1. **State queries need the app handshake**. Commands like `0x010d` (game mode status) and `0x010f` (EQ status) work inside realme Link because the app performs a long initialization sequence first. On a fresh Linux RFCOMM socket they time out.

2. **Game mode and EQ are toggles**. Because we cannot query state reliably, the Linux tools toggle and rely on the user to listen for the earbud prompt or audible change.

3. **No persistent connection**. Each button click opens a new RFCOMM socket, sends one command, and closes. This causes occasional `Device or resource busy` errors, which the scripts now retry.

4. **RFCOMM channel locked to 1**. We did not find SDP on the Linux side, but the capture and probing confirm channel 1.

---

## 6. Qt desktop app

A PyQt6 controller is in `qt_app/`:

- `realme_t310_controller.py` — main application
- `requirements.txt` — Python dependencies
- `realme-t310-controller.desktop` — KDE/GNOME launcher entry

Features:
- Modern Qt6 widget UI with QSS styling
- Buttons for Normal / ANC / Transparent
- Toggle game mode
- Cycle EQ preset
- Best-effort numbered EQ preset buttons (cycle-based)
- Detailed left / right / case battery via RFCOMM `0x0601`
- KDE system-tray icon with battery tooltip
- BlueZ Battery1 fallback
- MAC address setting (saved with QSettings)
- Background worker thread so the UI never freezes
- Retry logic for RFCOMM busy states

Run:

```bash
cd /home/anindra/Develop/realme-t310-anc-controls/qt_app
pip install -r requirements.txt   # if PyQt6 is not already installed
python3 realme_t310_controller.py
```

To install the launcher icon:

```bash
cp realme-t310-controller.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

---

## 7. CLI quick reference

```bash
anc normal            # ANC off
anc on                # Noise cancellation
anc transparent       # Transparency mode
anc game              # Toggle game mode
anc eq                # Cycle EQ preset
anc battery           # Read left/right/case battery via RFCOMM
```

Individual scripts:

```bash
python3 realme_anc.py normal
python3 realme_game_mode.py toggle
python3 realme_eq.py next
python3 realme_battery.py
```

## 8. How to make it better

### High value, doable now

1. **Implement the full realme Link handshake on Linux**  
   Replay the complete initialization sequence from the Android capture before sending queries. If successful, game-mode and EQ status queries will work, enabling reliable on/off/preset selection.

2. **Cache the RFCOMM connection**  
   Keep one RFCOMM socket open while the app is running instead of opening/closing per command. This removes the busy-state retries and makes the app feel instant.

3. **Add a system tray icon / KDE Plasma widget**  
   A tray menu or plasmoid would let the user switch modes without opening the full window.

### Medium value

4. **Map EQ preset numbers to names**  
   The realme Link app shows names like Original, Deep Bass, Serenade, Pure Bass. We know presets are 0–3, but the exact mapping is not confirmed. A quick listening test can map them.

5. **Battery level display**  
   The app reads battery during initialization. With the full handshake replicated, we can parse the battery response and show left/right/case percentages.

6. **Firmware version / device info**  
   Command `0x0501` returns the firmware version string. This could be shown in an "About" panel.

### Harder / needs more captures

7. **Find-my-earbuds and touch-control customization**  
   These likely have their own command IDs. Another Android capture while using those features would reveal them.

8. **Auto-reconnect on Bluetooth connect**  
   Use D-Bus to detect when the T310 connects and automatically show/enable the controller window.

9. **Native C++/Qt6 or KDE Kirigami app**  
   A compiled C++ app would start faster and integrate better with KDE Plasma. The protocol logic can be ported directly.

---

## 9. Files in this repo

| File | Purpose |
|---|---|
| `realme_anc.py` | CLI for ANC modes |
| `realme_game_mode.py` | CLI for game-mode toggle |
| `realme_eq.py` | CLI for EQ cycle |
| `realme_battery.py` | CLI to read battery from BlueZ D-Bus |
| `analyze.py` | Parse Android btsnoop HCI logs |
| `sniff.py` | Manual RFCOMM probe tool |
| `probe_*.py` | Various blind-probe helpers used during discovery |
| `qt_app/realme_t310_controller.py` | Qt6 GUI controller |
| `btsnoop_hci.log` | Game-mode Android capture |
| `eq_btsnoop.log` | EQ Android capture |
| `REVERSE_ENGINEERING_NOTES.md` | This document |

---

## 10. Safety notes

- All commands were discovered from traffic between the official app and the user’s own earbuds. No unauthorized access is involved.
- The commands are simple feature toggles; there is no evidence they can brick the device, but avoid sending large numbers of unknown commands in rapid succession.
