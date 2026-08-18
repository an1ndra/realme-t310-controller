#!/usr/bin/env python3
"""
realme_battery.py — Read battery levels from realme Buds T310.

First tries the proprietary RFCOMM command 0x0601 which returns detailed
left / right / case battery percentages. Falls back to BlueZ Battery1 D-Bus
interface if RFCOMM is unavailable.

Usage:
    python3 realme_battery.py [MAC]

Default MAC is 84:9D:4B:82:9C:98.
"""

import socket
import sys
import time
from pathlib import Path

try:
    import dbus
except ImportError:
    dbus = None

DEFAULT_MAC = "84:9D:4B:82:9C:98"
CHANNEL = 1
TIMEOUT = 5


def build_frame(cmd_hi: int, cmd_lo: int, data: bytes, seq: int = 0x01) -> bytes:
    data_len = len(data)
    payload_size = 1 + 2 + 1 + 2 + data_len
    len_field = payload_size + 1
    return (
        bytes(
            [
                0xAA,
                len_field & 0xFF,
                (len_field >> 8) & 0xFF,
                0x00,
                cmd_hi,
                cmd_lo,
                seq & 0xFF,
                data_len & 0xFF,
                0x00,
            ]
        )
        + data
    )


def rfcomm_battery(mac: str) -> dict[str, int] | None:
    """Query detailed battery via RFCOMM command 0x0601."""
    payload = build_frame(0x06, 0x01, bytes([]))
    last_err = None
    for attempt in range(5):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(TIMEOUT)
        try:
            sock.connect((mac, CHANNEL))
            break
        except OSError as e:
            last_err = e
            sock.close()
            if e.errno in (16, 77) and attempt < 4:
                time.sleep(0.6)
                continue
            return None
    else:
        return None

    try:
        sock.sendall(payload)
        sock.settimeout(2.0)
        resp = sock.recv(64)
    except (OSError, socket.timeout) as e:
        print(f"RFCOMM error: {e}")
        sock.close()
        return None
    finally:
        sock.close()

    if not resp or len(resp) < 10 or resp[4] != 0x06 or resp[5] not in (0x81, 0x84):
        return None

    data_len = resp[7] | (resp[8] << 8)
    if data_len < 8 or len(resp) < 9 + data_len:
        return None
    data = resp[9:9 + data_len]

    # Expected: status count [side level]...
    if data[0] != 0x00:
        return None
    count = data[1]
    levels = {}
    i = 2
    for _ in range(count):
        if i + 1 >= len(data):
            break
        side = data[i]
        level = data[i + 1] & 0x7F  # high bit is charging flag
        charging = bool(data[i + 1] & 0x80)
        name = {1: "left", 2: "right", 3: "case"}.get(side, f"side{side}")
        levels[name] = level
        levels[f"{name}_charging"] = charging
        i += 2
    return levels


def bluez_battery(mac: str) -> int | None:
    """Read combined battery from BlueZ Battery1."""
    if dbus is None:
        return None
    path = "/org/bluez/hci0/dev_" + mac.replace(":", "_").upper()
    candidates = []
    if Path("/run/dbus/system_bus_socket").exists():
        candidates.append(None)
    if Path("/run/host/run/dbus/system_bus_socket").exists():
        candidates.append("unix:path=/run/host/run/dbus/system_bus_socket")

    for addr in candidates:
        try:
            bus = dbus.SystemBus() if addr is None else dbus.bus.BusConnection(addr)
            obj = bus.get_object("org.bluez", path)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return int(props.Get("org.bluez.Battery1", "Percentage"))
        except Exception:
            continue
    return None


def main():
    mac = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MAC

    detailed = rfcomm_battery(mac)
    if detailed:
        parts = []
        for label in ("left", "right", "case"):
            if label in detailed:
                chg = " (charging)" if detailed.get(f"{label}_charging") else ""
                parts.append(f"{label.capitalize()}: {detailed[label]}%{chg}")
        print("Battery: " + ", ".join(parts))
        return

    pct = bluez_battery(mac)
    if pct is not None:
        print(f"Battery: {pct}% (combined, no per-earbud data)")
        return

    print("Could not read battery. Is the device paired and connected?")
    sys.exit(1)


if __name__ == "__main__":
    main()
