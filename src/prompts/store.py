from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import PROMPTS_FILE

_DEFAULT_PROMPT_TEXT = (
    "You are a professional Russian-language proofreader and editor.\n"
    "The text comes from educational materials — school textbooks, workbooks, and subject\n"
    "assignments. Subjects can be anything: mathematics, geometry, algebra, physics, chemistry,\n"
    "biology, Russian language, literature, history, geography, and others.\n"
    "IMPORTANT CONTEXT: this text was machine-extracted from a DOCX file. It may contain\n"
    "markup remnants (backslashes, pipe separators, image labels like «Изображение 1»,\n"
    "square-bracket markers). These are extraction artifacts, not part of the authored text.\n"
    "Ignore them completely — do not report them as errors of any kind.\n\n"
    "Carefully analyze the provided text and find ALL of the following:\n"
    "1. Typos and spelling errors\n"
    "2. Grammatical errors\n"
    "3. Punctuation errors\n"
    "4. Lexical repetitions (same or similar words in one sentence or adjacent sentences)\n"
    "5. Agreement errors (number, case, tense)\n"
    "6. Formula and calculation errors\n"
    "7. Logical inconsistencies (solution does not match the task)\n\n"
    "Do NOT check for presence or absence of the letter \"ё\".\n\n"
    "IMPORTANT — do NOT flag any of the following as errors:\n"
    "- Repetition of subject-specific technical terms — in a subject context, the same term\n"
    "  (e.g. прямая, точка, угол, вектор, молекула, уравнение) may and must appear multiple\n"
    "  times; this is required precision, not a stylistic flaw\n"
    "- Short standalone phrases or complete sentences on their own line — these are table cells\n"
    "  or list items, not parts of a larger sentence\n"
    "- Multiple complete sentences listed one after another — if each sentence already ends with\n"
    "  a period, exclamation mark, or question mark, they are intentionally separate; do NOT\n"
    "  call this a run-on sentence or suggest adding semicolons between them\n"
    "- Missing punctuation between items separated by \" | \" — the pipe is a structural separator\n"
    "- Numbered items, codes, or identifiers (e.g. \"№\", \"*\", section numbers)\n"
    "- Any technical or document-structure artifacts\n\n"
    "Return your response as a JSON object with EXACTLY this structure:\n"
    "{\n"
    "  \"errors\": [\n"
    "    {\n"
    "      \"fragment\": \"VERBATIM fragment from the text (copy character-for-character, 3-15 words)\",\n"
    "      \"error_type\": \"typo|grammar|punctuation|repetition|agreement|logic|other\",\n"
    "      \"explanation\": \"what is wrong (1-2 sentences in Russian)\",\n"
    "      \"suggestion\": \"corrected version or fix instruction (in Russian)\"\n"
    "    }\n"
    "  ],\n"
    "  \"has_errors\": true,\n"
    "  \"error_count\": 0,\n"
    "  \"summary\": \"N ошибок найдено\"\n"
    "}\n\n"
    "If no errors found: {\"errors\": [], \"has_errors\": false, \"error_count\": 0, \"summary\": \"Ошибок не найдено\"}\n\n"
    "CRITICAL: The \"fragment\" field MUST be copied VERBATIM from the input text, "
    "character-for-character. Do not rephrase or modify the fragment in any way.\n\n"
    "The user message will contain the document text in plain text format."
)

# Marker used to detect whether an existing stored prompt has the new instructions.
_PROMPT_MIGRATION_MARKER = "machine-extracted from a DOCX"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PromptStore:
    """Thread-safe JSON-backed registry of user-defined AI prompts."""

    def __init__(self, path: Path = PROMPTS_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()
        self._seed_default()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"prompts": {}}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _seed_default(self) -> None:
        with self._lock:
            if not self._data["prompts"]:
                default_id = "default"
                self._data["prompts"][default_id] = {
                    "id": default_id,
                    "name": "Корректор",
                    "text": _DEFAULT_PROMPT_TEXT,
                    "is_default": True,
                    "created_at": _now_iso(),
                }
                self._save()
                return
            # Migrate existing default prompt if it lacks the new instructions.
            for rec in self._data["prompts"].values():
                if rec.get("is_default") and _PROMPT_MIGRATION_MARKER not in rec.get("text", ""):
                    rec["text"] = _DEFAULT_PROMPT_TEXT
                    self._save()
                    break

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [dict(v) for v in self._data["prompts"].values()]
        records.sort(key=lambda r: (not r.get("is_default", False), r.get("created_at") or ""))
        return records

    def get(self, prompt_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._data["prompts"].get(prompt_id)
            return dict(rec) if rec is not None else None

    def get_default(self) -> dict[str, Any] | None:
        with self._lock:
            for rec in self._data["prompts"].values():
                if rec.get("is_default"):
                    return dict(rec)
            first = next(iter(self._data["prompts"].values()), None)
            return dict(first) if first else None

    def add(self, name: str, text: str) -> dict[str, Any]:
        with self._lock:
            prompt_id = str(uuid.uuid4())
            rec = {
                "id": prompt_id,
                "name": name.strip(),
                "text": text.strip(),
                "is_default": False,
                "created_at": _now_iso(),
            }
            self._data["prompts"][prompt_id] = rec
            self._save()
            return dict(rec)

    def update(self, prompt_id: str, name: str | None, text: str | None) -> dict[str, Any] | None:
        with self._lock:
            rec = self._data["prompts"].get(prompt_id)
            if rec is None:
                return None
            if name is not None:
                rec["name"] = name.strip()
            if text is not None:
                rec["text"] = text.strip()
            self._save()
            return dict(rec)

    def set_default(self, prompt_id: str) -> bool:
        with self._lock:
            if prompt_id not in self._data["prompts"]:
                return False
            for rec in self._data["prompts"].values():
                rec["is_default"] = rec["id"] == prompt_id
            self._save()
            return True

    def delete(self, prompt_id: str) -> bool:
        with self._lock:
            rec = self._data["prompts"].pop(prompt_id, None)
            if rec is None:
                return False
            if rec.get("is_default"):
                remaining = list(self._data["prompts"].values())
                if remaining:
                    remaining[0]["is_default"] = True
            self._save()
            return True
