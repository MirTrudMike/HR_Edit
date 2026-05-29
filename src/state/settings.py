from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from src.config import SETTINGS_FILE

_DEFAULTS: dict[str, Any] = {"describe_diagrams": True}


class SettingsStore:
    """Thread-safe JSON-backed store for user-facing processing settings."""

    def __init__(self, path: Path = SETTINGS_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                on_disk = json.loads(self._path.read_text(encoding="utf-8"))
                return {**_DEFAULTS, **on_disk}
            except Exception:
                pass
        return dict(_DEFAULTS)

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_describe_diagrams(self) -> bool:
        with self._lock:
            return bool(self._data.get("describe_diagrams", True))

    def set_describe_diagrams(self, value: bool) -> None:
        with self._lock:
            self._data["describe_diagrams"] = value
            self._save()

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)
