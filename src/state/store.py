from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import STATE_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    """Thread-safe JSON-backed registry of all processed archives."""

    def __init__(self, path: Path = STATE_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"archives": {}}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_processed(self, name: str) -> bool:
        with self._lock:
            rec = self._data["archives"].get(name)
            return rec is not None and rec.get("status") in {"ready", "processing"}

    def get(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._data["archives"].get(name)
            return dict(rec) if rec is not None else None

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [dict(v) for v in self._data["archives"].values()]
        records.sort(key=lambda r: r.get("processed_at") or "", reverse=True)
        return records

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_processing(self, name: str, archive_path: str, downloaded_at: str, kind: str = "archive") -> None:
        with self._lock:
            self._data["archives"][name] = {
                "name": name,
                "kind": kind,
                "archive_name": Path(archive_path).name,
                "archive_path": archive_path,
                "out_dir": "",
                "downloaded_at": downloaded_at,
                "processed_at": None,
                "last_opened_at": None,
                "is_new": False,
                "count": 0,
                "progress_done": 0,
                "progress_total": 0,
                "status": "processing",
                "error": None,
            }
            self._save()

    def update_progress(self, name: str, done: int, total: int) -> None:
        with self._lock:
            rec = self._data["archives"].get(name)
            if rec is not None and rec.get("status") == "processing":
                rec["progress_done"] = done
                rec["progress_total"] = total
                self._save()

    def mark_ready(self, name: str, out_dir: str, count: int) -> None:
        with self._lock:
            rec = self._data["archives"].get(name, {"name": name})
            rec.update({
                "out_dir": out_dir,
                "processed_at": _now_iso(),
                "is_new": True,
                "count": count,
                "status": "ready",
                "error": None,
            })
            self._data["archives"][name] = rec
            self._save()

    def mark_error(self, name: str, error: str) -> None:
        with self._lock:
            rec = self._data["archives"].get(name, {"name": name})
            rec.update({"status": "error", "error": error})
            self._data["archives"][name] = rec
            self._save()

    def mark_opened(self, name: str) -> None:
        with self._lock:
            rec = self._data["archives"].get(name)
            if rec is not None:
                rec["last_opened_at"] = _now_iso()
                rec["is_new"] = False
                self._save()

    def add_from_manifest(self, name: str, manifest: dict[str, Any]) -> None:
        """Import an existing manifest produced by a previous run."""
        with self._lock:
            if name in self._data["archives"]:
                return
            self._data["archives"][name] = {
                "name": name,
                "archive_name": Path(manifest.get("archive", "")).name,
                "archive_path": manifest.get("archive", ""),
                "out_dir": manifest.get("out_dir", ""),
                "downloaded_at": manifest.get("processed_at"),
                "processed_at": manifest.get("processed_at"),
                "last_opened_at": None,
                "is_new": False,
                "count": manifest.get("count", 0),
                "status": "ready",
                "error": None,
            }
            self._save()

    def rename(self, name: str, display_name: str) -> None:
        """Set a human-friendly display name; the original name (ID) is unchanged."""
        with self._lock:
            rec = self._data["archives"].get(name)
            if rec is not None:
                rec["display_name"] = display_name
                self._save()

    def delete(self, name: str) -> str | None:
        """Remove archive record; returns out_dir so caller can clean up files."""
        with self._lock:
            rec = self._data["archives"].pop(name, None)
            if rec is not None:
                self._save()
                return rec.get("out_dir") or ""
            return None
