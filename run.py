#!/usr/bin/env python3
"""Entry point: start the Downloads watcher and local web server."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from threading import Thread

# Ensure the project root is on sys.path so `src.*` imports resolve.
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn

from src.config import ARCHIVE_RUNS_DIR, SERVER_HOST, SERVER_PORT
from src.state.settings import SettingsStore
from src.state.store import StateStore
from src.watcher.watcher import DownloadsWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("masha")


def _reconcile(store: StateStore) -> None:
    """Import metadata from existing manifests that pre-date state.json."""
    import json

    for manifest_path in ARCHIVE_RUNS_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            name = manifest_path.parent.name
            store.add_from_manifest(name, manifest)
        except Exception as exc:
            logger.warning("Could not import manifest %s: %s", manifest_path, exc)


def main() -> None:
    store = StateStore()
    settings_store = SettingsStore()
    _reconcile(store)

    watcher = DownloadsWatcher(store, settings_store)
    watcher_thread = Thread(target=watcher.start, name="watcher", daemon=True)
    watcher_thread.start()

    from src.server.app import create_app

    app = create_app(store, settings_store)
    url = f"http://{SERVER_HOST}:{SERVER_PORT}"
    logger.info("Server starting at %s", url)
    print(f"\n  Masha is running at  {url}\n")

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
