"""
HR_Edit — system tray launcher for the local web server.

Tray menu:
  Start   — launch run.py and open the browser
  Open    — open the browser (active only when server is running)
  Stop    — kill the server process (tray stays alive)
  ----
  <update item> — idle / checking / update available / updating / error
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import IO, Optional

import pystray
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Single-instance guard: exit silently if another copy is already running
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    _MUTEX_NAME = "Global\\HR_Edit_SingleInstance"
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent

VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"   # python.exe for the server subprocess
ENTRY_POINT = PROJECT_ROOT / "run.py"
LOG_FILE = PROJECT_ROOT / "work" / "server.log"

SERVER_URL = "http://127.0.0.1:8765"

STARTUP_CHECK_DELAY = 8      # seconds after launch before first version check
AUTO_CHECK_INTERVAL = 6 * 3600  # repeat check every 6 hours

# ---------------------------------------------------------------------------
# Tray icons  (64×64 circles: grey = stopped, green = running)
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

_proc: Optional[subprocess.Popen] = None
_log_fh: Optional[IO[str]] = None
_proc_lock = threading.Lock()


def _is_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _start_server() -> None:
    global _proc, _log_fh
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return
        python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _log_fh = open(LOG_FILE, "a", encoding="utf-8")
        _proc = subprocess.Popen(
            [str(python), str(ENTRY_POINT)],
            cwd=str(PROJECT_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=_log_fh,
            stderr=_log_fh,
            stdin=subprocess.DEVNULL,
        )


def _stop_server() -> None:
    global _proc, _log_fh
    with _proc_lock:
        if _proc is None:
            return
        try:
            _proc.terminate()
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
        _proc = None
        if _log_fh is not None:
            try:
                _log_fh.close()
            except Exception:
                pass
            _log_fh = None


# ---------------------------------------------------------------------------
# Update state machine
#
# States:
#   None        — not checked yet  →  "Проверить обновления"
#   "checking"  — request in flight →  "Проверяю..." (disabled)
#   "available" — new version found →  "Обновить до X.Y.Z"
#   "up_to_date"— already latest   →  "Всё актуально ✓" (disabled, then resets)
#   "updating"  — pull in progress  →  "Обновляю..." (disabled)
#   "error"     — network / git err →  "Ошибка — попробовать снова"
# ---------------------------------------------------------------------------

_update_state: Optional[str] = None
_available_version: str = ""
_update_state_lock = threading.Lock()
_check_mutex = threading.Lock()   # prevents concurrent version checks


def _set_state(icon: pystray.Icon, state: Optional[str], version: str = "") -> None:
    global _update_state, _available_version
    with _update_state_lock:
        _update_state = state
        _available_version = version
    _refresh_menu(icon)


def _check_version_bg(icon: pystray.Icon) -> None:
    """Run version check in the calling thread. Use _check_mutex to prevent races."""
    if not _check_mutex.acquire(blocking=False):
        return  # check already in progress
    try:
        _set_state(icon, "checking")
        import updater
        remote = updater.get_remote_version()
        if remote is None:
            _set_state(icon, "error")
            return
        local = updater.get_local_version()
        if updater.is_newer(remote, local):
            _set_state(icon, "available", remote)
        else:
            _set_state(icon, "up_to_date")
            # Show "up to date" briefly, then return to default idle state
            time.sleep(4)
            with _update_state_lock:
                still_same = (_update_state == "up_to_date")
            if still_same:
                _set_state(icon, None)
    except Exception:
        _set_state(icon, "error")
    finally:
        _check_mutex.release()


def _do_update_bg(icon: pystray.Icon) -> None:
    """Pull latest code, reinstall deps, then restart the launcher."""
    _set_state(icon, "updating")

    was_running = _is_running()
    if was_running:
        _stop_server()

    try:
        import updater
        success, err = updater.run_update()
    except Exception as exc:
        success, err = False, str(exc)

    if not success:
        # On failure: restore server and show error
        _set_state(icon, "error")
        if was_running:
            _start_server()
        return

    # Success — spawn a fresh launcher instance, then exit this one
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    subprocess.Popen(
        [str(python), str(HERE / "launcher.py")],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    icon.stop()  # exits pystray event loop → process ends


def _auto_check_loop(icon: pystray.Icon) -> None:
    """Background loop: first check after startup, then every AUTO_CHECK_INTERVAL."""
    time.sleep(STARTUP_CHECK_DELAY)
    while True:
        with _update_state_lock:
            state = _update_state
        # Skip if a check is already running, update is available, or install is in progress
        if state not in ("checking", "available", "updating"):
            _check_version_bg(icon)
        time.sleep(AUTO_CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# Menu callbacks
# ---------------------------------------------------------------------------

def _wait_and_open(icon: pystray.Icon) -> None:
    """Wait for the server port to bind, then open the browser."""
    for _ in range(20):
        time.sleep(0.5)
        if _is_running():
            break
    time.sleep(1.5)
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


def _on_check_update(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    threading.Thread(target=_check_version_bg, args=(icon,), daemon=True).start()


def _on_do_update(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    threading.Thread(target=_do_update_bg, args=(icon,), daemon=True).start()


def _on_exit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
    _stop_server()
    icon.stop()


# ---------------------------------------------------------------------------
# Dynamic menu construction
# ---------------------------------------------------------------------------

def _build_update_item() -> pystray.MenuItem:
    with _update_state_lock:
        state = _update_state
        version = _available_version

    _noop = lambda i, it: None  # noqa: E731

    if state == "checking":
        return pystray.MenuItem("Проверяю обновления...", _noop, enabled=False)
    if state == "available":
        return pystray.MenuItem(f"Обновить до {version}", _on_do_update)
    if state == "up_to_date":
        return pystray.MenuItem("Всё актуально ✓", _noop, enabled=False)
    if state == "updating":
        return pystray.MenuItem("Обновляю...", _noop, enabled=False)
    if state == "error":
        return pystray.MenuItem("Ошибка — попробовать снова", _on_check_update)
    # Default idle state
    return pystray.MenuItem("Проверить обновления", _on_check_update)


def _build_menu(_icon: pystray.Icon) -> pystray.Menu:
    running = _is_running()
    return pystray.Menu(
        pystray.MenuItem(
            "Запустить" if not running else "Запущен ✓",
            _on_start,
            enabled=not running,
            default=True,
        ),
        pystray.MenuItem("Открыть", _on_open, enabled=running),
        pystray.MenuItem("Остановить", _on_stop, enabled=running),
        pystray.Menu.SEPARATOR,
        _build_update_item(),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", _on_exit),
    )


def _refresh_menu(icon: pystray.Icon) -> None:
    icon.icon = ICON_RUNNING if _is_running() else ICON_STOPPED
    icon.menu = _build_menu(icon)


# ---------------------------------------------------------------------------
# Heartbeat: updates icon/menu if the server dies unexpectedly
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
    threading.Thread(target=_auto_check_loop, args=(icon,), daemon=True).start()

    icon.run()


if __name__ == "__main__":
    main()
