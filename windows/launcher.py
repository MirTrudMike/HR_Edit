"""
HR_Edit — system tray launcher for the local web server.

Tray menu:
  Start   — launch run.py and open the browser
  Open    — open the browser (active only when server is running)
  Stop    — kill the server process (tray stays alive)
  Update  — pull latest from GitHub and restart  [placeholder]
"""
from __future__ import annotations

import subprocess
import sys
import time
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent

# On Windows the installer places the venv next to the project root.
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
ENTRY_POINT = PROJECT_ROOT / "run.py"

SERVER_URL = "http://127.0.0.1:8765"

# ---------------------------------------------------------------------------
# Icons  (16x16 circles: grey = stopped, green = running)
# ---------------------------------------------------------------------------

def _make_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=color)
    return img


ICON_STOPPED = _make_icon("#888888")
ICON_RUNNING = _make_icon("#3dba5f")

# ---------------------------------------------------------------------------
# Server process management
# ---------------------------------------------------------------------------

_proc: subprocess.Popen | None = None
_proc_lock = threading.Lock()


def _is_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _start_server() -> None:
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return  # already running

        python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
        _proc = subprocess.Popen(
            [str(python), str(ENTRY_POINT)],
            cwd=str(PROJECT_ROOT),
            # detach from the launcher's console so no black window appears
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def _stop_server() -> None:
    global _proc
    with _proc_lock:
        if _proc is None:
            return
        try:
            _proc.terminate()
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
        _proc = None


# ---------------------------------------------------------------------------
# Menu callbacks
# ---------------------------------------------------------------------------

def _wait_and_open(icon: pystray.Icon) -> None:
    """Wait briefly for the server to start, then open the browser."""
    for _ in range(20):          # up to 10 s
        time.sleep(0.5)
        if _is_running():
            break
    time.sleep(1.5)              # let uvicorn bind the port
    webbrowser.open(SERVER_URL)
    _refresh_menu(icon)


def _on_start(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    if _is_running():
        webbrowser.open(SERVER_URL)
        return
    _start_server()
    threading.Thread(target=_wait_and_open, args=(icon,), daemon=True).start()


def _on_open(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    webbrowser.open(SERVER_URL)


def _on_stop(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    _stop_server()
    _refresh_menu(icon)


def _on_update(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    # Placeholder — update logic will be implemented in a future version.
    pass


# ---------------------------------------------------------------------------
# Dynamic menu rebuild
# ---------------------------------------------------------------------------

def _build_menu(icon: pystray.Icon) -> pystray.Menu:
    running = _is_running()
    return pystray.Menu(
        pystray.MenuItem(
            "Запустить" if not running else "Запущен ✓",
            _on_start,
            enabled=not running,
            default=True,
        ),
        pystray.MenuItem(
            "Открыть",
            _on_open,
            enabled=running,
        ),
        pystray.MenuItem(
            "Остановить",
            _on_stop,
            enabled=running,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Обновить", _on_update),
    )


def _refresh_menu(icon: pystray.Icon) -> None:
    icon.icon = ICON_RUNNING if _is_running() else ICON_STOPPED
    icon.menu = _build_menu(icon)


# ---------------------------------------------------------------------------
# Heartbeat: update icon/menu if the server dies unexpectedly
# ---------------------------------------------------------------------------

def _heartbeat(icon: pystray.Icon) -> None:
    prev = None
    while True:
        time.sleep(2)
        curr = _is_running()
        if curr != prev:
            _refresh_menu(icon)
            prev = curr


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    icon = pystray.Icon(
        name="HR_Edit",
        icon=ICON_STOPPED,
        title="HR_Edit",
    )
    icon.menu = _build_menu(icon)

    threading.Thread(target=_heartbeat, args=(icon,), daemon=True).start()

    icon.run()


if __name__ == "__main__":
    main()
