# server_full_control_scroll.py
import socket
import struct
import io
import threading

from mss import mss
from PIL import Image
import pyautogui

HOST = "0.0.0.0"
PORT = 5000

# Fast interaction
pyautogui.FAILSAFE = False   # disable top-left failsafe
pyautogui.PAUSE = 0          # no delay between calls


def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def send_frames(conn):
    sct = mss()
    try:
        while True:
            screenshot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            data = buf.getvalue()

            size = len(data)
            conn.sendall(struct.pack("!I", size) + data)
    except Exception as e:
        print(f"[SERVER] send_frames ended: {e}")


def handle_input(conn):
    """
    Protocol:
      - 'M' + int32 x + int32 y                 → move mouse
      - 'C' + uint8 button                      → click (1=left, 2=right, 3=middle)
      - 'W' + int32 vertical + int32 horizontal → wheel scroll
      - 'K' + uint8 len + bytes(name)           → key/hotkey ("a", "enter", "ctrl+z")
    """
    try:
        while True:
            etype = recv_exact(conn, 1)
            if not etype:
                break
            etype = etype[0]

            if etype == ord('M'):
                data = recv_exact(conn, 8)
                if not data:
                    break
                x, y = struct.unpack("!ii", data)
                pyautogui.moveTo(x, y, duration=0)

            elif etype == ord('C'):
                btn_bytes = recv_exact(conn, 1)
                if not btn_bytes:
                    break
                button_code = btn_bytes[0]
                if button_code == 1:
                    pyautogui.click(button='left')
                elif button_code == 2:
                    pyautogui.click(button='right')
                elif button_code == 3:
                    pyautogui.click(button='middle')

            elif etype == ord('W'):
                data = recv_exact(conn, 8)
                if not data:
                    break
                vert, horiz = struct.unpack("!ii", data)
                # Positive = scroll up, negative = scroll down
                if vert != 0:
                    pyautogui.scroll(vert)
                if horiz != 0:
                    try:
                        pyautogui.hscroll(horiz)
                    except AttributeError:
                        # Some platforms may not have hscroll
                        pass

            elif etype == ord('K'):
                len_bytes = recv_exact(conn, 1)
                if not len_bytes:
                    break
                (name_len,) = struct.unpack("!B", len_bytes)
                key_bytes = recv_exact(conn, name_len)
                if not key_bytes:
                    break

                key_name = key_bytes.decode("ascii", errors="ignore").strip().lower()
                if not key_name:
                    continue

                try:
                    if '+' in key_name:
                        parts = [p for p in key_name.split('+') if p]
                        if parts:
                            pyautogui.hotkey(*parts)
                    else:
                        pyautogui.press(key_name)
                except Exception as e:
                    print(f"[SERVER] key error '{key_name}': {e}")

    except Exception as e:
        print(f"[SERVER] handle_input ended: {e}")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, PORT))
        server_sock.listen(1)
        print(f"[SERVER] Listening on {HOST}:{PORT}")

        conn, addr = server_sock.accept()
        print(f"[SERVER] Connected by {addr}")

        with conn:
            t1 = threading.Thread(target=send_frames, args=(conn,), daemon=True)
            t2 = threading.Thread(target=handle_input, args=(conn,), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        print("[SERVER] Connection closed")


if __name__ == "__main__":
    main()