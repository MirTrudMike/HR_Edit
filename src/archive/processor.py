from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from src.config import (
    ARCHIVE_RUNS_DIR,
    CACHE_DIR,
    DEFAULT_MODEL,
    DEFAULT_STRONG_MODEL,
    ESCALATE_THRESHOLD,
)
from src.archive.indexer import write_archive_index
from src.extractor.extract import extract


def safe_part(value: str) -> str:
    value = value.strip().replace("\\", "/")
    cleaned = "".join(ch if ch.isalnum() or ch in " ._()-" else "_" for ch in value)
    cleaned = cleaned.strip(" .")
    return cleaned or "untitled"


def safe_rel_path(zip_name: str) -> Path:
    pure = PurePosixPath(zip_name)
    parts = [safe_part(part) for part in pure.parts if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError(f"Unsafe empty zip path: {zip_name!r}")
    return Path(*parts)


def iter_docx_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name.startswith("__MACOSX/"):
            continue
        if PurePosixPath(name).name.startswith("~$"):
            continue
        if name.lower().endswith(".docx"):
            entries.append(info)
    return entries


def output_path_for_entry(out_dir: Path, entry_name: str, fmt: str) -> Path:
    rel = safe_rel_path(entry_name)
    return (out_dir / rel).with_suffix(f".{fmt}")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _render_fallback_original_html(source_docx: Path, reason: str) -> str:
    import html as _html
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Original document unavailable</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #f3f6fb; color: #162033; padding: 24px; }}
    .card {{ max-width: 720px; background: #fff; border: 1px solid #d6deeb; border-radius: 12px; padding: 20px; box-shadow: 0 10px 28px rgba(15,23,42,0.08); }}
    h1 {{ margin: 0 0 12px; font-size: 18px; }}
    p {{ margin: 0 0 10px; line-height: 1.55; }}
    code {{ display: block; white-space: pre-wrap; background: #f4f7fb; border: 1px solid #d6deeb; padding: 12px; border-radius: 8px; margin-top: 12px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Original DOCX preview is unavailable</h1>
    <p>{_html.escape(source_docx.name)}</p>
    <p>{_html.escape(reason)}</p>
    <code>Source file: {_html.escape(str(source_docx))}</code>
  </div>
</body>
</html>"""


def render_original_docx(source_docx: Path, pdf_path: Path) -> str | None:
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    html_fallback = pdf_path.with_suffix(".fallback.html")
    if html_fallback.exists():
        html_fallback.unlink()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if not libreoffice:
        html_fallback.write_text(
            _render_fallback_original_html(source_docx, "LibreOffice/soffice is not installed on this machine."),
            encoding="utf-8",
        )
        return "LibreOffice/soffice is not installed on this machine."

    cmd = [
        libreoffice,
        "--headless", "--nologo", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        "--convert-to", "pdf",
        "--outdir", str(pdf_path.parent),
        str(source_docx),
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    expected_pdf = pdf_path.parent / f"{source_docx.stem}.pdf"
    if completed.returncode != 0 or not expected_pdf.exists():
        reason = (completed.stderr or completed.stdout or "LibreOffice conversion failed.").strip()
        html_fallback.write_text(_render_fallback_original_html(source_docx, reason), encoding="utf-8")
        if expected_pdf.exists() and expected_pdf != pdf_path:
            try:
                expected_pdf.unlink()
            except OSError:
                pass
        return reason

    if expected_pdf != pdf_path:
        if pdf_path.exists():
            pdf_path.unlink()
        expected_pdf.rename(pdf_path)
    return None


def render_original_pages(pdf_path: Path, pages_dir: Path) -> list[str]:
    pdftoppm = shutil.which("pdftoppm")
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True, exist_ok=True)
    if not pdftoppm or not pdf_path.exists():
        return []

    output_prefix = pages_dir / "page"
    completed = subprocess.run(
        [pdftoppm, "-png", "-r", "144", str(pdf_path), str(output_prefix)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        shutil.rmtree(pages_dir, ignore_errors=True)
        return []

    return sorted(
        [path.name for path in pages_dir.glob("page-*.png")],
        key=lambda name: int(name.rsplit("-", 1)[-1].split(".")[0]),
    )


def extract_archive(
    args: argparse.Namespace,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    archive_path = Path(args.archive).resolve()
    if not archive_path.exists():
        raise FileNotFoundError(archive_path)

    out_dir = (
        Path(args.out_dir).resolve() if args.out_dir
        else ARCHIVE_RUNS_DIR / archive_path.stem
    )
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as tmp:
        entries = iter_docx_entries(archive)
        if args.limit:
            entries = entries[: args.limit]

        if progress_callback:
            progress_callback(0, len(entries))

        tmp_dir = Path(tmp)
        for index, info in enumerate(entries, 1):
            rel = safe_rel_path(info.filename)
            temp_docx = tmp_dir / rel
            temp_docx.parent.mkdir(parents=True, exist_ok=True)
            temp_docx.write_bytes(archive.read(info))

            output = output_path_for_entry(out_dir, info.filename, args.format)
            source_docx = output.with_suffix(".docx")
            original_pdf = output.with_suffix(".original.pdf")
            original_pages_dir = output.with_name(f"{output.stem}.original_pages")
            original_fallback = output.with_suffix(".original.fallback.html")
            debug = output.with_suffix(".ocr.json")
            images_dir = output.with_suffix("").parent / f"{output.stem}_images"
            output.parent.mkdir(parents=True, exist_ok=True)
            error = None

            source_docx.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(temp_docx, source_docx)
            render_error = render_original_docx(source_docx, original_pdf)
            original_pages = render_original_pages(original_pdf, original_pages_dir)

            if args.skip_existing and output.exists() and (not args.keep_artifacts or debug.exists()):
                print(f"[{index}/{len(entries)}] skip {info.filename}", flush=True)
            else:
                print(f"[{index}/{len(entries)}] extract {info.filename}", flush=True)
                try:
                    extract(
                        argparse.Namespace(
                            docx=str(temp_docx),
                            out=str(output),
                            format=args.format,
                            ocr=args.ocr,
                            model=args.model,
                            strong_model=args.strong_model,
                            escalate_threshold=args.escalate_threshold,
                            images_dir=str(images_dir),
                            cache_dir=str(cache_dir),
                            debug_json=str(debug),
                            keep_image_markers=args.keep_image_markers,
                            describe_diagrams=args.describe_diagrams,
                            page_fallback=args.page_fallback,
                        )
                    )
                    error = None
                    if not args.keep_artifacts:
                        if debug.exists():
                            debug.unlink()
                        if images_dir.exists():
                            shutil.rmtree(images_dir)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    output.write_text(f"# Processing error\n\n{error}\n", encoding="utf-8")
                    print(f"[{index}/{len(entries)}] error {info.filename}: {error}", flush=True)

            item: dict[str, Any] = {
                "source": info.filename,
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
            if args.keep_artifacts:
                item["debug"] = str(debug)
                item["images_dir"] = str(images_dir)
            results.append(item)
            if progress_callback:
                progress_callback(index, len(entries))

    write_archive_index(out_dir, archive_path, results)
    manifest = {
        "archive": str(archive_path),
        "archive_name": archive_path.name,
        "archive_title": archive_path.stem,
        "out_dir": str(out_dir),
        "format": args.format,
        "ocr": args.ocr,
        "describe_diagrams": args.describe_diagrams,
        "count": len(results),
        "processed_at": _iso_now(),
        "results": results,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Archive result: {out_dir}", flush=True)
    print(f"Index: {out_dir / 'index.html'}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract all DOCX files from a ZIP archive.")
    parser.add_argument("archive", help="Path to .zip archive.")
    parser.add_argument("--out-dir")
    parser.add_argument("--format", choices=["md", "txt", "json"], default="md")
    parser.add_argument("--ocr", choices=["none", "openai", "hybrid"], default="hybrid")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--strong-model", default=os.getenv("OPENAI_STRONG_MODEL", DEFAULT_STRONG_MODEL))
    parser.add_argument("--escalate-threshold", type=float, default=ESCALATE_THRESHOLD)
    parser.add_argument("--cache-dir")
    parser.add_argument("--keep-image-markers", action="store_true")
    parser.add_argument("--describe-diagrams", action="store_true")
    parser.add_argument("--page-fallback", choices=["off", "auto", "always"], default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser


def main() -> None:
    extract_archive(build_parser().parse_args())


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    main()
