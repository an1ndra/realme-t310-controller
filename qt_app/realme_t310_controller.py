#!/usr/bin/env python3
"""
realme_t310_controller.py — Qt6 desktop controller for realme Buds T310.
"""

import argparse
import socket
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    import dbus
except ImportError:
    dbus = None

DEFAULT_MAC = "84:9D:4B:82:9C:98"
RFCOMM_CHANNEL = 1


def log_to_file(path: str, msg: str):
    try:
        with open(path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def encode_frame(cmd_hi: int, cmd_lo: int, data: bytes, seq: int = 0x01) -> bytes:
    data_len = len(data)
    payload_size = 1 + 2 + 1 + 2 + data_len
    len_field = payload_size + 1
    return bytes([
        0xAA,
        len_field & 0xFF,
        (len_field >> 8) & 0xFF,
        0x00,
        cmd_hi,
        cmd_lo,
        seq & 0xFF,
        data_len & 0xFF,
        0x00,
    ]) + data


def parse_frame(resp: bytes) -> tuple[int, int, bytes] | None:
    if len(resp) < 9 or resp[0] != 0xAA:
        return None
    data_len = resp[7] | (resp[8] << 8)
    if len(resp) < 9 + data_len:
        return None
    return resp[4], resp[5], resp[9:9 + data_len]


@dataclass
class WorkerTask:
    name: str
    payload: bytes
    label: str


# ---------------------------------------------------------------------------
# Background RFCOMM worker
# ---------------------------------------------------------------------------

class RfcommWorker(QtCore.QThread):
    log = QtCore.pyqtSignal(str)
    finished_task = QtCore.pyqtSignal(str, str)
    connected_state = QtCore.pyqtSignal(bool)

    def __init__(self, get_mac: Callable[[], str], debug_path: str | None = None, parent=None):
        super().__init__(parent)
        self._get_mac = get_mac
        self._debug_path = debug_path
        self._queue: list[WorkerTask] = []
        self._mutex = QtCore.QMutex()
        self._cond = QtCore.QWaitCondition()
        self._running = True

    def _debug(self, msg: str):
        if self._debug_path:
            log_to_file(self._debug_path, f"[worker] {msg}")

    def enqueue(self, task: WorkerTask):
        with QtCore.QMutexLocker(self._mutex):
            self._queue.append(task)
            self._cond.wakeOne()

    def stop(self):
        with QtCore.QMutexLocker(self._mutex):
            self._running = False
            self._cond.wakeOne()
        self.wait(3000)

    def run(self):
        self._debug("worker started")
        while True:
            task = None
            with QtCore.QMutexLocker(self._mutex):
                if not self._running:
                    break
                if self._queue:
                    task = self._queue.pop(0)
                else:
                    self._cond.wait(self._mutex, 100)
                    continue
            if task is None:
                continue

            mac = self._get_mac()
            self.log.emit(f"{task.label} -> {mac}")
            try:
                resp = self._send_receive(mac, task.payload)
            except Exception as e:
                self._debug(f"exception in _send_receive: {traceback.format_exc()}")
                self.log.emit(f"{task.label}: failed ({e})")
                self.connected_state.emit(False)
                continue

            if resp:
                self.log.emit(f"{task.label}: OK")
                self.finished_task.emit(task.name, "OK")
            else:
                self.log.emit(f"{task.label}: failed / no response")
                self.connected_state.emit(False)

    def _send_receive(self, mac: str, payload: bytes) -> bytes | None:
        last_err = None
        sock = None
        for attempt in range(5):
            try:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                sock.settimeout(6)
                sock.connect((mac, RFCOMM_CHANNEL))
                self.connected_state.emit(True)
                break
            except OSError as e:
                last_err = e
                if e.errno in (16, 77) and attempt < 4:
                    self.log.emit(f"RFCOMM busy, retry {attempt + 1}/5")
                    time.sleep(0.8)
                    continue
                raise
        else:
            raise last_err or OSError("Could not connect")

        try:
            sock.sendall(payload)
            sock.settimeout(3.0)
            chunks = []
            while True:
                chunk = sock.recv(64)
                if not chunk:
                    break
                chunks.append(chunk)
                if len(chunk) < 64:
                    break
            return b"".join(chunks)
        finally:
            try:
                sock.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Battery helpers
# ---------------------------------------------------------------------------

class BatteryHelper:
    def __init__(self, mac: str):
        self.mac = mac

    def detailed(self) -> dict[str, int] | None:
        payload = encode_frame(0x06, 0x01, bytes([]))
        sock = None
        for attempt in range(3):
            try:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
                sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                sock.settimeout(5)
                sock.connect((self.mac, RFCOMM_CHANNEL))
                break
            except OSError as e:
                if e.errno in (16, 77) and attempt < 2:
                    time.sleep(0.6)
                    continue
                return None
        else:
            return None

        try:
            sock.sendall(payload)
            sock.settimeout(2.0)
            resp = sock.recv(64)
        except (OSError, socket.timeout):
            return None
        finally:
            try:
                sock.close()
            except Exception:
                pass

        parsed = parse_frame(resp)
        if not parsed or parsed[0] != 0x06 or parsed[1] != 0x81:
            return None
        data = parsed[2]
        if len(data) < 8 or data[0] != 0x00:
            return None
        count = data[1]
        levels = {}
        i = 2
        for _ in range(count):
            if i + 1 >= len(data):
                break
            side = data[i]
            level = data[i + 1] & 0x7F
            charging = bool(data[i + 1] & 0x80)
            name = {1: "left", 2: "right", 3: "case"}.get(side, f"side{side}")
            levels[name] = level
            levels[f"{name}_charging"] = charging
            i += 2
        return levels

    def bluez_combined(self) -> int | None:
        if dbus is None:
            return None
        path = "/org/bluez/hci0/dev_" + self.mac.replace(":", "_").upper()
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


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, debug_path: str | None = None):
        super().__init__()
        self._debug_path = debug_path
        self.setWindowTitle("realme Buds T310")
        self.resize(420, 640)

        self.settings = QtCore.QSettings("realme-t310", "controller")
        self.mac = self.settings.value("mac", DEFAULT_MAC)
        self._current_eq = 0

        self.worker = RfcommWorker(lambda: self.mac, debug_path=debug_path)
        self.worker.log.connect(self._on_log)
        self.worker.finished_task.connect(self._on_finished)
        self.worker.connected_state.connect(self._on_connection_state)
        self.worker.start()

        self._build_ui()

        self._debug("mainwindow initialized")

    def _debug(self, msg: str):
        if self._debug_path:
            log_to_file(self._debug_path, f"[gui] {msg}")

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QtWidgets.QLabel("realme Buds T310")
        title.setFont(QtGui.QFont("Sans", 16, QtGui.QFont.Weight.Bold))
        layout.addWidget(title)

        # Status card
        status_box = QtWidgets.QGroupBox("Status")
        status_layout = QtWidgets.QFormLayout(status_box)
        self.status_label = QtWidgets.QLabel("Idle")
        status_layout.addRow("Connection:", self.status_label)
        layout.addWidget(status_box)

        # MAC card
        mac_box = QtWidgets.QGroupBox("Connection")
        mac_layout = QtWidgets.QFormLayout(mac_box)
        self.mac_edit = QtWidgets.QLineEdit(self.mac)
        self.mac_edit.setReadOnly(True)
        self.mac_edit.setStyleSheet("background: #f0f0f0; color: #333;")
        mac_layout.addRow("MAC Address:", self.mac_edit)
        self.refresh_btn = QtWidgets.QPushButton("Refresh Battery")
        self.refresh_btn.clicked.connect(self._update_battery)
        mac_layout.addRow("", self.refresh_btn)
        layout.addWidget(mac_box)

        # Battery card
        self.batt_box = QtWidgets.QGroupBox("Battery")
        batt_layout = QtWidgets.QFormLayout(self.batt_box)
        self.battery_labels: dict[str, QtWidgets.QLabel] = {}
        self.battery_bars: dict[str, QtWidgets.QProgressBar] = {}
        for key, label in [("left", "Left Earbud"), ("right", "Right Earbud"), ("case", "Case")]:
            lbl = QtWidgets.QLabel("--")
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            self.battery_labels[key] = lbl
            self.battery_bars[key] = bar
            batt_layout.addRow(f"{label}:", QtWidgets.QWidget())  # spacer row
            batt_layout.addRow(lbl, bar)
        layout.addWidget(self.batt_box)

        # ANC card
        anc_box = QtWidgets.QGroupBox("Noise Control")
        anc_layout = QtWidgets.QFormLayout(anc_box)
        self.anc_combo = QtWidgets.QComboBox()
        self.anc_combo.addItems(["Normal", "ANC", "Transparency"])
        self.anc_combo.currentTextChanged.connect(self._set_anc)
        anc_layout.addRow("Mode:", self.anc_combo)
        layout.addWidget(anc_box)

        # Game mode card
        game_box = QtWidgets.QGroupBox("Features")
        game_layout = QtWidgets.QVBoxLayout(game_box)
        game_top = QtWidgets.QHBoxLayout()
        game_label = QtWidgets.QLabel("Game Mode:")
        self.game_switch = QtWidgets.QCheckBox()
        self.game_switch.stateChanged.connect(self._toggle_game)
        game_top.addWidget(game_label)
        game_top.addWidget(self.game_switch)
        game_layout.addLayout(game_top)

        # EQ card
        eq_box = QtWidgets.QGroupBox("Equalizer")
        eq_layout = QtWidgets.QVBoxLayout(eq_box)
        eq_grid = QtWidgets.QGridLayout()
        self.eq_buttons = {}
        for idx, name in enumerate(["Original", "Deep Bass", "Serenade", "Pure Bass"]):
            btn = QtWidgets.QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=idx: self._set_eq(n))
            eq_grid.addWidget(btn, idx // 2, idx % 2)
            self.eq_buttons[idx] = btn
        eq_layout.addLayout(eq_grid)
        self.eq_cycle_btn = QtWidgets.QPushButton("Cycle EQ")
        self.eq_cycle_btn.clicked.connect(self._cycle_eq)
        eq_layout.addWidget(self.eq_cycle_btn)
        game_layout.addWidget(eq_box)

        layout.addWidget(game_box, 1)

        # Log card
        log_box = QtWidgets.QGroupBox("Log")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(100)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box)

    def _on_log(self, msg: str):
        ts = QtCore.QTime.currentTime().toString("hh:mm:ss")
        self.log_view.appendPlainText(f"[{ts}] {msg}")

    def _on_finished(self, name: str, result: str):
        if "battery" in name.lower():
            return
        self._on_log(f"Done: {name} = {result}")

    def _on_connection_state(self, connected: bool):
        self.status_label.setText("Connected" if connected else "Disconnected")

    def _set_anc(self, mode: str):
        mapping = {
            "Normal": 0x01,
            "ANC": 0x08,
            "Transparency": 0x02,
        }
        if mode not in mapping:
            return
        value = mapping[mode]
        payload = encode_frame(0x04, 0x04, bytes([0x01, 0x01, value]))
        self.worker.enqueue(WorkerTask(f"anc-{mode}", payload, f"Set {mode}"))

    def _toggle_game(self, state: int):
        payload = encode_frame(0x03, 0x04, bytes([0x06, 0x00]))
        self.worker.enqueue(WorkerTask("game-toggle", payload, "Toggle game mode"))

    def _cycle_eq(self):
        self._current_eq = (self._current_eq + 1) % 4
        payload = encode_frame(0x06, 0x04, bytes([0x01]))
        self.worker.enqueue(WorkerTask("eq-cycle", payload, "Cycle EQ"))

    def _set_eq(self, preset: int):
        steps = (preset - self._current_eq) % 4
        if steps == 0:
            return
        for _ in range(steps):
            payload = encode_frame(0x06, 0x04, bytes([0x01]))
            self.worker.enqueue(WorkerTask("eq-cycle", payload, "Cycle EQ"))
        self._current_eq = preset

    def _update_battery(self):
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("...")
        QtCore.QTimer.singleShot(0, self._do_battery_read)

    def _do_battery_read(self):
        try:
            helper = BatteryHelper(self.mac)
            levels = helper.detailed()
            if not levels:
                pct = helper.bluez_combined()
                self._apply_battery("left", pct, None)
                self._apply_battery("right", None, None)
                self._apply_battery("case", None, None)
                return

            for key in ("left", "right", "case"):
                self._apply_battery(key, levels.get(key), levels.get(f"{key}_charging"))
        except Exception as e:
            self._debug(f"battery read error: {traceback.format_exc()}")
            self._on_log(f"Battery read error: {e}")
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh Battery")

    def _apply_battery(self, key: str, pct: int | None, charging: bool | None):
        bar = self.battery_bars[key]
        lbl = self.battery_labels[key]
        if pct is None:
            bar.setValue(0)
            lbl.setText("--")
            return
        bar.setValue(pct)
        chg = " (charging)" if charging else ""
        lbl.setText(f"{pct}%{chg}")

    def closeEvent(self, event):
        self.worker.stop()
        QtWidgets.QApplication.instance().quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", metavar="PATH", help="Write debug log to PATH")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(debug_path=args.debug)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()