from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

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


def qn(ns: str, name: str) -> str:
    return f"{{{NS[ns]}}}{name}"


def is_tag(node: etree._Element, ns: str, name: str) -> bool:
    return node.tag == qn(ns, name)


def local_name(node: etree._Element) -> str:
    return etree.QName(node).localname


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
