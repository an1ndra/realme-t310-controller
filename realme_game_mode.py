#!/usr/bin/env python3
"""
realme_game_mode.py — Toggle Game Mode (low-latency mode) on Realme Buds T310.

Confirmed by analyzing a realme Link Android HCI snoop capture:
    Query state: cmd=0x010d, payload=01 06
                 response cmd=0x810d, data=01 06 [state]
    Toggle:      cmd=0x0304, payload=06 00
                 response cmd=0x8304, data=00 (status OK)

The official app toggles game mode and then reads back the state. This script
provides explicit "on"/"off" commands by querying first and toggling only
when needed.

Frame format:
    aa [total_len-2: 2B LE] [flags=00] [cmd: 2B LE] [seq: 1B] [data_len: 2B LE] [data]

Usage:
    python3 realme_game_mode.py on
    python3 realme_game_mode.py off
    python3 realme_game_mode.py status
"""

import socket
import sys
import time

MAC = "84:9D:4B:82:9C:98"
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


def send_recv(payload: bytes, recv_timeout: float = 2.0):
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


def query_status() -> int | None:
    """Return game-mode state (0 off, 1 on) or None on failure."""
    payload = build_frame(0x01, 0x0D, bytes([0x01, 0x06]))
    resp = send_recv(payload)
    if not resp or len(resp) < 10:
        return None
    data_len = resp[7] | (resp[8] << 8)
    if data_len < 3 or len(resp) < 9 + data_len:
        return None
    data = resp[9:9 + data_len]
    if data[:2] == bytes([0x01, 0x06]):
        return data[2]
    return None


def toggle() -> bool:
    """Send the game-mode toggle command. Returns True if ACKed."""
    payload = build_frame(0x03, 0x04, bytes([0x06, 0x00]))
    resp = send_recv(payload)
    if not resp:
        print("No response from earbuds.")
        return False
    print(f"Toggle response: {resp.hex()}")
    if len(resp) >= 10 and resp[4] == 0x03 and resp[5] == 0x84:
        status = resp[9] if len(resp) > 9 else None
        print(f"Toggle ACK status={status}")
        return status == 0
    print(f"Unexpected response: {resp.hex()}")
    return False


def set_state(desired: int) -> bool:
    """Set game mode to desired state (0 or 1) using query+toggle when possible."""
    current = query_status()
    if current is None:
        print("Could not query current game-mode state; toggling blindly.")
        print("Listen for the game-mode prompt to confirm the new state.")
        return toggle()
    print(f"Current game mode: {'ON' if current else 'OFF'}")
    if current == desired:
        print(f"Already {'ON' if desired else 'OFF'}.")
        return True
    if not toggle():
        return False
    new_state = query_status()
    if new_state is None:
        print("Toggle sent, but could not verify new state.")
        return False
    print(f"New game mode: {'ON' if new_state else 'OFF'}")
    return new_state == desired


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd in ("on", "1", "true", "enable"):
        ok = set_state(1)
    elif cmd in ("off", "0", "false", "disable"):
        ok = set_state(0)
    elif cmd in ("status", "state"):
        state = query_status()
        if state is None:
            print("Could not query game-mode status.")
            sys.exit(1)
        print(f"Game mode: {'ON' if state else 'OFF'}")
        ok = True
    elif cmd == "toggle":
        ok = toggle()
    else:
        print(__doc__)
        sys.exit(1)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
