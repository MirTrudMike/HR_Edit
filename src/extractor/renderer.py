from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from src.extractor.context import ExtractionContext


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _cell_to_markdown(cell_blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in cell_blocks:
        if block["type"] == "paragraph":
            parts.append(block["text"].replace("\n", "<br>").strip())
        elif block["type"] == "table":
            parts.append(_nested_table_to_inline(block))
    return "<br>".join(part for part in parts if part)


def _nested_table_to_inline(table: dict[str, Any]) -> str:
    rows: list[str] = []
    for row in table["rows"]:
        cells = [_cell_to_markdown(cell) for cell in row]
        rows.append(" | ".join(cell for cell in cells if cell))
    return "<br>".join(row for row in rows if row)


def _escape_md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _markdown_table_lines(table: dict[str, Any]) -> list[str]:
    rows = table["rows"]
    if not rows:
        return []
    max_cols = max((len(row) for row in rows), default=0)
    rendered_rows: list[list[str]] = []
    for row in rows:
        rendered = [_escape_md_cell(_cell_to_markdown(cell)) for cell in row]
        rendered.extend([""] * (max_cols - len(rendered)))
        rendered_rows.append(rendered)

    lines = ["| " + " | ".join(rendered_rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in rendered_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_markdown(blocks: list[Any]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block["type"] == "paragraph":
            text = block["text"].strip()
            if text:
                lines.append(text)
                lines.append("")
        elif block["type"] == "table":
            lines.extend(_markdown_table_lines(block))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Plain-text rendering
# ---------------------------------------------------------------------------

def _cell_to_plain_text(cell_blocks: list[Any], indent: int = 0) -> str:
    parts: list[str] = []
    for block in cell_blocks:
        if block["type"] == "paragraph":
            text = _normalize_plain_text(block["text"])
            if text:
                parts.append(text)
        elif block["type"] == "table":
            nested = _plain_table_lines(block, indent=indent)
            parts.append("\n".join(nested))
    return "\n".join(part for part in parts if part)


def _normalize_plain_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines()]
    return "\n".join(line for line in lines if line)


def _looks_like_key_value_table(table: dict[str, Any]) -> bool:
    rows = table["rows"]
    if not rows:
        return False
    two_col_rows = [row for row in rows if len(row) == 2]
    if len(two_col_rows) < max(1, len(rows) // 2):
        return False
    for row in two_col_rows:
        left = _cell_to_plain_text(row[0]).strip()
        if not left:
            return False
    return True


def _plain_table_lines(table: dict[str, Any], indent: int = 0) -> list[str]:
    prefix = " " * indent
    rows = table["rows"]
    if not rows:
        return []

    if _looks_like_key_value_table(table):
        lines: list[str] = []
        for row in rows:
            if len(row) == 1:
                value = _cell_to_plain_text(row[0], indent=indent).strip()
                if value:
                    lines.append(f"{prefix}{value}")
                continue
            key = _cell_to_plain_text(row[0], indent=indent).replace("\n", " ").strip()
            value = _cell_to_plain_text(row[1], indent=indent + 2).strip()
            if value:
                if "\n" in value:
                    lines.append(f"{prefix}{key}:")
                    for line in value.splitlines():
                        lines.append(f"{prefix}  {line}")
                else:
                    lines.append(f"{prefix}{key}: {value}")
            elif key:
                lines.append(f"{prefix}{key}:")
        return lines

    lines = []
    for row in rows:
        cells = [_cell_to_plain_text(cell, indent=indent).replace("\n", " / ").strip() for cell in row]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(f"{prefix}" + " | ".join(cells))
    return lines


def render_plain_text(blocks: list[Any]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block["type"] == "paragraph":
            text = _normalize_plain_text(block["text"])
            if text:
                lines.extend(text.splitlines())
                lines.append("")
        elif block["type"] == "table":
            lines.extend(_plain_table_lines(block))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# JSON / debug rendering
# ---------------------------------------------------------------------------

def render_json_result(
    blocks: list[Any], ctx: ExtractionContext, fallback: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "text": render_plain_text(blocks),
        "markdown": render_markdown(blocks),
        "blocks": blocks,
        "images": [image.__dict__ for image in ctx.images],
        "warnings": ctx.warnings,
        "fallback": fallback,
    }


def render_pages_fallback(docx_path: Path, out_dir: Path) -> dict[str, Any]:
    office = shutil.which("libreoffice") or shutil.which("soffice")
    if not office:
        return {
            "available": False,
            "reason": "LibreOffice/soffice is not installed. Page rendering fallback is unavailable.",
            "pages": [],
        }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        subprocess.run(
            [office, "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(docx_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pdfs = list(tmp_path.glob("*.pdf"))
        if not pdfs:
            return {"available": False, "reason": "LibreOffice did not produce a PDF.", "pages": []}
        fallback_pdf = out_dir / f"{docx_path.stem}.fallback.pdf"
        shutil.copy2(pdfs[0], fallback_pdf)
        return {
            "available": True,
            "reason": "PDF fallback was rendered. PNG page OCR is not implemented yet.",
            "pdf": str(fallback_pdf),
            "pages": [],
        }


def write_debug_json(
    path: Path,
    ctx: ExtractionContext,
    blocks: list[Any],
    fallback: dict[str, Any] | None,
) -> None:
    payload = {
        "source": str(ctx.docx_path),
        "ocr_mode": ctx.ocr_mode,
        "model": ctx.model,
        "strong_model": ctx.strong_model,
        "escalate_threshold": ctx.escalate_threshold,
        "images": [image.__dict__ for image in ctx.images],
        "warnings": ctx.warnings,
        "fallback": fallback,
        "blocks": blocks,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
