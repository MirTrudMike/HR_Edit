from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Explicit path — avoids cwd-dependent lookup when launched as a subprocess
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)
WORK_DIR = PROJECT_ROOT / "work"
ARCHIVE_RUNS_DIR = WORK_DIR / "archive_runs"
UPLOADS_DIR = WORK_DIR / "uploads"
CACHE_DIR = WORK_DIR / "cache" / "ocr"
STATE_FILE = WORK_DIR / "state.json"
PROMPTS_FILE = WORK_DIR / "prompts.json"
SETTINGS_FILE = WORK_DIR / "settings.json"

# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------
DOWNLOADS_DIR = Path.home() / "Downloads"
ARCHIVE_PREFIX = "full "
STABILITY_SECS: float = 3.0
FILE_CHECK_INTERVAL: float = 1.0

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
SERVER_HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8765"))

# ---------------------------------------------------------------------------
# OCR models
# ---------------------------------------------------------------------------
DEFAULT_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_STRONG_MODEL: str = os.getenv("OPENAI_STRONG_MODEL", "gpt-5.5")
ANALYSIS_MODEL: str = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5.2")
ESCALATE_THRESHOLD: float = 0.85
