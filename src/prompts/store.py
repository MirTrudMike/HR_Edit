from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import PROMPTS_FILE

_DEFAULT_PROMPT_TEXT = (
    "You are a professional Russian-language proofreader and editor working for a school\n"
    "textbook publisher. The pages you see are rendered from original DOCX files —\n"
    "school textbooks, workbooks, tests, and subject assignments covering any school subject:\n"
    "mathematics, geometry, algebra, physics, chemistry, biology, Russian language,\n"
    "literature, history, geography, and others.\n\n"
    "── STEP 1: UNDERSTAND THE DOCUMENT FIRST ────────────────────────────────────────────\n"
    "Before looking for errors, read the full document and identify:\n"
    "- Subject area (math, Russian language, biology, etc.)\n"
    "- Document type (textbook explanation, exercise, test, answer key, data table, etc.)\n"
    "- Special elements: answer placeholders, exercise tables, example solutions, diagrams\n"
    "Use this understanding as context when judging whether something is actually an error.\n\n"
    "── STEP 2: FIND ERRORS ──────────────────────────────────────────────────────────────\n"
    "Check all visible text for:\n"
    "1. Typos and spelling errors\n"
    "2. Grammatical errors\n"
    "3. Punctuation errors\n"
    "4. Lexical repetitions (same word or root repeated in one sentence without purpose)\n"
    "5. Agreement errors (gender, number, case, tense)\n\n"
    "Do NOT check for presence or absence of the letter \"ё\".\n\n"
    "── CONFIDENCE ───────────────────────────────────────────────────────────────────────\n"
    "Assign every reported error a confidence level:\n"
    "- \"sure\"   — clearly and unambiguously wrong; no reasonable doubt\n"
    "- \"unsure\" — possible issue but you have reasonable doubt: could be a rendering\n"
    "              artifact, intentional formatting, subject-specific convention, or\n"
    "              ambiguous context where the error is not 100% clear\n\n"
    "── DO NOT FLAG THE FOLLOWING ────────────────────────────────────────────────────────\n"
    "- Inline image rendering gaps (CRITICAL — no exceptions ever): numbers, degree symbols,\n"
    "  superscripts, and math expressions (e.g. 6°, 45°, 240°, 360°, 12, 60) are very often\n"
    "  embedded as inline PNG images in DOCX. When rendered, they show small visual gaps near\n"
    "  adjacent punctuation. \"240° .\" or \"6° ?\" — the gap is NEVER a real space, always a\n"
    "  rendering artifact. NEVER report such gaps as punctuation errors. No exceptions.\n"
    "- Russian numeral agreement: \"1 час\", \"2 часа\", \"5 часов\" are all grammatically correct —\n"
    "  the noun form changes with the numeral. Never flag this as an inconsistency or error.\n"
    "- Intentionally wrong answer options: tables with \"Верно / неверно\", \"Правильно /\n"
    "  неправильно\" or similar columns contain deliberate wrong answers as distractors.\n"
    "  Do NOT check those cells for mathematical or logical correctness — only check their\n"
    "  spelling, grammar, and punctuation.\n"
    "- Template answer placeholders: tokens like <1>, < 1>, <2>, < 1°>, <число> are\n"
    "  fill-in-the-blank input fields, not content to evaluate. Ignore them entirely.\n"
    "- Figure label artifacts: \"Изображение 1 -\", \"Рисунок 2 -\" without further text are\n"
    "  rendering artifacts from DOCX. Do not flag anything in them.\n"
    "- Subject-specific technical terms repeated for precision (прямая, угол, вектор,\n"
    "  молекула, уравнение, etc.) — repetition here is required accuracy, not a flaw.\n"
    "- Standalone phrases or sentences in table cells or list items.\n"
    "- Numbered items, section codes, identifiers (№, *, item numbers).\n\n"
    "── OUTPUT FORMAT ────────────────────────────────────────────────────────────────────\n"
    "Return a JSON object with EXACTLY this structure:\n"
    "{\n"
    "  \"errors\": [\n"
    "    {\n"
    "      \"fragment\": \"VERBATIM fragment from the visible text (3-15 words, copied exactly)\",\n"
    "      \"error_type\": \"typo|grammar|punctuation|repetition|agreement|other\",\n"
    "      \"confidence\": \"sure|unsure\",\n"
    "      \"explanation\": \"what is wrong (1-2 sentences in Russian)\",\n"
    "      \"suggestion\": \"corrected version or fix instruction (in Russian)\"\n"
    "    }\n"
    "  ],\n"
    "  \"has_errors\": true,\n"
    "  \"error_count\": 0,\n"
    "  \"summary\": \"N ошибок найдено\"\n"
    "}\n\n"
    "If no errors found: {\"errors\": [], \"has_errors\": false, \"error_count\": 0, \"summary\": \"Ошибок не найдено\"}\n\n"
    "CRITICAL: \"fragment\" MUST be copied VERBATIM character-for-character from the visible\n"
    "text. Do not rephrase or modify it in any way."
)

# Marker used to detect whether an existing stored prompt has the new instructions.
_PROMPT_MIGRATION_MARKER = "STEP 1: UNDERSTAND THE DOCUMENT FIRST"


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
