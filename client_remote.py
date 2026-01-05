
import socket
import struct
import sys
import json
import os
from dataclasses import dataclass, asdict

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QImage, QPixmap, QResizeEvent, QKeyEvent,
    QMouseEvent, QWheelEvent
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QMessageBox,
    QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDialog, QFormLayout,
    QLineEdit, QSpinBox
)

DEFAULT_PORT = 5000
CONNECTIONS_FILE = os.path.join(os.path.expanduser("~"), ".remote_connections.json")


@dataclass
class ConnectionItem:
    name: str
    host: str
    port: int = DEFAULT_PORT


def load_connections() -> list[ConnectionItem]:
    if not os.path.exists(CONNECTIONS_FILE):
        return [ConnectionItem(name="Default", host="10.50.163.66", port=DEFAULT_PORT)]
    try:
        with open(CONNECTIONS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = []
        for x in raw:
            items.append(ConnectionItem(
                name=str(x.get("name", "Unnamed")),
                host=str(x.get("host", "")).strip(),
                port=int(x.get("port", DEFAULT_PORT)),
            ))
        return items or [ConnectionItem(name="Default", host="10.50.163.66", port=DEFAULT_PORT)]
    except Exception:
        return [ConnectionItem(name="Default", host="10.50.163.66", port=DEFAULT_PORT)]


def save_connections(items: list[ConnectionItem]) -> None:
    with open(CONNECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in items], f, indent=2)


# ---------------- Receiver thread (Server->Client typed messages) ----------------

class ReceiverThread(QThread):
    frame_received = pyqtSignal(QImage)
    clipboard_received = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, sock: socket.socket):
        super().__init__()
        self.sock = sock
        self._running = True

    def stop(self):
        self._running = False

    def recv_exact(self, n: int):
        data = b""
        while len(data) < n and self._running:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def run(self):
        try:
            while self._running:
                # [1 byte type][4 bytes length][payload]
                t = self.recv_exact(1)
                if not t:
                    break
                mtype = t  # bytes of length 1

                lb = self.recv_exact(4)
                if not lb:
                    break
                (n,) = struct.unpack("!I", lb)

                payload = self.recv_exact(n)
                if payload is None:
                    break

                if mtype == b'F':
                    img = QImage.fromData(payload, "JPEG")
                    if not img.isNull():
                        self.frame_received.emit(img)

                elif mtype == b'B':
                    text = payload.decode("utf-8", errors="replace")
                    self.clipboard_received.emit(text)

                else:
                    # Unknown message type; stop to avoid desync
                    self.error.emit(f"Unknown server message type: {mtype!r}")
                    break

        except Exception as e:
            self.error.emit(str(e))


# ---------------- Screen label ----------------

class RemoteScreenLabel(QLabel):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")

    def mouseMoveEvent(self, event: QMouseEvent):
        dragging = bool(event.buttons() & (Qt.MouseButton.LeftButton |
                                          Qt.MouseButton.RightButton |
                                          Qt.MouseButton.MiddleButton))
        self.controller.handle_mouse_move(event, dragging=dragging)

    def mousePressEvent(self, event: QMouseEvent):
        self.controller.handle_mouse_press(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.controller.handle_mouse_release(event)

    def wheelEvent(self, event: QWheelEvent):
        d = event.angleDelta()
        self.controller.send_mouse_wheel(int(d.y()), int(d.x()))


# ---------------- Connection dialog ----------------

class ConnectionDialog(QDialog):
    def __init__(self, parent=None, item: ConnectionItem | None = None):
        super().__init__(parent)
        self.setWindowTitle("Connection")

        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Host/IP:", self.host_edit)
        form.addRow("Port:", self.port_spin)

        btns = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        btns.addStretch(1)
        btns.addWidget(self.ok_btn)
        btns.addWidget(self.cancel_btn)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        if item:
            self.name_edit.setText(item.name)
            self.host_edit.setText(item.host)
            self.port_spin.setValue(item.port)

    def get_item(self) -> ConnectionItem | None:
        name = self.name_edit.text().strip()
        host = self.host_edit.text().strip()
        port = int(self.port_spin.value())
        if not name or not host:
            QMessageBox.warning(self, "Invalid", "Name and Host/IP are required.")
            return None
        return ConnectionItem(name=name, host=host, port=port)


# ---------------- Main Window ----------------

class RemoteClient(QMainWindow):
    def __init__(self):
        super().__init__()

        self.sock: socket.socket | None = None
        self.receiver: ReceiverThread | None = None
        self.connected_to: ConnectionItem | None = None

        self.remote_width = None
        self.remote_height = None
        self.last_frame = None
        self.last_mouse_remote = None

        # Clipboard sync loop protection
        self._applying_remote_clipboard = False
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self._on_local_clipboard_changed)

        self.setWindowTitle("Python Remote Control (mini VNC) + Connections + Clipboard")
        self.resize(1300, 750)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        # Left: connections panel
        self.conn_widget = QWidget()
        left_layout = QVBoxLayout(self.conn_widget)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Host", "Port"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.connect_selected)

        row1 = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_edit = QPushButton("Edit")
        self.btn_remove = QPushButton("Remove")
        row1.addWidget(self.btn_add)
        row1.addWidget(self.btn_edit)
        row1.addWidget(self.btn_remove)

        row2 = QHBoxLayout()
        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        row2.addWidget(self.btn_connect)
        row2.addWidget(self.btn_disconnect)

        left_layout.addWidget(self.table, 1)
        left_layout.addLayout(row1)
        left_layout.addLayout(row2)

        self.btn_add.clicked.connect(self.add_connection)
        self.btn_edit.clicked.connect(self.edit_connection)
        self.btn_remove.clicked.connect(self.remove_connection)
        self.btn_connect.clicked.connect(self.connect_selected)
        self.btn_disconnect.clicked.connect(self.disconnect_current)

        # Right: screen
        self.screen_label = RemoteScreenLabel(self)
        self.screen_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        splitter.addWidget(self.conn_widget)
        splitter.addWidget(self.screen_label)
        splitter.setSizes([350, 950])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("Disconnected")

        self.connections = load_connections()
        self.refresh_table()

    # ---------- Connections UI ----------

    def refresh_table(self):
        self.table.setRowCount(0)
        for item in self.connections:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(item.name))
            self.table.setItem(r, 1, QTableWidgetItem(item.host))
            self.table.setItem(r, 2, QTableWidgetItem(str(item.port)))
        self.table.resizeColumnsToContents()

    def selected_index(self):
        sel = self.table.selectionModel().selectedRows()
        return sel[0].row() if sel else None

    def add_connection(self):
        dlg = ConnectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item = dlg.get_item()
            if item:
                self.connections.append(item)
                save_connections(self.connections)
                self.refresh_table()

    def edit_connection(self):
        idx = self.selected_index()
        if idx is None:
            return
        dlg = ConnectionDialog(self, self.connections[idx])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item = dlg.get_item()
            if item:
                self.connections[idx] = item
                save_connections(self.connections)
                self.refresh_table()

    def remove_connection(self):
        idx = self.selected_index()
        if idx is None:
            return
        item = self.connections[idx]
        if self.connected_to and item == self.connected_to:
            QMessageBox.warning(self, "Busy", "Disconnect before removing the active connection.")
            return
        self.connections.pop(idx)
        save_connections(self.connections)
        self.refresh_table()

    def connect_selected(self):
        idx = self.selected_index()
        if idx is None:
            return
        self.connect_to(self.connections[idx])

    # ---------- Connect / Disconnect ----------

    def connect_to(self, item: ConnectionItem):
        if self.sock:
            self.disconnect_current()

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((item.host, item.port))
            s.settimeout(None)
        except Exception as e:
            QMessageBox.warning(self, "Connection failed", f"Could not connect to {item.host}:{item.port}\n{e}")
            return

        self.sock = s
        self.connected_to = item

        self.receiver = ReceiverThread(s)
        self.receiver.frame_received.connect(self.on_frame)
        self.receiver.clipboard_received.connect(self.on_remote_clipboard)
        self.receiver.error.connect(self.on_error)
        self.receiver.start()

        self.statusBar().showMessage(f"Connected to {item.name} ({item.host}:{item.port})")

    def disconnect_current(self):
        if self.receiver:
            self.receiver.stop()
            try:
                self.receiver.wait(500)
            except Exception:
                pass
            self.receiver = None

        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        self.connected_to = None
        self.remote_width = None
        self.remote_height = None
        self.last_frame = None
        self.last_mouse_remote = None
        self.screen_label.clear()
        self.statusBar().showMessage("Disconnected")

    # ---------- Send helpers (Client->Server events) ----------

    def _send(self, payload: bytes):
        if not self.sock:
            return
        try:
            self.sock.sendall(payload)
        except Exception as e:
            print(f"[CLIENT] send error: {e}")

    def send_mouse_move(self, x, y):
        self._send(struct.pack("!cii", b'M', int(x), int(y)))

    def send_mouse_down(self, button):
        self._send(struct.pack("!cB", b'P', int(button)))

    def send_mouse_up(self, button):
        self._send(struct.pack("!cB", b'R', int(button)))

    def send_mouse_wheel(self, vert, horiz):
        self._send(struct.pack("!cii", b'W', int(vert), int(horiz)))

    def send_key_name(self, name: str):
        name_bytes = name.encode("ascii", errors="ignore")
        if not name_bytes:
            return
        if len(name_bytes) > 255:
            name_bytes = name_bytes[:255]
        self._send(struct.pack("!cB", b'K', len(name_bytes)) + name_bytes)

    def send_clipboard_text(self, text: str):
        data = text.encode("utf-8", errors="replace")
        self._send(struct.pack("!cI", b'B', len(data)) + data)

    # ---------- Frame handling ----------

    def on_frame(self, img: QImage):
        self.last_frame = img
        self.remote_width = img.width()
        self.remote_height = img.height()
        self.update_label_pixmap()

    def update_label_pixmap(self):
        if self.last_frame is None:
            return
        label_size = self.screen_label.size()
        pix = QPixmap.fromImage(self.last_frame).scaled(
            label_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.screen_label.setPixmap(pix)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.update_label_pixmap()

    def on_error(self, msg: str):
        QMessageBox.warning(self, "Connection error", msg)
        self.disconnect_current()

    # ---------- Mouse mapping ----------

    def _map_to_remote(self, event: QMouseEvent):
        if self.remote_width is None or self.remote_height is None or self.last_frame is None:
            return None

        label_w = self.screen_label.width()
        label_h = self.screen_label.height()
        if label_w <= 0 or label_h <= 0:
            return None

        rw, rh = self.remote_width, self.remote_height
        scale = min(label_w / rw, label_h / rh)
        img_w = rw * scale
        img_h = rh * scale
        off_x = (label_w - img_w) / 2
        off_y = (label_h - img_h) / 2

        x = event.position().x()
        y = event.position().y()
        if x < off_x or x > off_x + img_w or y < off_y or y > off_y + img_h:
            return None

        local_x = x - off_x
        local_y = y - off_y
        return int(local_x / scale), int(local_y / scale)

    def handle_mouse_move(self, event: QMouseEvent, dragging: bool = False):
        if not self.sock:
            return
        mapped = self._map_to_remote(event)
        if mapped is None:
            return
        rx, ry = mapped

        threshold = 0 if dragging else 2
        if self.last_mouse_remote is not None:
            lx, ly = self.last_mouse_remote
            if abs(rx - lx) < threshold and abs(ry - ly) < threshold:
                return

        self.last_mouse_remote = (rx, ry)
        self.send_mouse_move(rx, ry)

    def handle_mouse_press(self, event: QMouseEvent):
        if not self.sock:
            return
        mapped = self._map_to_remote(event)
        if mapped is None:
            return
        rx, ry = mapped
        self.last_mouse_remote = (rx, ry)
        self.send_mouse_move(rx, ry)

        if event.button() == Qt.MouseButton.LeftButton:
            self.send_mouse_down(1)
        elif event.button() == Qt.MouseButton.RightButton:
            self.send_mouse_down(2)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.send_mouse_down(3)

    def handle_mouse_release(self, event: QMouseEvent):
        if not self.sock:
            return
        mapped = self._map_to_remote(event)
        if mapped is not None:
            rx, ry = mapped
            self.last_mouse_remote = (rx, ry)
            self.send_mouse_move(rx, ry)

        if event.button() == Qt.MouseButton.LeftButton:
            self.send_mouse_up(1)
        elif event.button() == Qt.MouseButton.RightButton:
            self.send_mouse_up(2)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.send_mouse_up(3)

    # ---------- Keyboard ----------

    def keyPressEvent(self, event: QKeyEvent):
        name = self.build_key_name(event)
        if name:
            self.send_key_name(name)

    def build_key_name(self, event: QKeyEvent):
        mods = []
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            mods.append("ctrl")
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            mods.append("alt")
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            mods.append("shift")

        base = ""
        text = event.text()
        if text and text.isprintable():
            base = text.lower()
            if base == " ":
                base = "space"
        else:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                base = "enter"
            elif 0 <= key <= 1114111:
                base = chr(key)
            elif key == Qt.Key.Key_Escape:
                base = "esc"
            elif key == Qt.Key.Key_Tab:
                base = "tab"
            elif key == Qt.Key.Key_Backspace:
                base = "backspace"
            elif key == Qt.Key.Key_Delete:
                base = "delete"
            elif key == Qt.Key.Key_Left:
                base = "left"
            elif key == Qt.Key.Key_Right:
                base = "right"
            elif key == Qt.Key.Key_Up:
                base = "up"
            elif key == Qt.Key.Key_Down:
                base = "down"
            elif key == Qt.Key.Key_Home:
                base = "home"
            elif key == Qt.Key.Key_End:
                base = "end"
            elif key == Qt.Key.Key_PageUp:
                base = "pageup"
            elif key == Qt.Key.Key_PageDown:
                base = "pagedown"
            elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
                base = f"f{key - Qt.Key.Key_F1 + 1}"

        if not base:
            return None
        return "+".join(mods + [base]) if mods else base

    # ---------- Clipboard sync ----------

    def _on_local_clipboard_changed(self):
        """
        When local clipboard changes, push to server.
        Avoid echo loop when we are applying remote clipboard.
        """
        if self._applying_remote_clipboard:
            return
        if not self.sock:
            return

        text = self.clipboard.text()
        if text is None:
            text = ""
        self.send_clipboard_text(text)

    def on_remote_clipboard(self, text: str):
        """
        When server clipboard arrives, set local clipboard (avoid loop).
        """
        try:
            self._applying_remote_clipboard = True
            # only set if different to reduce noise
            if self.clipboard.text() != text:
                self.clipboard.setText(text)
        finally:
            self._applying_remote_clipboard = False

    def closeEvent(self, event):
        self.disconnect_current()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = RemoteClient()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
