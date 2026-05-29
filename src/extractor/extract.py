from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path

from src.config import DEFAULT_MODEL, DEFAULT_STRONG_MODEL, ESCALATE_THRESHOLD
from src.extractor.context import ExtractionContext
from src.extractor.images import read_relationships
from src.extractor.parser import parse_document
from src.extractor.renderer import (
    render_json_result,
    render_markdown,
    render_pages_fallback,
    render_plain_text,
    write_debug_json,
)


def extract(args: argparse.Namespace) -> None:
    source = Path(args.docx).resolve()
    default_work_dir = Path("work").resolve()
    default_runs_dir = default_work_dir / "runs"
    default_cache_dir = default_work_dir / "cache" / "ocr"
    default_runs_dir.mkdir(parents=True, exist_ok=True)
    default_cache_dir.mkdir(parents=True, exist_ok=True)

    default_suffix = {"md": ".md", "txt": ".txt", "json": ".json"}[args.format]
    out = Path(args.out).resolve() if args.out else default_runs_dir / f"{source.stem}{default_suffix}"
    out_dir = out.parent
    images_dir = (
        Path(args.images_dir).resolve() if args.images_dir
        else default_runs_dir / f"{source.stem}_images"
    )
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else default_cache_dir
    images_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as zf:
        ctx = ExtractionContext(
            docx_path=source,
            out_dir=out_dir,
            images_dir=images_dir,
            cache_dir=cache_dir,
            ocr_mode=args.ocr,
            model=args.model,
            strong_model=args.strong_model,
            escalate_threshold=args.escalate_threshold,
            describe_diagrams=args.describe_diagrams,
            keep_image_markers=args.keep_image_markers,
            zip_file=zf,
            rels=read_relationships(zf),
        )
        blocks = parse_document(ctx)
        suspicious = any(img.suspicious for img in ctx.images) or any(
            "legacy" in w.lower() for w in ctx.warnings
        )
        fallback = None
        if args.page_fallback == "always" or (args.page_fallback == "auto" and suspicious):
            fallback = render_pages_fallback(source, out_dir)
            if fallback and not fallback.get("available"):
                ctx.warnings.append(fallback["reason"])

        if args.format == "json":
            result = render_json_result(blocks, ctx, fallback)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.format == "txt":
            out.write_text(render_plain_text(blocks), encoding="utf-8")
        else:
            out.write_text(render_markdown(blocks), encoding="utf-8")

        debug_path = Path(args.debug_json).resolve() if args.debug_json else out.with_suffix(".ocr.json")
        write_debug_json(debug_path, ctx, blocks, fallback)

        print(f"Wrote: {out}")
        print(f"Debug: {debug_path}")
        print(f"Images: {images_dir}")
        if ctx.warnings:
            print("Warnings:")
            for warning in ctx.warnings:
                print(f"- {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract DOCX text with inline image OCR.")
    parser.add_argument("docx", help="Path to .docx file.")
    parser.add_argument("--out", help="Output file path.")
    parser.add_argument("--format", choices=["md", "txt", "json"], default="md")
    parser.add_argument("--ocr", choices=["none", "openai", "hybrid"], default="hybrid")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--strong-model", default=os.getenv("OPENAI_STRONG_MODEL", DEFAULT_STRONG_MODEL))
    parser.add_argument("--escalate-threshold", type=float, default=ESCALATE_THRESHOLD)
    parser.add_argument("--images-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--debug-json")
    parser.add_argument("--keep-image-markers", action="store_true")
    parser.add_argument("--describe-diagrams", action="store_true")
    parser.add_argument("--page-fallback", choices=["off", "auto", "always"], default="auto")
    return parser


def main() -> None:
    extract(build_parser().parse_args())


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    main()
