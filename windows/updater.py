"""
HR_Edit update checker and applier.

Checks GitHub raw VERSION file, then updates via git pull + pip install.
No extra dependencies — uses only stdlib (urllib, subprocess, pathlib).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent

VERSION_FILE = PROJECT_ROOT / "VERSION"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
REQUIREMENTS_LAUNCHER = HERE / "requirements-launcher.txt"

VENV_PIP = PROJECT_ROOT / ".venv" / "Scripts" / "pip.exe"

REMOTE_VERSION_URL = (
    "https://raw.githubusercontent.com/MirTrudMike/HR_Edit/main/VERSION"
)


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def get_local_version() -> str:
    """Read VERSION from project root. Returns '0.0.0' on any error."""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def get_remote_version(timeout: int = 10) -> str | None:
    """
    Fetch the latest version string from GitHub.
    Returns None on network error or unexpected response.
    """
    try:
        with urlopen(REMOTE_VERSION_URL, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8").strip()
            # Sanity check: should look like a version number
            if raw and all(c.isdigit() or c == "." for c in raw):
                return raw
            return None
    except (URLError, OSError, ValueError):
        return None


def is_newer(remote: str, local: str) -> bool:
    """Return True if remote version is strictly greater than local."""
    def _parse(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)

    return _parse(remote) > _parse(local)


# ---------------------------------------------------------------------------
# Update runner
# ---------------------------------------------------------------------------

def run_update() -> tuple[bool, str]:
    """
    Pull latest code from GitHub and install any new Python dependencies.

    Returns (success, error_message). On success, error_message is empty.
    The caller is responsible for restarting the server / launcher.
    """
    git = _find_git()
    if git is None:
        return False, "git not found in PATH"

    # Step 1: git pull origin main
    try:
        result = subprocess.run(
            [git, "pull", "origin", "main"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "git pull timed out (120 s)"
    except OSError as exc:
        return False, f"cannot run git: {exc}"

    # Step 2: update main requirements
    pip = str(VENV_PIP) if VENV_PIP.exists() else "pip"
    ok, msg = _pip_install(pip, REQUIREMENTS_FILE)
    if not ok:
        return False, msg

    # Step 3: update launcher requirements (pystray, Pillow)
    if REQUIREMENTS_LAUNCHER.exists():
        ok, msg = _pip_install(pip, REQUIREMENTS_LAUNCHER)
        if not ok:
            return False, msg

    return True, ""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _pip_install(pip: str, req_file: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [pip, "install", "-r", str(req_file), "--quiet"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"pip install timed out for {req_file.name}"
    except OSError as exc:
        return False, f"cannot run pip: {exc}"


def _find_git() -> str | None:
    """Return 'git' if available in PATH, else None."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            timeout=5,
        )
        return "git"
    except (OSError, subprocess.TimeoutExpired):
        return None
