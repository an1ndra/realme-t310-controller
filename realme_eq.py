#!/usr/bin/env python3
"""
realme_eq.py — Cycle/select EQ presets on Realme Buds T310.

Discovered from Android realme Link HCI snoop capture:
    Query current preset:  cmd=0x010f, payload=(empty)
                           response cmd=0x810f, data=00 [preset]
    Cycle/change preset:   cmd=0x0604, payload=01

The official app appears to cycle presets with the 0x0604 01 command.
This script provides explicit preset selection by reading the current
preset and cycling until the desired one is active.

Presets observed in the capture: 0, 1, 2, 3. Mapping to app names is
unknown; typical realme EQ names are Original, Deep Bass, Serenade, Pure Bass.

Usage:
    python3 realme_eq.py status
    python3 realme_eq.py next
    python3 realme_eq.py 0
    python3 realme_eq.py 1
    python3 realme_eq.py 2
    python3 realme_eq.py 3
"""

import socket
import sys
import time

MAC = "84:9D:4B:82:9C:98"
CHANNEL = 1
TIMEOUT = 5
MAX_CYCLES = 8


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


def send_recv(payload: bytes, recv_timeout: float = 2.0):
    # RFCOMM can be briefly busy if the previous connection is still closing.
    last_err = None
    for attempt in range(5):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(TIMEOUT)
        try:
            sock.connect((MAC, CHANNEL))
            break
        except OSError as e:
            last_err = e
            sock.close()
            if e.errno in (16, 77) and attempt < 4:
                time.sleep(0.6)
                continue
            raise
    else:
        raise last_err
    sock.sendall(payload)
    sock.settimeout(recv_timeout)
    try:
        resp = sock.recv(64)
    except socket.timeout:
        resp = None
    sock.close()
    return resp


def query_preset() -> int | None:
    """Return current EQ preset ID or None on failure."""
    payload = build_frame(0x01, 0x0F, bytes([]))
    resp = send_recv(payload)
    if not resp or len(resp) < 10:
        return None
    data_len = resp[7] | (resp[8] << 8)
    if data_len < 2 or len(resp) < 9 + data_len:
        return None
    data = resp[9:9 + data_len]
    if data[0] == 0x00:
        return data[1]
    return None


def cycle_preset() -> bool:
    """Send the EQ cycle command. Returns True if ACKed."""
    payload = build_frame(0x06, 0x04, bytes([0x01]))
    resp = send_recv(payload)
    if not resp:
        print("No response from earbuds.")
        return False
    print(f"Cycle response: {resp.hex()}")
    if len(resp) >= 10 and resp[4] == 0x06 and resp[5] == 0x84:
        status = resp[9] if len(resp) > 9 else None
        return status == 0
    return False


def set_preset(target: int) -> bool:
    """Cycle until target preset is active."""
    if not 0 <= target <= 3:
        print("Preset must be 0-3.")
        return False

    current = query_preset()
    if current is None:
        print("Could not query current EQ preset.")
        return False
    print(f"Current EQ preset: {current}")
    if current == target:
        print(f"Already at preset {target}.")
        return True

    for _ in range(MAX_CYCLES):
        if not cycle_preset():
            return False
        new = query_preset()
        if new is None:
            print("Cycle sent, but could not verify new preset.")
            return False
        print(f"  -> preset {new}")
        if new == target:
            print(f"Reached preset {target}.")
            return True

    print(f"Failed to reach preset {target} after {MAX_CYCLES} cycles.")
    return False


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd in ("status", "state"):
        preset = query_preset()
        if preset is None:
            print("Could not query EQ preset.")
            sys.exit(1)
        print(f"EQ preset: {preset}")
    elif cmd in ("next", "cycle"):
        current = query_preset()
        if current is not None:
            print(f"Current: {current}")
        if cycle_preset():
            new = query_preset()
            if new is not None:
                print(f"New preset: {new}")
        else:
            sys.exit(1)
    elif cmd.isdigit():
        ok = set_preset(int(cmd))
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
