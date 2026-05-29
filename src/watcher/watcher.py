from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING

from src.config import (
    ARCHIVE_RUNS_DIR,
    ARCHIVE_PREFIX,
    CACHE_DIR,
    DEFAULT_MODEL,
    DEFAULT_STRONG_MODEL,
    DOWNLOADS_DIR,
    ESCALATE_THRESHOLD,
    FILE_CHECK_INTERVAL,
    STABILITY_SECS,
)

if TYPE_CHECKING:
    from src.state.store import StateStore
    from src.state.settings import SettingsStore

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler as _WatchdogBase
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False
    _WatchdogBase = object  # type: ignore[assignment,misc]


class DownloadsWatcher:
    """Watches the Downloads folder for new 'full *.zip' archives."""

    def __init__(self, store: StateStore, settings_store: SettingsStore | None = None) -> None:
        self._store = store
        self._settings_store = settings_store
        self._pending: dict[str, float] = {}   # path_str -> time of last stable check
        self._pending_sizes: dict[str, int] = {}  # path_str -> last measured size

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._scan_existing()
        if _HAS_WATCHDOG:
            self._run_with_watchdog()
        else:
            logger.warning("watchdog not installed; falling back to polling mode")
            self._run_polling()

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_target(path: Path) -> bool:
        return (
            path.suffix.lower() == ".zip"
            and path.name.lower().startswith(ARCHIVE_PREFIX.lower())
        )

    def _add_candidate(self, path: Path) -> None:
        if not self._is_target(path):
            return
        name = path.stem
        if self._store.is_processed(name):
            return
        path_str = str(path)
        if path_str not in self._pending:
            logger.info("New candidate: %s", path.name)
            self._pending[path_str] = time.monotonic()
            self._pending_sizes[path_str] = -1

    def _drain_stable(self) -> list[Path]:
        """Return paths whose size has been stable for STABILITY_SECS."""
        now = time.monotonic()
        ready: list[Path] = []
        for path_str in list(self._pending):
            path = Path(path_str)
            if not path.exists():
                self._pending.pop(path_str, None)
                self._pending_sizes.pop(path_str, None)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            last_size = self._pending_sizes.get(path_str, -1)
            if size != last_size:
                self._pending_sizes[path_str] = size
                self._pending[path_str] = now
            elif now - self._pending[path_str] >= STABILITY_SECS:
                ready.append(path)
                self._pending.pop(path_str)
                self._pending_sizes.pop(path_str, None)
        return ready

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process(self, path: Path) -> None:
        name = path.stem
        if self._store.is_processed(name):
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = time.time()
        downloaded_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds")

        self._store.add_processing(name, str(path), downloaded_at)
        logger.info("Processing archive: %s", path.name)
        try:
            dd = self._settings_store.get_describe_diagrams() if self._settings_store else True
            def _on_progress(done: int, total: int) -> None:
                self._store.update_progress(name, done, total)
            self._run_pipeline(path, describe_diagrams=dd, progress_callback=_on_progress)
            out_dir = ARCHIVE_RUNS_DIR / name
            count = 0
            manifest_path = out_dir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                count = manifest.get("count", 0)
            self._store.mark_ready(name, str(out_dir), count)
            logger.info("Done: %s (%d docs)", name, count)
        except Exception as exc:
            self._store.mark_error(name, str(exc))
            logger.error("Failed: %s — %s", name, exc)

    @staticmethod
    def _run_pipeline(
        archive_path: Path,
        *,
        describe_diagrams: bool = True,
        progress_callback=None,
    ) -> None:
        from src.archive.processor import extract_archive

        ARCHIVE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        extract_archive(
            argparse.Namespace(
                archive=str(archive_path),
                out_dir=str(ARCHIVE_RUNS_DIR / archive_path.stem),
                format="md",
                ocr="hybrid",
                model=DEFAULT_MODEL,
                strong_model=DEFAULT_STRONG_MODEL,
                escalate_threshold=ESCALATE_THRESHOLD,
                cache_dir=str(CACHE_DIR),
                keep_image_markers=False,
                describe_diagrams=describe_diagrams,
                page_fallback="auto",
                skip_existing=False,
                clean=True,
                keep_artifacts=False,
                limit=None,
            ),
            progress_callback=progress_callback,
        )

    # ------------------------------------------------------------------
    # Main loops
    # ------------------------------------------------------------------

    def _scan_existing(self) -> None:
        if not DOWNLOADS_DIR.exists():
            return
        for path in DOWNLOADS_DIR.glob("*.zip"):
            self._add_candidate(path)

    def _tick(self) -> None:
        ready = self._drain_stable()
        for path in ready:
            Thread(target=self._process, args=(path,), daemon=True).start()

    def _run_with_watchdog(self) -> None:
        handler = _WatchdogHandler(self)
        observer = Observer()
        if DOWNLOADS_DIR.exists():
            observer.schedule(handler, str(DOWNLOADS_DIR), recursive=False)
        observer.start()
        logger.info("Watchdog observer started on %s", DOWNLOADS_DIR)
        try:
            while True:
                self._tick()
                time.sleep(FILE_CHECK_INTERVAL)
        finally:
            observer.stop()
            observer.join()

    def _run_polling(self) -> None:
        logger.info("Polling %s every %.1fs", DOWNLOADS_DIR, FILE_CHECK_INTERVAL)
        while True:
            if DOWNLOADS_DIR.exists():
                for path in DOWNLOADS_DIR.glob("*.zip"):
                    self._add_candidate(path)
            self._tick()
            time.sleep(FILE_CHECK_INTERVAL)


class _WatchdogHandler(_WatchdogBase):
    def __init__(self, watcher: DownloadsWatcher) -> None:
        if _HAS_WATCHDOG:
            super().__init__()
        self._watcher = watcher

    def on_created(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._watcher._add_candidate(Path(event.src_path))

    def on_moved(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._watcher._add_candidate(Path(event.dest_path))
