from __future__ import annotations

import argparse
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import (
    ANALYSIS_MODEL,
    ANALYSIS_MODELS,
    ARCHIVE_RUNS_DIR,
    CACHE_DIR,
    DEFAULT_MODEL,
    DEFAULT_STRONG_MODEL,
    ESCALATE_THRESHOLD,
    UPLOADS_DIR,
)
from src.prompts.store import PromptStore
from src.state.settings import SettingsStore

if TYPE_CHECKING:
    from src.state.store import StateStore

_HOME_HTML = Path(__file__).parent / "home.html"


class RenameBody(BaseModel):
    display_name: str


class PromptBody(BaseModel):
    name: str
    text: str


class PromptUpdateBody(BaseModel):
    name: str | None = None
    text: str | None = None
    is_default: bool | None = None


class AnalyzeBody(BaseModel):
    archive_name: str
    doc_index: int
    prompt_id: str | None = None
    model_id: str | None = None


class SettingsBody(BaseModel):
    describe_diagrams: bool


_RE_TABLE_ROW    = re.compile(r"^\|")
_RE_TABLE_SEP    = re.compile(r"^[-:| ]+$")
_RE_DIVIDER_LINE = re.compile(r"^[-|:\s]+$")   # non-pipe separator rows like "---  ---  ---"
_RE_MD_HEADER    = re.compile(r"^#{1,6}\s+")
_RE_MD_STRONG    = re.compile(r"\*\*([^*\n]+)\*\*")
_RE_MD_EM        = re.compile(r"\*([^*\n]+)\*")
_RE_MD_CODE      = re.compile(r"`([^`]+)`")
_RE_IMAGE        = re.compile(r"\[IMAGE:\d+:[^\]]*\]")
_RE_URL          = re.compile(r"https?://\S+")
_RE_LIST         = re.compile(r"^[\s]*[-*]\s+")
_RE_HTML_TAG     = re.compile(r"<[^>]+>")
_RE_MULTIBLANK   = re.compile(r"\n{3,}")
# Backslash escape artifacts left by Markdown table rendering
_RE_BACKSLASH    = re.compile(r"\\+")
# Lines consisting entirely of image/figure label sequences like
# "Изображение 1 -" or "Изображение 1 - Изображение 2 - Изображение 3 -".
# These are remnants of our [IMAGE:N:...] markers being stripped.
# Does NOT match "смотри рисунок 1" — that has content before the keyword.
_RE_IMAGE_LABEL_LINE = re.compile(
    r"^((изображение|рисунок|рис\.)\s*\d*\s*[-–—]?\s*)+$",
    re.IGNORECASE,
)


def _clean_inline(text: str) -> str:
    """Strip inline artifacts from a text chunk (cell or line)."""
    text = _RE_HTML_TAG.sub(" ", text)
    text = _RE_IMAGE.sub("", text)
    text = _RE_URL.sub("", text)
    text = _RE_BACKSLASH.sub("", text)
    text = _RE_MD_STRONG.sub(r"\1", text)
    text = _RE_MD_EM.sub(r"\1", text)
    text = _RE_MD_CODE.sub(r"\1", text)
    return " ".join(text.split())


def _md_to_plain(md: str) -> str:
    """Strip Markdown markup to get clean plain text for AI proofreading."""
    lines: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        # Skip image/figure label lines entirely — they are extraction artifacts
        if _RE_IMAGE_LABEL_LINE.match(stripped):
            continue
        if _RE_TABLE_ROW.match(stripped):
            # Pipe table row: join non-empty cells with " | " so the AI
            # sees them as one structured entry, then add a blank line to
            # make each row a distinct paragraph (prevents treating consecutive
            # rows as a single run-on sentence).
            cells = [_clean_inline(c) for c in stripped.strip("|").split("|")]
            cells = [c for c in cells if c and not _RE_TABLE_SEP.match(c)]
            if cells:
                lines.append(" | ".join(cells))
                lines.append("")
            continue
        line = _RE_MD_HEADER.sub("", line)
        line = _RE_LIST.sub("", line)
        line = _clean_inline(line)
        # Skip divider-only lines (e.g. "---  ---  ---", Markdown HR "---")
        if line and _RE_DIVIDER_LINE.match(line):
            continue
        lines.append(line)
    return _RE_MULTIBLANK.sub("\n\n", "\n".join(lines)).strip()


_RE_DEGREE = re.compile(r"°")
# Figure label lines like "Изображение 1 -" or "Рисунок 2 -" are rendering artifacts.
_RE_FIGURE_LABEL = re.compile(
    r"^((изображение|рисунок|рис\.)\s*\d*\s*[-–—]?\s*)+$",
    re.IGNORECASE,
)


def _filter_inline_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    """Remove false-positive errors caused by known DOCX rendering artifacts.

    Two classes are filtered deterministically:
    1. Punctuation errors whose fragment contains a degree symbol (°) — these are always
       visual gaps from inline PNG images, not real spaces.
    2. Any error whose fragment is solely a figure-label artifact like "Изображение 1 -"
       or "Рисунок 2 -" — these captions appear in the rendered page but carry no text
       to proofread.
    """
    errors = result.get("errors", [])
    filtered = [
        e for e in errors
        if not (
            e.get("error_type") == "punctuation"
            and _RE_DEGREE.search(e.get("fragment", ""))
        )
        and not _RE_FIGURE_LABEL.match(e.get("fragment", "").strip())
    ]
    if len(filtered) != len(errors):
        result = dict(result)
        result["errors"] = filtered
        result["error_count"] = len(filtered)
        result["has_errors"] = bool(filtered)
        result["summary"] = (
            "Ошибок не найдено" if not filtered else f"{len(filtered)} ошибок найдено"
        )
    return result


def _call_openai_analyze(image_paths: list[Path], system_prompt: str, model: str) -> dict[str, Any]:
    """Send rendered document pages to OpenAI vision and return parsed JSON result."""
    import base64
    import os

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    content: list[Any] = [{"type": "text", "text": "Проверь этот документ:"}]
    for page_path in image_paths:
        b64 = base64.b64encode(page_path.read_bytes()).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    raw = response.choices[0].message.content or "{}"
    result = json.loads(raw)
    if not isinstance(result.get("errors"), list):
        result["errors"] = []
    result.setdefault("has_errors", bool(result["errors"]))
    result.setdefault("error_count", len(result["errors"]))
    result.setdefault("summary", "Ошибок не найдено" if not result["errors"] else f"Найдено {len(result['errors'])}")
    return result


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


def _run_docx_pipeline(
    docx_path: Path,
    *,
    describe_diagrams: bool = True,
    progress_callback=None,
) -> None:
    import shutil as _shutil

    from src.archive.indexer import write_archive_index
    from src.archive.processor import render_original_docx, render_original_pages
    from src.extractor.extract import extract

    ARCHIVE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    out_dir = ARCHIVE_RUNS_DIR / docx_path.stem
    if out_dir.exists():
        _shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = docx_path.stem
    output = out_dir / f"{stem}.md"
    source_docx = out_dir / f"{stem}.docx"
    original_pdf = out_dir / f"{stem}.original.pdf"
    original_pages_dir = out_dir / f"{stem}.original_pages"
    original_fallback = out_dir / f"{stem}.original.fallback.html"
    images_dir = out_dir / f"{stem}_images"
    debug = out_dir / f"{stem}.ocr.json"

    _shutil.copy2(docx_path, source_docx)

    if progress_callback:
        progress_callback(0, 1)

    render_error = render_original_docx(source_docx, original_pdf)
    original_pages = render_original_pages(original_pdf, original_pages_dir)

    error = None
    try:
        extract(
            argparse.Namespace(
                docx=str(docx_path),
                out=str(output),
                format="md",
                ocr="hybrid",
                model=DEFAULT_MODEL,
                strong_model=DEFAULT_STRONG_MODEL,
                escalate_threshold=ESCALATE_THRESHOLD,
                images_dir=str(images_dir),
                cache_dir=str(CACHE_DIR),
                debug_json=str(debug),
                keep_image_markers=False,
                describe_diagrams=describe_diagrams,
                page_fallback="auto",
            )
        )
        if debug.exists():
            debug.unlink()
        if images_dir.exists():
            _shutil.rmtree(images_dir)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        output.write_text(f"# Processing error\n\n{error}\n", encoding="utf-8")

    item: dict[str, Any] = {
        "source": docx_path.name,
        "output": str(output),
        "source_docx": str(source_docx),
        "original_pdf": str(original_pdf),
        "original_pages": [str(original_pages_dir / name) for name in original_pages],
        "original_fallback": str(original_fallback),
    }
    if error:
        item["error"] = error
    if render_error:
        item["original_error"] = render_error

    write_archive_index(out_dir, docx_path, [item])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "archive": str(docx_path),
        "archive_name": docx_path.name,
        "archive_title": docx_path.stem,
        "out_dir": str(out_dir),
        "format": "md",
        "ocr": "hybrid",
        "describe_diagrams": True,
        "count": 1,
        "processed_at": now,
        "results": [item],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if progress_callback:
        progress_callback(1, 1)


def create_app(store: StateStore, settings_store: SettingsStore | None = None) -> FastAPI:
    if settings_store is None:
        settings_store = SettingsStore()

    app = FastAPI(title="HYURazberesh", docs_url=None, redoc_url=None)

    ARCHIVE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/archives",
        StaticFiles(directory=str(ARCHIVE_RUNS_DIR), html=True),
        name="archives",
    )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return _HOME_HTML.read_text(encoding="utf-8")

    @app.get("/api/archives")
    async def list_archives() -> dict[str, Any]:
        return {"archives": store.get_all()}

    @app.post("/api/archives/{name:path}/open")
    async def open_archive(name: str) -> dict[str, Any]:
        store.mark_opened(name)
        rec = store.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="Archive not found")
        return rec

    @app.post("/api/archives/{name:path}/rename")
    async def rename_archive(name: str, body: RenameBody) -> dict[str, Any]:
        dn = body.display_name.strip()
        if not dn:
            raise HTTPException(status_code=422, detail="display_name cannot be empty")
        store.rename(name, dn)
        rec = store.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="Archive not found")
        return rec

    @app.delete("/api/archives/{name:path}")
    async def delete_archive(name: str) -> dict[str, str]:
        rec = store.get(name)
        out_dir = store.delete(name)
        if out_dir is None:
            raise HTTPException(status_code=404, detail="Archive not found")
        if out_dir:
            out_path = Path(out_dir)
            if out_path.is_dir():
                shutil.rmtree(out_path, ignore_errors=True)
        if rec:
            ap = Path(rec.get("archive_path", ""))
            if ap.is_file():
                ap.unlink(missing_ok=True)
        return {"status": "deleted", "name": name}

    @app.post("/api/upload")
    async def upload_archive(file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(status_code=422, detail="Only .zip files are accepted")

        raw_stem = Path(file.filename).stem
        safe_name = "".join(
            ch if ch.isalnum() or ch in " ._()-" else "_" for ch in raw_stem
        ).strip(" .")
        if not safe_name:
            safe_name = "upload"

        existing = store.get(safe_name)
        if existing and existing.get("status") == "processing":
            raise HTTPException(status_code=409, detail="Архив уже обрабатывается")

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / f"{safe_name}.zip"
        dest.write_bytes(await file.read())

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.add_processing(safe_name, str(dest), now)

        def _process() -> None:
            try:
                dd = settings_store.get_describe_diagrams()
                def _on_progress(done: int, total: int) -> None:
                    store.update_progress(safe_name, done, total)
                _run_pipeline(dest, describe_diagrams=dd, progress_callback=_on_progress)
                out_dir = ARCHIVE_RUNS_DIR / safe_name
                count = 0
                doc_errors: list[str] = []
                manifest_path = out_dir / "manifest.json"
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    count = manifest.get("count", 0)
                    for r in manifest.get("results", []):
                        if r.get("error"):
                            doc_errors.append(f"{Path(r['source']).name}: {r['error']}")
                partial_error = "; ".join(doc_errors) if doc_errors else None
                store.mark_ready(safe_name, str(out_dir), count, error=partial_error)
            except Exception as exc:
                store.mark_error(safe_name, str(exc))

        threading.Thread(target=_process, daemon=True).start()
        return {"name": safe_name, "status": "processing"}

    @app.post("/api/upload-docx")
    async def upload_docx(file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename or not file.filename.lower().endswith(".docx"):
            raise HTTPException(status_code=422, detail="Only .docx files are accepted")

        raw_stem = Path(file.filename).stem
        safe_name = "".join(
            ch if ch.isalnum() or ch in " ._()-" else "_" for ch in raw_stem
        ).strip(" .")
        if not safe_name:
            safe_name = "docx_upload"

        existing = store.get(safe_name)
        if existing and existing.get("status") == "processing":
            raise HTTPException(status_code=409, detail="Файл уже обрабатывается")

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / f"{safe_name}.docx"
        dest.write_bytes(await file.read())

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.add_processing(safe_name, str(dest), now, kind="docx")

        def _process_docx() -> None:
            try:
                dd = settings_store.get_describe_diagrams()
                def _on_progress(done: int, total: int) -> None:
                    store.update_progress(safe_name, done, total)
                _run_docx_pipeline(dest, describe_diagrams=dd, progress_callback=_on_progress)
                out_dir = ARCHIVE_RUNS_DIR / safe_name
                count = 1
                doc_errors: list[str] = []
                manifest_path = out_dir / "manifest.json"
                if manifest_path.exists():
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    count = manifest_data.get("count", 1)
                    for r in manifest_data.get("results", []):
                        if r.get("error"):
                            doc_errors.append(f"{Path(r['source']).name}: {r['error']}")
                partial_error = "; ".join(doc_errors) if doc_errors else None
                store.mark_ready(safe_name, str(out_dir), count, error=partial_error)
            except Exception as exc:
                store.mark_error(safe_name, str(exc))

        threading.Thread(target=_process_docx, daemon=True).start()
        return {"name": safe_name, "status": "processing"}

    @app.get("/api/status")
    async def status() -> dict[str, str]:
        return {"status": "ok", "watching": str(ARCHIVE_RUNS_DIR)}

    # ------------------------------------------------------------------
    # Settings API
    # ------------------------------------------------------------------

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings_store.get_all()

    @app.put("/api/settings")
    async def update_settings(body: SettingsBody) -> dict[str, Any]:
        settings_store.set_describe_diagrams(body.describe_diagrams)
        return settings_store.get_all()

    # ------------------------------------------------------------------
    # Prompts API
    # ------------------------------------------------------------------

    prompt_store = PromptStore()

    @app.get("/api/prompts")
    async def list_prompts() -> dict[str, Any]:
        return {"prompts": prompt_store.get_all()}

    @app.post("/api/prompts")
    async def create_prompt(body: PromptBody) -> dict[str, Any]:
        name = body.name.strip()
        text = body.text.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name cannot be empty")
        if not text:
            raise HTTPException(status_code=422, detail="text cannot be empty")
        return prompt_store.add(name, text)

    @app.put("/api/prompts/{prompt_id}")
    async def update_prompt(prompt_id: str, body: PromptUpdateBody) -> dict[str, Any]:
        if body.is_default is True:
            if not prompt_store.set_default(prompt_id):
                raise HTTPException(status_code=404, detail="Prompt not found")
        if body.name is not None or body.text is not None:
            rec = prompt_store.update(prompt_id, body.name, body.text)
            if rec is None:
                raise HTTPException(status_code=404, detail="Prompt not found")
        rec = prompt_store.get(prompt_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return rec

    @app.delete("/api/prompts/{prompt_id}")
    async def delete_prompt(prompt_id: str) -> dict[str, str]:
        if not prompt_store.delete(prompt_id):
            raise HTTPException(status_code=404, detail="Prompt not found")
        return {"status": "deleted", "id": prompt_id}

    # ------------------------------------------------------------------
    # Analyze API
    # ------------------------------------------------------------------

    @app.get("/api/models")
    async def list_models() -> dict[str, Any]:
        return {"models": ANALYSIS_MODELS, "default": ANALYSIS_MODEL}

    @app.post("/api/analyze")
    async def analyze_document(body: AnalyzeBody) -> dict[str, Any]:
        archive_rec = store.get(body.archive_name)
        if archive_rec is None:
            raise HTTPException(status_code=404, detail="Archive not found")

        out_dir = Path(archive_rec.get("out_dir", ""))
        manifest_path = out_dir / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail="Manifest not found")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        results = manifest.get("results", [])
        if body.doc_index < 0 or body.doc_index >= len(results):
            raise HTTPException(status_code=422, detail="doc_index out of range")

        original_pages = results[body.doc_index].get("original_pages", [])
        if not original_pages:
            raise HTTPException(status_code=422, detail="Страницы оригинала не найдены — возможно, документ не был отрендерен")

        image_paths = [Path(p) for p in original_pages]
        missing = [ip.name for ip in image_paths if not ip.exists()]
        if missing:
            raise HTTPException(status_code=404, detail=f"Файлы страниц не найдены: {', '.join(missing)}")

        model = body.model_id or ANALYSIS_MODEL
        valid_ids = {m["id"] for m in ANALYSIS_MODELS}
        if model not in valid_ids:
            raise HTTPException(status_code=422, detail=f"Неизвестная модель: {model}")

        prompt_id = body.prompt_id
        if prompt_id:
            prompt_rec = prompt_store.get(prompt_id)
        else:
            prompt_rec = prompt_store.get_default()

        if prompt_rec is None:
            raise HTTPException(status_code=404, detail="Prompt not found")

        try:
            result = _call_openai_analyze(image_paths, prompt_rec["text"], model)
            result = _filter_inline_artifacts(result)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenAI error: {exc}") from exc

        result["prompt_name"] = prompt_rec["name"]
        result["model_id"] = model
        result["archive_name"] = body.archive_name
        result["doc_index"] = body.doc_index
        return result

    return app
