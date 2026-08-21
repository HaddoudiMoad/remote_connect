remote_connect
=================

Mini Python remote-control (screen + input + clipboard) with a PyQt6 client and a lightweight server.

Project structure
-----------------
- server_remote.py  - TCP server: captures the primary monitor, streams JPEG frames, handles input and clipboard updates.
- client_remote.py  - PyQt6 GUI client: connects to server, displays remote screen, forwards mouse/keyboard, syncs clipboard, and manages saved connections.
- server_remote.spec - PyInstaller spec file (for building a standalone server binary).

Features
--------
- Real-time screen streaming (JPEG, configurable FPS)
- Client -> server input forwarding: mouse move / click / wheel, keyboard (including hotkeys)
- Bi-directional clipboard sync (text)
- Simple connections list persisted to the user home (~/.remote_connections.json)

Requirements
------------
- Python 3.8+ (tested with 3.8+)
- Packages (installable via pip):
  - PyQt6
  - mss
  - pillow (PIL)
  - pyautogui
  - pyperclip (optional; tkinter is used as fallback for clipboard)

Install
-------
Create a virtual environment and install dependencies:

Windows (PowerShell):

  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install --upgrade pip
  pip install PyQt6 mss pillow pyautogui pyperclip

Linux / macOS (example):

  python -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install PyQt6 mss pillow pyautogui pyperclip

Running
-------
1) Start the server on the machine you want to control:

  python server_remote.py

By default the server listens on 0.0.0.0:5000. Adjust HOST and PORT at the top of server_remote.py if needed.

2) From the client machine, run the GUI client and connect to the server host/IP:

  python client_remote.py

Use the Connections panel to add the server host and connect. The client stores connections in the user home as .remote_connections.json.

Building a standalone server (optional)
-------------------------------------
A PyInstaller spec file is provided. Build with PyInstaller (must be installed):

  pip install pyinstaller
  pyinstaller server_remote.spec

This produces a standalone executable you can deploy. Note: pyautogui and screenshot libraries may require extra runtime dependencies on headless or minimal systems.

Protocol (brief)
----------------
Messages are sent over a TCP connection with simple typed frames:

Server -> Client (typed messages):
- 'F' + uint32 length + JPEG payload  - frame image
- 'B' + uint32 length + UTF-8 text    - clipboard text

Client -> Server (input events):
- 'M' + int32 x + int32 y             - mouse move
- 'P' + uint8 button                  - mouse down (1 left, 2 right, 3 middle)
- 'R' + uint8 button                  - mouse up
- 'C' + uint8 button                  - click
- 'W' + int32 vertical + int32 horiz  - wheel scroll (vertical, horizontal)
- 'K' + uint8 len + bytes(name)       - key or hotkey (e.g. "ctrl+z")
- 'B' + uint32 length + utf8 text     - set server clipboard text

Security and warnings
---------------------
- There is NO authentication or encryption implemented. Use only on trusted networks or via an encrypted tunnel (VPN / SSH port forwarding).
- The server performs real mouse/keyboard actions on the host machine (use carefully).
- Clipboard handling tries pyperclip first then tkinter; both may fail on headless systems.
- PyAutoGUI may require platform-specific dependencies and may behave differently when no interactive desktop is present.

Troubleshooting
---------------
- No frames / blank screen: ensure mss and pillow are installed and that the server has an accessible display (not headless).
- Clipboard not syncing: check pyperclip availability or tkinter support on the server host.
- Permission issues: on some OSes, controlling mouse/keyboard may need elevated privileges.

Extending
---------
- Add authentication (e.g., simple password exchange or TLS) before accepting commands.
- Add optional compression or adjustable JPEG quality/frame size for lower bandwidth.
- Implement multiple concurrent clients with per-client input restrictions.

Credits / Notes
----------------
Created from the code in this repository. For changes or bug reports, edit the source files and submit updates.

License
-------
This project is licensed under the MIT License - see the [LICENSE](</C:/Users/mhaddoudi/OneDrive - TRONICO/Documents/__Codes/remote_connect/LICENSE>) file for details.

Copyright (c) 2026 MOAD HADDOUDI
