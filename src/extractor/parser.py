from __future__ import annotations

from typing import Any

from lxml import etree

from src.extractor.context import NS, ExtractionContext, is_tag, qn
from src.extractor.images import extract_image


def _normalize_word_spacing(text: str) -> str:
    return text.replace("\xa0", " ")


def _parse_math(node: etree._Element) -> str:
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
            math_text = _parse_math(child)
            parts.append(math_text or "[OFFICE_MATH]")
    return "".join(parts)


def parse_paragraph(ctx: ExtractionContext, paragraph: etree._Element) -> str:
    parts: list[str] = []
    for child in paragraph:
        if is_tag(child, "w", "r"):
            parts.append(parse_run(ctx, child))
        elif child.tag in {qn("m", "oMath"), qn("m", "oMathPara")}:
            parts.append(_parse_math(child))
        elif is_tag(child, "w", "hyperlink"):
            for run in child.findall("w:r", namespaces=NS):
                parts.append(parse_run(ctx, run))
    return _normalize_word_spacing("".join(parts))


def _iter_block_children(parent: etree._Element) -> list[etree._Element]:
    return [child for child in parent if child.tag in {qn("w", "p"), qn("w", "tbl")}]


def _parse_cell_blocks(ctx: ExtractionContext, cell: etree._Element) -> list[Any]:
    blocks: list[Any] = []
    for child in _iter_block_children(cell):
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
            parsed_row.append(_parse_cell_blocks(ctx, cell))
        rows.append(parsed_row)
    return {"type": "table", "rows": rows}


def parse_document(ctx: ExtractionContext) -> list[Any]:
    root = etree.fromstring(ctx.zip_file.read("word/document.xml"))
    body = root.find("w:body", namespaces=NS)
    if body is None:
        raise ValueError("word/document.xml has no w:body")

    blocks: list[Any] = []
    for child in _iter_block_children(body):
        if is_tag(child, "w", "p"):
            text = parse_paragraph(ctx, child)
            if text.strip():
                blocks.append({"type": "paragraph", "text": text})
        elif is_tag(child, "w", "tbl"):
            blocks.append(parse_table(ctx, child))
    return blocks
