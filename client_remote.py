import socket
import struct
import sys
import json
import os
from dataclasses import dataclass, asdict

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QResizeEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QMessageBox,
    QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDialog, QFormLayout, QLineEdit,
    QSpinBox
)

DEFAULT_PORT = 5000
CONNECTIONS_FILE = os.path.join(os.path.expanduser("~"), ".remote_connections.json")


# ----------------------------- Data model -----------------------------

@dataclass
class ConnectionItem:
    name: str
    host: str
    port: int = DEFAULT_PORT


def load_connections() -> list[ConnectionItem]:
    if not os.path.exists(CONNECTIONS_FILE):
        # Provide a default entry matching your previous hardcoded config
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
    data = [asdict(x) for x in items]
    with open(CONNECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ----------------------------- Receiver thread -----------------------------

class ReceiverThread(QThread):
    frame_received = pyqtSignal(QImage)
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
                size_bytes = self.recv_exact(4)
                if not size_bytes:
                    break
                (size,) = struct.unpack("!I", size_bytes)
                img_bytes = self.recv_exact(size)
                if not img_bytes:
                    break
                img = QImage.fromData(img_bytes, "JPEG")
                if img.isNull():
                    continue
                self.frame_received.emit(img)
        except Exception as e:
            self.error.emit(str(e))


# ----------------------------- Remote screen label -----------------------------

class RemoteScreenLabel(QLabel):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")

    def mouseMoveEvent(self, event: QMouseEvent):
        self.controller.handle_mouse_event(event, move_only=True)

    def mousePressEvent(self, event: QMouseEvent):
        self.controller.handle_mouse_event(event, move_only=False)

    def wheelEvent(self, event: QWheelEvent):
        # Use angleDelta; 120 units typical per notch
        delta = event.angleDelta()
        vert = delta.y()
        horiz = delta.x()
        if vert != 0 or horiz != 0:
            self.controller.send_mouse_wheel(vert, horiz)


# ----------------------------- Connection dialog -----------------------------

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

        btn_row = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addStretch(1)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btn_row)
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


# ----------------------------- Main Window -----------------------------

class RemoteClient(QMainWindow):
    def __init__(self):
        super().__init__()

        # Connection state
        self.sock: socket.socket | None = None
        self.receiver: ReceiverThread | None = None
        self.connected_to: ConnectionItem | None = None

        # Remote frame state
        self.remote_width = None
        self.remote_height = None
        self.last_frame = None  # QImage
        self.last_mouse_remote = None  # (x, y)

        # UI
        self.setWindowTitle("Python Remote Control (mini VNC) + Connections")
        self.resize(1300, 750)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        # Left panel (Connections)
        self.conn_widget = QWidget()
        left_layout = QVBoxLayout(self.conn_widget)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Host", "Port"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.connect_selected)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_edit = QPushButton("Edit")
        self.btn_remove = QPushButton("Remove")
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_remove)

        btns2 = QHBoxLayout()
        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        btns2.addWidget(self.btn_connect)
        btns2.addWidget(self.btn_disconnect)

        left_layout.addWidget(self.table, 1)
        left_layout.addLayout(btns)
        left_layout.addLayout(btns2)

        self.btn_add.clicked.connect(self.add_connection)
        self.btn_edit.clicked.connect(self.edit_connection)
        self.btn_remove.clicked.connect(self.remove_connection)
        self.btn_connect.clicked.connect(self.connect_selected)
        self.btn_disconnect.clicked.connect(self.disconnect_current)

        # Right panel (Screen)
        self.screen_label = RemoteScreenLabel(self)
        self.screen_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        splitter.addWidget(self.conn_widget)
        splitter.addWidget(self.screen_label)
        splitter.setSizes([350, 950])

        self.setCentralWidget(splitter)

        # Load and display connections
        self.connections: list[ConnectionItem] = load_connections()
        self.refresh_table()

    # -------------------- UI helpers --------------------

    def refresh_table(self):
        self.table.setRowCount(0)
        for item in self.connections:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(item.name))
            self.table.setItem(r, 1, QTableWidgetItem(item.host))
            self.table.setItem(r, 2, QTableWidgetItem(str(item.port)))
        self.table.resizeColumnsToContents()

    def selected_index(self) -> int | None:
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        return sel[0].row()

    # -------------------- Connection panel actions --------------------

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
        item = self.connections[idx]
        self.connect_to(item)

    # -------------------- Connect / Disconnect --------------------

    def connect_to(self, item: ConnectionItem):
        # If already connected, disconnect first
        if self.sock is not None:
            self.disconnect_current()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((item.host, item.port))
            sock.settimeout(None)
        except Exception as e:
            QMessageBox.warning(self, "Connection failed", f"Could not connect to {item.host}:{item.port}\n{e}")
            return

        self.sock = sock
        self.connected_to = item

        # Start receiver
        self.receiver = ReceiverThread(sock)
        self.receiver.frame_received.connect(self.on_frame)
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

        # Reset viewer state
        self.remote_width = None
        self.remote_height = None
        self.last_frame = None
        self.last_mouse_remote = None
        self.screen_label.clear()
        self.statusBar().showMessage("Disconnected")

    # -------------------- Sending helpers --------------------

    def send_mouse_move(self, x, y):
        if not self.sock:
            return
        try:
            msg = struct.pack("!cii", b'M', int(x), int(y))
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_mouse_move error: {e}")

    def send_mouse_click(self, button=1):
        if not self.sock:
            return
        try:
            msg = struct.pack("!cB", b'C', button)
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_mouse_click error: {e}")

    def send_mouse_wheel(self, vert: int, horiz: int):
        if not self.sock:
            return
        try:
            msg = struct.pack("!cii", b'W', int(vert), int(horiz))
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_mouse_wheel error: {e}")

    def send_key_name(self, name: str):
        if not self.sock:
            return
        try:
            name_bytes = name.encode("ascii", errors="ignore")
            if not name_bytes:
                return
            if len(name_bytes) > 255:
                name_bytes = name_bytes[:255]
            msg = struct.pack("!cB", b'K', len(name_bytes)) + name_bytes
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_key_name error: {e}")

    # -------------------- Frame handling --------------------

    def on_frame(self, img: QImage):
        self.last_frame = img
        self.remote_width = img.width()
        self.remote_height = img.height()
        self.update_label_pixmap()

    def update_label_pixmap(self):
        if self.last_frame is None:
            return
        label_size = self.screen_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            return
        pix = QPixmap.fromImage(self.last_frame)
        pix = pix.scaled(
            label_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.screen_label.setPixmap(pix)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.update_label_pixmap()

    def on_error(self, msg: str):
        QMessageBox.warning(self, "Connection error", msg)
        self.disconnect_current()

    # -------------------- Mouse mapping (same logic as yours) --------------------

    def handle_mouse_event(self, event: QMouseEvent, move_only: bool):
        if not self.sock:
            return
        if self.remote_width is None or self.remote_height is None:
            return
        if self.last_frame is None:
            return

        label_w = self.screen_label.width()
        label_h = self.screen_label.height()
        if label_w <= 0 or label_h <= 0:
            return

        remote_w = self.remote_width
        remote_h = self.remote_height
        scale = min(label_w / remote_w, label_h / remote_h)
        img_w = remote_w * scale
        img_h = remote_h * scale
        offset_x = (label_w - img_w) / 2
        offset_y = (label_h - img_h) / 2

        x = event.position().x()
        y = event.position().y()

        # Ignore clicks on black borders
        if x < offset_x or x > offset_x + img_w or y < offset_y or y > offset_y + img_h:
            return

        local_x = x - offset_x
        local_y = y - offset_y
        remote_x = int(local_x / scale)
        remote_y = int(local_y / scale)

        # Throttle move spam
        if self.last_mouse_remote is not None and move_only:
            lx, ly = self.last_mouse_remote
            if abs(remote_x - lx) < 2 and abs(remote_y - ly) < 2:
                return

        self.last_mouse_remote = (remote_x, remote_y)
        self.send_mouse_move(remote_x, remote_y)

        if not move_only:
            if event.button() == Qt.MouseButton.LeftButton:
                self.send_mouse_click(1)
            elif event.button() == Qt.MouseButton.RightButton:
                self.send_mouse_click(2)
            elif event.button() == Qt.MouseButton.MiddleButton:
                self.send_mouse_click(3)

    # -------------------- Keyboard & shortcuts --------------------

    def keyPressEvent(self, event: QKeyEvent):
        combo_name = self.build_key_name(event)
        if combo_name:
            self.send_key_name(combo_name)

    def build_key_name(self, event: QKeyEvent) -> str | None:
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
                idx = key - Qt.Key.Key_F1 + 1
                base = f"f{idx}"

        if not base:
            return None
        return "+".join(mods + [base]) if mods else base

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
