
import socket
import struct
import io
import threading
from mss import mss
from PIL import Image
import pyautogui
import time

HOST = "0.0.0.0"
PORT = 5000

# Fast interaction
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def recv_exact(conn, n, stop_event: threading.Event):
    data = b""
    while len(data) < n and not stop_event.is_set():
        try:
            chunk = conn.recv(n - len(data))
        except (ConnectionResetError, OSError):
            return None
        if not chunk:
            return None
        data += chunk
    return data


def send_frames(conn, stop_event: threading.Event, fps_limit=30):
    """Continuously capture and send frames until stop_event is set or socket fails."""
    sct = mss()
    min_frame_time = 1.0 / fps_limit if fps_limit else 0.0

    try:
        while not stop_event.is_set():
            start = time.time()

            screenshot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            data = buf.getvalue()

            try:
                conn.sendall(struct.pack("!I", len(data)) + data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

            # FPS throttle (reduces CPU/network load)
            elapsed = time.time() - start
            remaining = min_frame_time - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except Exception as e:
        print(f"[SERVER] send_frames ended: {e}")
    finally:
        stop_event.set()


def handle_input(conn, stop_event: threading.Event):
    """
    Protocol:
    - 'M' + int32 x + int32 y -> move mouse
    - 'C' + uint8 button -> click (1=left, 2=right, 3=middle)
    - 'W' + int32 vertical + int32 horizontal -> wheel scroll
    - 'K' + uint8 len + bytes(name) -> key/hotkey ("a", "enter", "ctrl+z")
    """
    try:
        while not stop_event.is_set():
            etype = recv_exact(conn, 1, stop_event)
            if not etype:
                break

            etype = etype[0]

            if etype == ord('M'):
                data = recv_exact(conn, 8, stop_event)
                if not data:
                    break
                x, y = struct.unpack("!ii", data)
                pyautogui.moveTo(x, y, duration=0)

            elif etype == ord('C'):
                btn_bytes = recv_exact(conn, 1, stop_event)
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
                data = recv_exact(conn, 8, stop_event)
                if not data:
                    break
                vert, horiz = struct.unpack("!ii", data)
                if vert != 0:
                    pyautogui.scroll(vert)
                if horiz != 0:
                    try:
                        pyautogui.hscroll(horiz)
                    except AttributeError:
                        pass

            elif etype == ord('K'):
                len_bytes = recv_exact(conn, 1, stop_event)
                if not len_bytes:
                    break
                (name_len,) = struct.unpack("!B", len_bytes)
                key_bytes = recv_exact(conn, name_len, stop_event)
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
    finally:
        stop_event.set()


def handle_client(conn, addr):
    print(f"[SERVER] Connected by {addr}")
    stop_event = threading.Event()

    t1 = threading.Thread(target=send_frames, args=(conn, stop_event), daemon=True)
    t2 = threading.Thread(target=handle_input, args=(conn, stop_event), daemon=True)
    t1.start()
    t2.start()

    # Wait until either thread ends, then stop the other
    while not stop_event.is_set():
        time.sleep(0.05)

    # Give threads a short chance to exit gracefully
    t1.join(timeout=1)
    t2.join(timeout=1)

    try:
        conn.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass

    print("[SERVER] Client disconnected")


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, PORT))
        server_sock.listen(5)
        print(f"[SERVER] Listening on {HOST}:{PORT}")

        while True:
            try:
                conn, addr = server_sock.accept()
                handle_client(conn, addr)
                # After client disconnects, loop continues and server stays alive
            except KeyboardInterrupt:
                print("\n[SERVER] Shutting down (KeyboardInterrupt)")
                break
            except Exception as e:
                print(f"[SERVER] Accept/handle error: {e}")


if __name__ == "__main__":
    main()
