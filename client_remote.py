import socket
import struct
import sys

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QResizeEvent, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QMessageBox

SERVER_IP = "10.50.163.66"
PORT = 5000


class ReceiverThread(QThread):
    frame_received = pyqtSignal(QImage)
    error = pyqtSignal(str)

    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self._running = True

    def stop(self):
        self._running = False

    def recv_exact(self, n):
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


class RemoteClient(QMainWindow):
    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self.receiver = ReceiverThread(sock)
        self.receiver.frame_received.connect(self.on_frame)
        self.receiver.error.connect(self.on_error)

        self.remote_width = None
        self.remote_height = None
        self.last_frame = None  # QImage

        # for throttling mouse moves
        self.last_mouse_remote = None  # (x, y)

        self.screen_label = RemoteScreenLabel(self)
        self.setCentralWidget(self.screen_label)

        self.setWindowTitle("Python Remote Control (mini VNC)")
        self.resize(1200, 700)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screen_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.screen_label.setFocus()

        self.receiver.start()

    # --------- send helpers ----------

    def send_mouse_move(self, x, y):
        try:
            msg = struct.pack("!cii", b'M', int(x), int(y))
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_mouse_move error: {e}")

    def send_mouse_click(self, button=1):
        try:
            msg = struct.pack("!cB", b'C', button)
            self.sock.sendall(msg)
        except Exception as e:
            print(f"[CLIENT] send_mouse_click error: {e}")

    def send_key_name(self, name: str):
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

    # --------- frame handling --------

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

    # --------- mouse mapping ---------

    def handle_mouse_event(self, event: QMouseEvent, move_only: bool):
        if self.remote_width is None or self.remote_height is None:
            return
        if self.last_frame is None:
            return

        label_w = self.screen_label.width()
        label_h = self.screen_label.height()
        if label_w <= 0 or label_h <= 0:
            return

        # aspect-ratio aware mapping: active image area inside label
        remote_w = self.remote_width
        remote_h = self.remote_height
        scale = min(label_w / remote_w, label_h / remote_h)
        img_w = remote_w * scale
        img_h = remote_h * scale
        offset_x = (label_w - img_w) / 2
        offset_y = (label_h - img_h) / 2

        x = event.position().x()
        y = event.position().y()

        # ignore clicks on black borders
        if x < offset_x or x > offset_x + img_w or y < offset_y or y > offset_y + img_h:
            return

        local_x = x - offset_x
        local_y = y - offset_y

        remote_x = int(local_x / scale)
        remote_y = int(local_y / scale)

        # throttle positional spam when just moving
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

    # --------- keyboard & shortcuts ---------

    def keyPressEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            # optional: ignore repeats
            pass

        combo_name = self.build_key_name(event)
        if combo_name:
            print("[CLIENT] key ->", combo_name)
            self.send_key_name(combo_name)

        if combo_name == 'q':
            pass
            #self.close()

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
            if base == ' ':
                base = 'space'
        else:
            key = event.key()

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                base = "enter"
            elif key == Qt.Key.Key_Escape:
                base = "esc"
            elif 0 <= key <= 1114111:
                base = chr(key)
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

        if mods:
            return "+".join(mods + [base])
        return base

    def closeEvent(self, event):
        self.receiver.stop()
        try:
            self.sock.close()
        except:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, PORT))
    print(f"[CLIENT] Connected to {SERVER_IP}:{PORT}")

    win = RemoteClient(sock)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()