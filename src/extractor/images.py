from __future__ import annotations

import base64
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from src.extractor.context import NS, ExtractionContext, ImageRef, qn


def read_relationships(zf) -> dict[str, str]:
    rels_path = "word/_rels/document.xml.rels"
    from lxml import etree
    root = etree.fromstring(zf.read(rels_path))
    rels: dict[str, str] = {}
    for rel in root.findall("rel:Relationship", namespaces=NS):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        rel_type = rel.get("Type", "")
        if rel_id and target and rel_type.endswith("/image"):
            rels[rel_id] = _resolve_word_target(target)
    return rels


def _resolve_word_target(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("word") / target)


def _image_mime(fmt: str | None) -> str:
    if fmt == "JPEG":
        return "image/jpeg"
    if fmt == "WEBP":
        return "image/webp"
    if fmt == "GIF":
        return "image/gif"
    return "image/png"


def normalize_image_bytes(
    raw: bytes, max_side: int = 1600
) -> tuple[bytes, str, int | None, int | None]:
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


def extract_image(ctx: ExtractionContext, drawing, inline: bool) -> str:
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

    should_ocr = bool(normalized) and _should_ocr_image(ctx, ref)
    if should_ocr:
        ref.ocr = run_ocr(ctx, ref, normalized, fmt)

    ctx.images.append(ref)
    return image_replacement(ctx, ref)


def _should_ocr_image(ctx: ExtractionContext, ref: ImageRef) -> bool:
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
    text = _clean_inline_text(ref.ocr.get("text") or "")
    if kind in {"text", "formula"} and text:
        if ctx.keep_image_markers:
            return f"{text} {marker}"
        return text

    description = _clean_inline_text(ref.ocr.get("description") or ref.ocr.get("notes") or "")
    if kind == "diagram" and description and ctx.describe_diagrams:
        return f"[IMAGE:{ref.index}: {description}]"
    if kind not in {"text", "formula"} and description and ctx.describe_diagrams:
        return f"[IMAGE:{ref.index}: {description}]"
    return marker


def _clean_inline_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\xa0", " ").split())


def run_ocr(
    ctx: ExtractionContext,
    ref: ImageRef,
    normalized: bytes,
    fmt: str | None,
) -> dict[str, Any]:
    cache_path = ctx.cache_dir / f"{ref.sha256}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("notes") != "OPENAI_API_KEY is not set.":
            if _should_escalate(ctx, cached) and cached.get("model") != ctx.strong_model:
                stronger = _run_openai_ocr(ctx, ref, normalized, fmt, ctx.strong_model)
                stronger["escalated_from"] = cached.get("model", ctx.model)
                cache_path.write_text(json.dumps(stronger, ensure_ascii=False, indent=2), encoding="utf-8")
                return stronger
            return cached

    if ctx.ocr_mode in {"openai", "hybrid"}:
        result = _run_openai_ocr(ctx, ref, normalized, fmt, ctx.model)
        if _should_escalate(ctx, result):
            stronger = _run_openai_ocr(ctx, ref, normalized, fmt, ctx.strong_model)
            stronger["escalated_from"] = ctx.model
            result = stronger
    else:
        result = {"kind": "unknown", "text": "", "confidence": 0, "notes": "OCR mode is disabled."}

    if result.get("notes") not in {"OPENAI_API_KEY is not set."}:
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _should_escalate(ctx: ExtractionContext, result: dict[str, Any]) -> bool:
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


def _run_openai_ocr(
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
    data_url = f"data:{_image_mime(fmt)};base64,{b64}"

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
    result = _parse_ocr_json(output)
    result["model"] = model
    return result


def _parse_ocr_json(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            # Model returned a JSON array or scalar instead of an object
            data = {
                "kind": "unknown",
                "text": str(data),
                "latex": "",
                "confidence": 0.3,
                "description": "",
                "notes": f"Model returned unexpected JSON type: {type(data).__name__}.",
            }
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
