#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from dotenv import load_dotenv
from lxml import etree
from PIL import Image, UnidentifiedImageError


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}

EMU_PER_INCH = 914400
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_STRONG_MODEL = "gpt-5.5"


@dataclass
class ImageRef:
    index: int
    rel_id: str
    target: str
    source_name: str
    saved_path: str
    sha256: str
    width_px: int | None
    height_px: int | None
    format: str | None
    inline: bool
    extent_cx: int | None
    extent_cy: int | None
    suspicious: bool = False
    ocr: dict[str, Any] | None = None


@dataclass
class ExtractionContext:
    docx_path: Path
    out_dir: Path
    images_dir: Path
    cache_dir: Path
    ocr_mode: str
    model: str
    strong_model: str
    escalate_threshold: float
    describe_diagrams: bool
    keep_image_markers: bool
    zip_file: zipfile.ZipFile
    rels: dict[str, str]
    image_counter: int = 0
    images: list[ImageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def qn(ns: str, name: str) -> str:
    return f"{{{NS[ns]}}}{name}"


def is_tag(node: etree._Element, ns: str, name: str) -> bool:
    return node.tag == qn(ns, name)


def local_name(node: etree._Element) -> str:
    return etree.QName(node).localname


def read_relationships(zf: zipfile.ZipFile) -> dict[str, str]:
    rels_path = "word/_rels/document.xml.rels"
    root = etree.fromstring(zf.read(rels_path))
    rels: dict[str, str] = {}
    for rel in root.findall("rel:Relationship", namespaces=NS):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        rel_type = rel.get("Type", "")
        if rel_id and target and rel_type.endswith("/image"):
            rels[rel_id] = resolve_word_target(target)
    return rels


def resolve_word_target(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("word") / target)


def image_mime(fmt: str | None) -> str:
    if fmt == "JPEG":
        return "image/jpeg"
    if fmt == "WEBP":
        return "image/webp"
    if fmt == "GIF":
        return "image/gif"
    return "image/png"


def normalize_image_bytes(raw: bytes, max_side: int = 1600) -> tuple[bytes, str, int | None, int | None]:
    with Image.open(BytesIO(raw)) as im:
        fmt = im.format or "PNG"
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, "WHITE")
        bg.alpha_composite(rgba)
        rgb = bg.convert("RGB")

        width, height = rgb.size
        scale = 1
        if max(width, height) < 800:
            scale = min(8, max(2, 800 // max(width, height)))
        if scale > 1:
            rgb = rgb.resize((width * scale, height * scale), Image.Resampling.LANCZOS)

        if max(rgb.size) > max_side:
            rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        out = BytesIO()
        rgb.save(out, format="PNG")
        return out.getvalue(), fmt, width, height


def safe_filename(name: str, fallback: str) -> str:
    raw = Path(PurePosixPath(name).name).name or fallback
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    return safe or fallback


def extract_image(ctx: ExtractionContext, drawing: etree._Element, inline: bool) -> str:
    rel_ids = drawing.xpath(".//a:blip/@r:embed", namespaces=NS)
    if not rel_ids:
        ctx.warnings.append("Found drawing without embedded image relationship.")
        return "[UNSUPPORTED_IMAGE]"

    rel_id = rel_ids[0]
    target = ctx.rels.get(rel_id)
    if not target:
        ctx.warnings.append(f"Image relationship {rel_id} not found.")
        return f"[MISSING_IMAGE:{rel_id}]"

    ctx.image_counter += 1
    raw = ctx.zip_file.read(target)
    sha = hashlib.sha256(raw).hexdigest()
    try:
        normalized, fmt, width, height = normalize_image_bytes(raw)
        saved_name = f"{ctx.image_counter:03d}_{safe_filename(target, 'image')}.png"
        saved_path = ctx.images_dir / saved_name
        saved_path.write_bytes(normalized)
    except (UnidentifiedImageError, OSError) as exc:
        normalized = b""
        fmt = "UNSUPPORTED"
        width = None
        height = None
        saved_name = f"{ctx.image_counter:03d}_{safe_filename(target, 'image')}.bin"
        saved_path = ctx.images_dir / saved_name
        saved_path.write_bytes(raw)
        ctx.warnings.append(f"Unsupported image format for {target}: {type(exc).__name__}")

    extent = drawing.xpath(".//wp:extent[1]", namespaces=NS)
    cx = int(extent[0].get("cx")) if extent and extent[0].get("cx") else None
    cy = int(extent[0].get("cy")) if extent and extent[0].get("cy") else None
    suspicious = not inline

    ref = ImageRef(
        index=ctx.image_counter,
        rel_id=rel_id,
        target=target,
        source_name=target,
        saved_path=str(saved_path),
        sha256=sha,
        width_px=width,
        height_px=height,
        format=fmt,
        inline=inline,
        extent_cx=cx,
        extent_cy=cy,
        suspicious=suspicious,
    )

    should_ocr = bool(normalized) and should_ocr_image(ctx, ref)
    if should_ocr:
        ref.ocr = run_ocr(ctx, ref, normalized, fmt)

    ctx.images.append(ref)
    return image_replacement(ctx, ref)


def should_ocr_image(ctx: ExtractionContext, ref: ImageRef) -> bool:
    if ctx.ocr_mode == "none":
        return False
    if ctx.ocr_mode == "openai":
        return True
    if ctx.ocr_mode != "hybrid":
        return False

    if ctx.describe_diagrams:
        return True

    if ref.width_px is None or ref.height_px is None:
        return True

    area = ref.width_px * ref.height_px
    long_side = max(ref.width_px, ref.height_px)
    short_side = min(ref.width_px, ref.height_px)
    looks_like_inline_formula = long_side <= 600 and short_side <= 180 and area <= 120_000
    return looks_like_inline_formula


def image_replacement(ctx: ExtractionContext, ref: ImageRef) -> str:
    marker = f"[IMAGE:{ref.index}:{ref.source_name}]"
    if not ref.ocr:
        return marker

    kind = ref.ocr.get("kind") or "unknown"
    text = clean_inline_text(ref.ocr.get("text") or "")
    if kind in {"text", "formula"} and text:
        if ctx.keep_image_markers:
            return f"{text} {marker}"
        return text

    description = clean_inline_text(ref.ocr.get("description") or ref.ocr.get("notes") or "")
    if kind == "diagram" and description and ctx.describe_diagrams:
        return f"[IMAGE:{ref.index}: {description}]"
    if kind not in {"text", "formula"} and description and ctx.describe_diagrams:
        return f"[IMAGE:{ref.index}: {description}]"
    return marker


def clean_inline_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\xa0", " ").split())


def run_ocr(ctx: ExtractionContext, ref: ImageRef, normalized: bytes, fmt: str | None) -> dict[str, Any]:
    cache_path = ctx.cache_dir / f"{ref.sha256}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("notes") != "OPENAI_API_KEY is not set.":
            if should_escalate_ocr(ctx, cached) and cached.get("model") != ctx.strong_model:
                stronger = run_openai_ocr(ctx, ref, normalized, fmt, ctx.strong_model)
                stronger["escalated_from"] = cached.get("model", ctx.model)
                cache_path.write_text(json.dumps(stronger, ensure_ascii=False, indent=2), encoding="utf-8")
                return stronger
            return cached

    if ctx.ocr_mode in {"openai", "hybrid"}:
        result = run_openai_ocr(ctx, ref, normalized, fmt, ctx.model)
        if should_escalate_ocr(ctx, result):
            stronger = run_openai_ocr(ctx, ref, normalized, fmt, ctx.strong_model)
            stronger["escalated_from"] = ctx.model
            result = stronger
    else:
        result = {"kind": "unknown", "text": "", "confidence": 0, "notes": "OCR mode is disabled."}

    if result.get("notes") not in {"OPENAI_API_KEY is not set."}:
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def should_escalate_ocr(ctx: ExtractionContext, result: dict[str, Any]) -> bool:
    if not ctx.strong_model or ctx.strong_model == ctx.model:
        return False
    if result.get("notes") == "OPENAI_API_KEY is not set.":
        return False
    kind = result.get("kind") or "unknown"
    confidence = result.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    return kind in {"unknown", "empty"} or confidence_value < ctx.escalate_threshold


def run_openai_ocr(
    ctx: ExtractionContext,
    ref: ImageRef,
    normalized: bytes,
    fmt: str | None,
    model: str,
) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        ctx.warnings.append("OPENAI_API_KEY is not set; image OCR skipped.")
        return {
            "kind": "unknown",
            "text": "",
            "latex": "",
            "confidence": 0,
            "notes": "OPENAI_API_KEY is not set.",
        }

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(normalized).decode("ascii")
    data_url = f"{image_mime(fmt)};base64,{b64}"
    data_url = f"data:{data_url}"

    task = (
        "Extract text from this image for insertion back into a Russian educational DOCX. "
        "Return only JSON with keys: kind, text, latex, confidence, description, notes. "
        "kind must be one of text, formula, diagram, empty, unknown. "
        "If it is a formula, put a human-editable plain text form in text and LaTeX in latex. "
        "Preserve symbols such as °, ·, =, <, >. "
        "If the image is not primarily text or a formula, set kind to diagram, keep text empty, "
        "and write a short Russian description in description: one or two concise sentences. "
        "Do not invent missing content."
    )

    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": task},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
        )
    except Exception as exc:
        ctx.warnings.append(f"OpenAI OCR failed for {ref.source_name} with {model}: {exc}")
        return {
            "kind": "unknown",
            "text": "",
            "latex": "",
            "confidence": 0,
            "description": "",
            "notes": f"OpenAI OCR failed with {model}: {type(exc).__name__}",
            "model": model,
        }
    output = getattr(response, "output_text", "") or ""
    result = parse_ocr_json(output)
    result["model"] = model
    return result


def parse_ocr_json(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {
            "kind": "unknown",
            "text": text,
            "latex": "",
            "confidence": 0.3,
            "description": "",
            "notes": "Model returned non-JSON output.",
        }
    for key in ("kind", "text", "latex", "description", "notes"):
        data.setdefault(key, "")
    data.setdefault("confidence", None)
    return data


def normalize_word_spacing(text: str) -> str:
    return text.replace("\xa0", " ")


def parse_math(node: etree._Element) -> str:
    texts = node.xpath(".//m:t/text()", namespaces=NS)
    return "".join(texts)


def parse_run(ctx: ExtractionContext, run: etree._Element) -> str:
    parts: list[str] = []
    for child in run:
        if is_tag(child, "w", "t"):
            parts.append(child.text or "")
        elif is_tag(child, "w", "tab"):
            parts.append("\t")
        elif is_tag(child, "w", "br"):
            parts.append("\n")
        elif is_tag(child, "w", "drawing"):
            inline = bool(child.xpath(".//wp:inline", namespaces=NS))
            parts.append(extract_image(ctx, child, inline=inline))
        elif is_tag(child, "w", "object") or is_tag(child, "w", "pict"):
            parts.append("[UNSUPPORTED_LEGACY_OBJECT]")
            ctx.warnings.append("Found legacy Word object/picture; fallback rendering may be needed.")
        elif child.tag in {qn("m", "oMath"), qn("m", "oMathPara")}:
            math_text = parse_math(child)
            parts.append(math_text or "[OFFICE_MATH]")
    return "".join(parts)


def parse_paragraph(ctx: ExtractionContext, paragraph: etree._Element) -> str:
    parts: list[str] = []
    for child in paragraph:
        if is_tag(child, "w", "r"):
            parts.append(parse_run(ctx, child))
        elif child.tag in {qn("m", "oMath"), qn("m", "oMathPara")}:
            parts.append(parse_math(child))
        elif is_tag(child, "w", "hyperlink"):
            for run in child.findall("w:r", namespaces=NS):
                parts.append(parse_run(ctx, run))
    return normalize_word_spacing("".join(parts))


def iter_block_children(parent: etree._Element) -> list[etree._Element]:
    return [child for child in parent if child.tag in {qn("w", "p"), qn("w", "tbl")}]


def parse_cell_blocks(ctx: ExtractionContext, cell: etree._Element) -> list[Any]:
    blocks: list[Any] = []
    for child in iter_block_children(cell):
        if is_tag(child, "w", "p"):
            text = parse_paragraph(ctx, child)
            if text.strip():
                blocks.append({"type": "paragraph", "text": text})
        elif is_tag(child, "w", "tbl"):
            blocks.append(parse_table(ctx, child))
    return blocks


def parse_table(ctx: ExtractionContext, table: etree._Element) -> dict[str, Any]:
    rows: list[list[list[Any]]] = []
    for row in table.findall("w:tr", namespaces=NS):
        parsed_row: list[list[Any]] = []
        for cell in row.findall("w:tc", namespaces=NS):
            parsed_row.append(parse_cell_blocks(ctx, cell))
        rows.append(parsed_row)
    return {"type": "table", "rows": rows}


def parse_document(ctx: ExtractionContext) -> list[Any]:
    root = etree.fromstring(ctx.zip_file.read("word/document.xml"))
    body = root.find("w:body", namespaces=NS)
    if body is None:
        raise ValueError("word/document.xml has no w:body")

    blocks: list[Any] = []
    for child in iter_block_children(body):
        if is_tag(child, "w", "p"):
            text = parse_paragraph(ctx, child)
            if text.strip():
                blocks.append({"type": "paragraph", "text": text})
        elif is_tag(child, "w", "tbl"):
            blocks.append(parse_table(ctx, child))
    return blocks


def cell_to_markdown(cell_blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in cell_blocks:
        if block["type"] == "paragraph":
            parts.append(block["text"].replace("\n", "<br>").strip())
        elif block["type"] == "table":
            parts.append(nested_table_to_inline(block))
    return "<br>".join(part for part in parts if part)


def nested_table_to_inline(table: dict[str, Any]) -> str:
    rows: list[str] = []
    for row in table["rows"]:
        cells = [cell_to_markdown(cell) for cell in row]
        rows.append(" | ".join(cell for cell in cells if cell))
    return "<br>".join(row for row in rows if row)


def escape_md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def markdown_table_lines(table: dict[str, Any]) -> list[str]:
    rows = table["rows"]
    if not rows:
        return []
    max_cols = max((len(row) for row in rows), default=0)
    rendered_rows: list[list[str]] = []
    for row in rows:
        rendered = [escape_md_cell(cell_to_markdown(cell)) for cell in row]
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
            lines.extend(markdown_table_lines(block))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def cell_to_plain_text(cell_blocks: list[Any], indent: int = 0) -> str:
    parts: list[str] = []
    for block in cell_blocks:
        if block["type"] == "paragraph":
            text = normalize_plain_text(block["text"])
            if text:
                parts.append(text)
        elif block["type"] == "table":
            nested = plain_table_lines(block, indent=indent)
            parts.append("\n".join(nested))
    return "\n".join(part for part in parts if part)


def normalize_plain_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines()]
    return "\n".join(line for line in lines if line)


def looks_like_key_value_table(table: dict[str, Any]) -> bool:
    rows = table["rows"]
    if not rows:
        return False
    two_col_rows = [row for row in rows if len(row) == 2]
    if len(two_col_rows) < max(1, len(rows) // 2):
        return False
    for row in two_col_rows:
        left = cell_to_plain_text(row[0]).strip()
        if not left:
            return False
    return True


def plain_table_lines(table: dict[str, Any], indent: int = 0) -> list[str]:
    prefix = " " * indent
    rows = table["rows"]
    if not rows:
        return []

    if looks_like_key_value_table(table):
        lines: list[str] = []
        for row in rows:
            if len(row) == 1:
                value = cell_to_plain_text(row[0], indent=indent).strip()
                if value:
                    lines.append(f"{prefix}{value}")
                continue

            key = cell_to_plain_text(row[0], indent=indent).replace("\n", " ").strip()
            value = cell_to_plain_text(row[1], indent=indent + 2).strip()
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
        cells = [cell_to_plain_text(cell, indent=indent).replace("\n", " / ").strip() for cell in row]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(f"{prefix}" + " | ".join(cells))
    return lines


def render_plain_text(blocks: list[Any]) -> str:
    lines: list[str] = []
    for block in blocks:
        if block["type"] == "paragraph":
            text = normalize_plain_text(block["text"])
            if text:
                lines.extend(text.splitlines())
                lines.append("")
        elif block["type"] == "table":
            lines.extend(plain_table_lines(block))
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def render_json_result(blocks: list[Any], ctx: ExtractionContext, fallback: dict[str, Any] | None) -> dict[str, Any]:
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


def extract(args: argparse.Namespace) -> None:
    load_dotenv()
    source = Path(args.docx).resolve()
    default_work_dir = Path("work").resolve()
    default_runs_dir = default_work_dir / "runs"
    default_cache_dir = default_work_dir / "cache" / "ocr"
    default_runs_dir.mkdir(parents=True, exist_ok=True)
    default_cache_dir.mkdir(parents=True, exist_ok=True)

    default_suffix = {"md": ".md", "txt": ".txt", "json": ".json"}[args.format]
    out = Path(args.out).resolve() if args.out else default_runs_dir / f"{source.stem}{default_suffix}"
    out_dir = out.parent
    images_dir = Path(args.images_dir).resolve() if args.images_dir else default_runs_dir / f"{source.stem}_images"
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
        suspicious = any(image.suspicious for image in ctx.images) or any("legacy" in w.lower() for w in ctx.warnings)
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
    parser.add_argument("--out", help="Output file path. Defaults to input name with .md.")
    parser.add_argument("--format", choices=["md", "txt", "json"], default="md")
    parser.add_argument("--ocr", choices=["none", "openai", "hybrid"], default="hybrid")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--strong-model", default=os.getenv("OPENAI_STRONG_MODEL", DEFAULT_STRONG_MODEL))
    parser.add_argument("--escalate-threshold", type=float, default=0.85)
    parser.add_argument("--images-dir", help="Directory for extracted normalized images.")
    parser.add_argument("--cache-dir", help="Directory for OCR cache JSON files.")
    parser.add_argument("--debug-json", help="Path for diagnostic JSON.")
    parser.add_argument("--keep-image-markers", action="store_true")
    parser.add_argument("--describe-diagrams", action="store_true")
    parser.add_argument("--page-fallback", choices=["off", "auto", "always"], default="auto")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    extract(args)


if __name__ == "__main__":
    main()
