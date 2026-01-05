
import socket
import struct
import io
import threading
import time

from mss import mss
from PIL import Image
import pyautogui

HOST = "0.0.0.0"
PORT = 5000

# Fast interaction
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


# ---------------- Clipboard helpers (text) ----------------

def _clipboard_backend():
    """
    Try pyperclip first. If unavailable, fallback to tkinter.
    Returns (get_text, set_text) callables.
    """
    try:
        import pyperclip  # type: ignore

        def get_text():
            try:
                t = pyperclip.paste()
                return "" if t is None else str(t)
            except Exception:
                return ""

        def set_text(text: str):
            try:
                pyperclip.copy(text)
            except Exception:
                pass

        return get_text, set_text
    except Exception:
        # tkinter fallback (may fail on headless/no DISPLAY)
        try:
            import tkinter as tk  # standard lib

            def get_text():
                try:
                    r = tk.Tk()
                    r.withdraw()
                    t = r.clipboard_get()
                    r.destroy()
                    return "" if t is None else str(t)
                except Exception:
                    return ""

            def set_text(text: str):
                try:
                    r = tk.Tk()
                    r.withdraw()
                    r.clipboard_clear()
                    r.clipboard_append(text)
                    r.update()  # keep clipboard after exit
                    r.destroy()
                except Exception:
                    pass

            return get_text, set_text
        except Exception:
            def get_text():
                return ""
            def set_text(text: str):
                pass
            return get_text, set_text


GET_CLIP, SET_CLIP = _clipboard_backend()


# ---------------- Low-level socket helpers ----------------

def recv_exact(conn: socket.socket, n: int, stop_event: threading.Event):
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


def send_typed(conn: socket.socket, send_lock: threading.Lock, mtype: bytes, payload: bytes):
    """
    Server->Client typed message:
    [1 byte type][uint32 length][payload]
    """
    header = struct.pack("!cI", mtype, len(payload))
    with send_lock:
        conn.sendall(header + payload)


def _btn_code_to_name(code: int) -> str:
    return {1: "left", 2: "right", 3: "middle"}.get(code, "left")


# ---------------- Server threads ----------------

def send_frames(conn: socket.socket, stop_event: threading.Event, send_lock: threading.Lock, fps_limit: int = 30):
    sct = mss()
    min_frame_time = 1.0 / fps_limit if fps_limit and fps_limit > 0 else 0.0

    try:
        while not stop_event.is_set():
            t0 = time.time()

            screenshot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=50)
            jpeg = buf.getvalue()

            try:
                send_typed(conn, send_lock, b'F', jpeg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

            elapsed = time.time() - t0
            remaining = min_frame_time - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except Exception as e:
        print(f"[SERVER] send_frames ended: {e}")
    finally:
        stop_event.set()


def clipboard_monitor(conn: socket.socket, stop_event: threading.Event, send_lock: threading.Lock, poll_s: float = 0.35):
    """
    Poll server clipboard text and push to client if changed.
    """
    last = None
    try:
        while not stop_event.is_set():
            current = GET_CLIP()
            if current != last:
                last = current
                try:
                    send_typed(conn, send_lock, b'B', current.encode("utf-8", errors="replace"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
            time.sleep(poll_s)
    except Exception as e:
        print(f"[SERVER] clipboard_monitor ended: {e}")
    finally:
        stop_event.set()


def handle_input(conn: socket.socket, stop_event: threading.Event):
    """
    Client->Server input protocol:
    - 'M' + int32 x + int32 y                   -> mouse move
    - 'P' + uint8 button                       -> mouse down
    - 'R' + uint8 button                       -> mouse up
    - 'C' + uint8 button                       -> click
    - 'W' + int32 vertical + int32 horizontal   -> wheel scroll
    - 'K' + uint8 len + bytes(name)            -> key/hotkey ("ctrl+z")
    - 'B' + uint32 length + utf8 text          -> set server clipboard text
    """
    try:
        while not stop_event.is_set():
            etype = recv_exact(conn, 1, stop_event)
            if not etype:
                break
            et = etype[0]

            if et == ord('M'):
                data = recv_exact(conn, 8, stop_event)
                if not data:
                    break
                x, y = struct.unpack("!ii", data)
                pyautogui.moveTo(x, y, duration=0)

            elif et == ord('C'):
                b = recv_exact(conn, 1, stop_event)
                if not b:
                    break
                pyautogui.click(button=_btn_code_to_name(b[0]))

            elif et == ord('P'):
                b = recv_exact(conn, 1, stop_event)
                if not b:
                    break
                pyautogui.mouseDown(button=_btn_code_to_name(b[0]))

            elif et == ord('R'):
                b = recv_exact(conn, 1, stop_event)
                if not b:
                    break
                pyautogui.mouseUp(button=_btn_code_to_name(b[0]))

            elif et == ord('W'):
                data = recv_exact(conn, 8, stop_event)
                if not data:
                    break
                vert, horiz = struct.unpack("!ii", data)
                if vert:
                    pyautogui.scroll(vert)
                if horiz:
                    try:
                        pyautogui.hscroll(horiz)
                    except AttributeError:
                        pass

            elif et == ord('K'):
                lb = recv_exact(conn, 1, stop_event)
                if not lb:
                    break
                (name_len,) = struct.unpack("!B", lb)
                kb = recv_exact(conn, name_len, stop_event)
                if not kb:
                    break
                key_name = kb.decode("ascii", errors="ignore").strip().lower()
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

            elif et == ord('B'):
                # set clipboard from client
                lb = recv_exact(conn, 4, stop_event)
                if not lb:
                    break
                (n,) = struct.unpack("!I", lb)
                tb = recv_exact(conn, n, stop_event)
                if tb is None:
                    break
                text = tb.decode("utf-8", errors="replace")
                SET_CLIP(text)

            else:
                # Unknown event: stop to avoid desync
                print(f"[SERVER] Unknown input type: {chr(et)} ({et})")
                break

    except Exception as e:
        print(f"[SERVER] handle_input ended: {e}")
    finally:
        stop_event.set()


# ---------------- Client session handler ----------------

def handle_client(conn: socket.socket, addr):
    print(f"[SERVER] Connected by {addr}")
    stop_event = threading.Event()
    send_lock = threading.Lock()

    t_frame = threading.Thread(target=send_frames, args=(conn, stop_event, send_lock, 30), daemon=True)
    t_input = threading.Thread(target=handle_input, args=(conn, stop_event), daemon=True)
    t_clip  = threading.Thread(target=clipboard_monitor, args=(conn, stop_event, send_lock), daemon=True)

    t_frame.start()
    t_input.start()
    t_clip.start()

    while not stop_event.is_set():
        time.sleep(0.05)

    t_frame.join(timeout=1)
    t_input.join(timeout=1)
    t_clip.join(timeout=1)

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
                handle_client(conn, addr)  # one client at a time
            except KeyboardInterrupt:
                print("\n[SERVER] Shutting down")
                break
            except Exception as e:
                print(f"[SERVER] Accept/handle error: {e}")


if __name__ == "__main__":
    main()
