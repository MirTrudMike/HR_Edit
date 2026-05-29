#!/usr/bin/env python3
"""Backward-compatible CLI entry point. Logic lives in src/archive/."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.archive.processor import build_parser, extract_archive, main  # noqa: F401

if __name__ == "__main__":
    main()
