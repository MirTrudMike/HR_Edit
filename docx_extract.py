#!/usr/bin/env python3
"""Backward-compatible CLI entry point. Logic lives in src/extractor/."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.config import DEFAULT_MODEL, DEFAULT_STRONG_MODEL  # noqa: F401 (imported by archive_extract)
from src.extractor.extract import build_parser, extract, main  # noqa: F401

if __name__ == "__main__":
    main()
