from __future__ import annotations

import html
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


# ---------------------------------------------------------------------------
# Markdown → HTML converter (for archive viewer)
# ---------------------------------------------------------------------------

def _md_normalize_text(value: str) -> str:
    value = re.sub(r"<\s+(\d+)>", r"<\1>", value)
    value = re.sub(r"<\s+(\d+)>\s*°", r"<\1>°", value)
    return value


def _md_inline(value: str) -> str:
    escaped = html.escape(_md_normalize_text(value), quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _md_norm_breaks(value: str) -> str:
    for token in ("&lt;br&gt;", "&lt;br/&gt;", "&lt;br /&gt;", "<br>", "<br/>", "<br />"):
        value = value.replace(token, "\n")
    return value.replace("\\n", "\n")


def _md_pipe_count(value: str) -> int:
    count = 0
    escaped = False
    for ch in value:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            count += 1
    return count


def _md_split_row(row: str) -> list[str]:
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells: list[str] = []
    current = ""
    escaped = False
    for ch in body:
        if escaped:
            current += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += ch
    cells.append(current.strip())
    return cells


def _md_nested_table(lines: list[str]) -> str:
    rows = []
    for ln in lines:
        if _md_pipe_count(ln) > 0:
            norm = ln if ln.strip().startswith("|") else "|" + ln + "|"
            rows.append(_md_split_row(norm))
    if not rows:
        return ""
    inner = "".join(
        "<tr>" + "".join(f"<td>{_md_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="nested-table-wrap"><table class="nested-table"><tbody>{inner}</tbody></table></div>'


def _md_cell(cell: str) -> str:
    normalized = _md_norm_breaks(cell)
    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]
    if sum(1 for ln in lines if _md_pipe_count(ln) > 0) >= 2:
        before: list[str] = []
        table_lines: list[str] = []
        after: list[str] = []
        seen = done = False
        for ln in lines:
            if not done and _md_pipe_count(ln) > 0:
                seen = True
                table_lines.append(ln)
            elif not seen:
                before.append(ln)
            else:
                done = True
                after.append(ln)
        return (
            "".join(f"<p>{_md_inline(ln)}</p>" for ln in before)
            + _md_nested_table(table_lines)
            + "".join(f"<p>{_md_inline(ln)}</p>" for ln in after)
        )
    return _md_inline(normalized).replace("\n", "<br>")


_MD_SEP = re.compile(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$")


def md_to_html(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + _md_inline(" ".join(para)) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = _md_norm_breaks(lines[i])
        if not line.strip():
            flush()
            i += 1
            continue
        if re.match(r"^\|.*\|$", line.strip()):
            flush()
            table_lines: list[str] = []
            while i < len(lines) and re.match(r"^\|.*\|$", lines[i].strip()):
                table_lines.append(lines[i].strip())
                i += 1
            rows = [
                _md_split_row(row)
                for idx, row in enumerate(table_lines)
                if not (idx == 1 and _MD_SEP.match(row))
            ]
            inner = "".join(
                "<tr>" + "".join(f"<td>{_md_cell(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            out.append(f"<table><tbody>{inner}</tbody></table>")
            continue
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"^-\s+", line):
            flush()
            out.append("<p>" + _md_inline(line.replace("- ", "• ", 1)) + "</p>")
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Archive index.html generation
# ---------------------------------------------------------------------------

_ARCHIVE_VIEWER_CSS = r"""
    :root {
      --bg:              #f2f4f6;
      --sb-bg:           #0f172a;
      --sb-bg2:          #1a2540;
      --sb-border:       #1e2d42;
      --sb-text:         #e2e8f0;
      --sb-muted:        #94a3b8;
      --sb-hover:        rgba(255,255,255,.06);
      --sb-active:       rgba(59,130,246,.18);
      --sb-active-brd:   #7dd3fc;
      --sb-folder:       #7dd3fc;
      --sb-search-bg:    #0b1221;
      --sb-search-brd:   #2d3f5c;
      --panel:           #ffffff;
      --border:          #e2e8f0;
      --text:            #191c1e;
      --text-2:          #44474c;
      --muted:           #75777d;
      --accent:          #1d2b3e;
      --accent-soft:     #d5e3fd;
      --code-bg:         #f8fafc;
      --ph-bg:           #f8fafc;
    }
    .ms {
      font-family: 'Material Symbols Outlined';
      font-weight: normal; font-style: normal;
      font-size: 20px; line-height: 1;
      display: inline-block; white-space: nowrap;
      direction: ltr; vertical-align: middle;
      font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    [hidden] { display: none !important; }
    body {
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      overflow: hidden;
    }

    /* ── App shell ── */
    .app {
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      height: 100vh;
    }
    .app.sidebar-collapsed { grid-template-columns: 48px minmax(0, 1fr); }

    /* ── Sidebar ── */
    aside.sidebar {
      background: linear-gradient(180deg, var(--sb-bg) 0%, var(--sb-bg2) 100%);
      border-right: 1px solid var(--sb-border);
      color: var(--sb-text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }
    .sb-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 12px 4px;
      flex-shrink: 0;
    }
    .sb-brand {
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }
    .sb-brand-name {
      font-size: 14px;
      font-weight: 600;
      color: #fff;
      letter-spacing: -0.01em;
      white-space: nowrap;
    }
    .btn-home {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px; height: 28px;
      border-radius: 5px;
      border: 1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.05);
      color: var(--sb-muted);
      cursor: pointer;
      text-decoration: none;
      flex-shrink: 0;
      transition: background .12s, color .12s;
    }
    .btn-home:hover { background: rgba(255,255,255,.1); color: #fff; }
    .btn-sb-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px; height: 28px;
      border-radius: 5px;
      border: 1px solid rgba(255,255,255,.1);
      background: transparent;
      color: var(--sb-muted);
      cursor: pointer;
      flex-shrink: 0;
      transition: background .12s, color .12s;
      font-family: inherit;
    }
    .btn-sb-toggle:hover:not(:disabled) { background: rgba(255,255,255,.08); color: #fff; }
    .btn-sb-toggle:disabled { opacity: .3; cursor: default; }
    .sb-archive-name {
      font-size: 11px;
      color: var(--sb-muted);
      padding: 4px 12px 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex-shrink: 0;
    }
    .sb-search-wrap { padding: 0 8px 8px; flex-shrink: 0; }
    .sb-search {
      width: 100%;
      background: var(--sb-search-bg);
      border: 1px solid var(--sb-search-brd);
      border-radius: 5px;
      padding: 7px 10px;
      font-size: 13px;
      color: #f0f6ff;
      outline: none;
      font-family: inherit;
    }
    .sb-search::placeholder { color: #4d6180; }
    .sb-search:focus { border-color: var(--sb-folder); box-shadow: 0 0 0 2px rgba(125,211,252,.12); }
    .sb-tree-wrap {
      flex: 1;
      overflow-y: auto;
      padding: 0 4px 16px;
      scrollbar-width: thin;
      scrollbar-color: #2a3a55 transparent;
    }
    .sb-tree-wrap::-webkit-scrollbar { width: 4px; }
    .sb-tree-wrap::-webkit-scrollbar-thumb { background: #2a3a55; border-radius: 2px; }

    /* ── Collapsed sidebar ── */
    .sidebar-collapsed aside.sidebar { align-items: center; }
    .sidebar-collapsed .sb-brand,
    .sidebar-collapsed .sb-archive-name,
    .sidebar-collapsed .sb-search-wrap,
    .sidebar-collapsed .sb-tree-wrap { display: none; }
    .sidebar-collapsed .sb-header {
      padding: 12px 0;
      justify-content: center;
    }

    /* ── Tree ── */
    .tree { font-size: 13px; }
    .tree-group { margin: 1px 0; }
    .folder {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 6px 8px;
      background: transparent;
      border: 0;
      color: var(--sb-folder);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .06em;
      cursor: pointer;
      border-radius: 4px;
      text-align: left;
      font-family: inherit;
    }
    .folder:hover { background: var(--sb-hover); color: #a5d8f8; }
    .folder-toggle {
      display: inline-block;
      color: #c5e9ff;
      transition: transform .13s ease;
      font-size: 15px;
      line-height: 1;
    }
    .tree-group.collapsed .folder-toggle { transform: rotate(-90deg); }
    .tree-children {
      padding-left: 8px;
      border-left: 1px solid rgba(148,163,184,.16);
      margin-left: 12px;
    }
    .tree-group.collapsed .tree-children { display: none; }
    .doc {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 6px 8px;
      background: transparent;
      border: 0;
      border-left: 2px solid transparent;
      color: #afc4dd;
      font-size: 13px;
      cursor: pointer;
      border-radius: 0 4px 4px 0;
      text-align: left;
      line-height: 1.3;
      font-family: inherit;
      text-decoration: none;
      margin: 1px 0;
    }
    .doc:hover { background: var(--sb-hover); color: #e2eeff; }
    .doc.active {
      background: var(--sb-active);
      color: #fff;
      border-left-color: var(--sb-folder);
      font-weight: 500;
    }

    /* ── Main ── */
    main {
      display: flex;
      flex-direction: column;
      height: 100vh;
      min-width: 0;
    }

    /* ── Toolbar ── */
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 16px;
      height: 50px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
      flex-shrink: 0;
    }
    .toolbar-title { flex: 1; min-width: 0; }
    .toolbar-doc-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .toolbar-doc-path {
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 1px;
    }
    .toolbar-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      height: 30px;
      padding: 0 11px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--panel);
      color: var(--text-2);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
      font-family: inherit;
      transition: border-color .1s, background .1s;
    }
    .toolbar-btn:hover { border-color: #94a3b8; background: #f8fafc; }
    .toolbar-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
    .status-msg { font-size: 11px; color: var(--muted); white-space: nowrap; min-width: 80px; text-align: right; }

    /* ── Viewer ── */
    .viewer {
      flex: 1;
      display: grid;
      grid-template-columns: 1fr;
      min-height: 0;
      overflow: hidden;
    }
    .viewer.split { grid-template-columns: minmax(0,1fr) minmax(0,1fr); }

    /* ── Panel ── */
    .panel, .original-pane {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 14px;
      height: 34px;
      border-bottom: 1px solid var(--border);
      background: var(--ph-bg);
      flex-shrink: 0;
    }
    .original-pane .panel-header { border-left: 1px solid var(--border); }
    .panel-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
    }
    .zoom-row { display: inline-flex; align-items: center; gap: 1px; }
    .zoom-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px; height: 22px;
      border: 0;
      border-radius: 4px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      font-family: inherit;
      transition: background .1s, color .1s;
    }
    .zoom-btn:hover { background: #e2e8f0; color: var(--text); }
    .zoom-val {
      font-size: 11px;
      color: var(--muted);
      min-width: 34px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }

    /* ── Preview scroll ── */
    .preview-scroll {
      flex: 1;
      overflow: auto;
      padding: 24px;
      min-height: 0;
    }
    .preview-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .preview-scroll::-webkit-scrollbar-track { background: transparent; }
    .preview-scroll::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 3px; }
    .preview {
      max-width: 860px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 32px 40px;
      box-shadow: 0 1px 4px rgba(0,0,0,.04);
      min-height: 400px;
      flex-shrink: 0;
    }
    .preview-content { transform-origin: top left; }

    /* ── Document typography ── */
    .preview h1,.preview h2,.preview h3 {
      font-family: 'Inter', system-ui, sans-serif;
      color: var(--text);
      margin: 24px 0 12px;
      line-height: 1.25;
    }
    .preview h1:first-child,.preview h2:first-child,.preview h3:first-child { margin-top: 0; }
    .preview h1 { font-size: 20px; font-weight: 600; }
    .preview h2 { font-size: 16px; font-weight: 600; }
    .preview h3 { font-size: 14px; font-weight: 500; }
    .preview p {
      font-family: 'Literata', Georgia, serif;
      font-size: 16px;
      line-height: 1.7;
      color: #2d3748;
      margin: 10px 0;
    }
    .preview table {
      width: 100%;
      border-collapse: collapse;
      margin: 16px 0;
      font-size: 13px;
      display: block;
      overflow-x: auto;
      max-width: 100%;
    }
    .preview th,.preview td {
      border-bottom: 1px solid var(--border);
      padding: 9px 12px;
      vertical-align: top;
      min-width: 100px;
      text-align: left;
    }
    .preview th {
      background: #f8fafc;
      font-weight: 500;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--text-2);
      border-bottom: 2px solid var(--border);
    }
    .preview tr:last-child td { border-bottom: 0; }
    .nested-table-wrap {
      overflow-x: auto;
      margin: 8px 0;
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    .preview table.nested-table {
      display: table;
      width: max-content;
      min-width: 100%;
      margin: 0;
      border: 0;
      font-size: 12px;
    }
    .preview table.nested-table td { min-width: 60px; padding: 6px 8px; }
    .preview table.nested-table td:first-child { font-weight: 500; background: #f8fafc; }
    .preview pre {
      white-space: pre-wrap;
      background: var(--code-bg);
      border: 1px solid var(--border);
      padding: 12px;
      border-radius: 4px;
      font-size: 13px;
      overflow: auto;
    }

    /* ── Original pane ── */
    .original-pane { background: #e8edf5; border-left: 1px solid var(--border); }
    .original-pane.hidden { display: none; }
    .original-pane .preview-scroll { background: #e8edf5; padding: 16px; }
    .orig-wrap { flex-shrink: 0; transform-origin: top left; }
    .original-pages { display: flex; flex-direction: column; gap: 16px; transform-origin: top left; }
    .original-page {
      background: #fff;
      border: 1px solid #d1d9e0;
      box-shadow: 0 3px 12px rgba(0,0,0,.08);
      border-radius: 3px;
      overflow: hidden;
    }
    .original-page img { display: block; width: 100%; height: auto; }

    /* ── AI panel grid overrides ── */
    .viewer.ai-open                { grid-template-columns: minmax(0,1fr) 360px; }
    .viewer.split.ai-open          { grid-template-columns: minmax(0,1fr) minmax(0,1fr) 300px; }

    /* ── AI panel ── */
    .ai-pane {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      border-left: 1px solid var(--border);
      background: var(--panel);
    }
    .ai-pane.hidden { display: none; }
    .ai-panel-body {
      flex: 1;
      overflow-y: auto;
      min-height: 0;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .ai-panel-body::-webkit-scrollbar { width: 5px; }
    .ai-panel-body::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 3px; }

    /* ── AI toolbar button ── */
    .btn-ai {
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
      border-color: transparent;
      color: #fff;
    }
    .btn-ai:hover { background: linear-gradient(135deg, #4338ca 0%, #6d28d9 100%); border-color: transparent; }
    .btn-ai.active {
      background: linear-gradient(135deg, #3730a3 0%, #5b21b6 100%);
      border-color: transparent;
      color: #fff;
      box-shadow: inset 0 1px 3px rgba(0,0,0,.2);
    }

    /* ── Prompt selector in AI panel header ── */
    .prompt-select {
      font-size: 11px;
      font-family: inherit;
      color: var(--text-2);
      background: var(--surface-low);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 3px 6px;
      cursor: pointer;
      max-width: 140px;
      outline: none;
    }
    .prompt-select:focus { border-color: #7c3aed; }

    /* ── AI idle state ── */
    .ai-idle {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      text-align: center;
      padding: 24px 16px;
      color: var(--muted);
    }
    .ai-idle-icon {
      font-size: 36px;
      color: #c4b5fd;
      line-height: 1;
    }
    .ai-idle-hint { font-size: 12px; line-height: 1.5; }
    .btn-analyze {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 9px 20px;
      border-radius: 8px;
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
      border: none;
      color: #fff;
      font-size: 13px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      transition: opacity .15s;
      margin-top: 4px;
    }
    .btn-analyze:hover { opacity: .88; }
    .btn-analyze:disabled { opacity: .45; cursor: default; }

    /* ── AI loading state ── */
    .ai-loading {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 14px;
      padding: 24px 16px;
    }
    .ai-dots {
      display: flex; gap: 7px; align-items: center; height: 28px;
    }
    .ai-dots span {
      width: 9px; height: 9px;
      background: #7c3aed;
      border-radius: 50%;
      animation: ai-bounce .75s ease-in-out infinite;
    }
    .ai-dots span:nth-child(2) { animation-delay: .15s; }
    .ai-dots span:nth-child(3) { animation-delay: .30s; }
    @keyframes ai-bounce {
      0%, 80%, 100% { transform: translateY(0); opacity: .35; }
      40%           { transform: translateY(-9px); opacity: 1; }
    }
    .ai-loading-text {
      font-size: 13px;
      color: var(--muted);
      min-height: 1.4em;
      transition: opacity .25s;
    }

    /* ── AI result ── */
    .ai-summary {
      font-size: 12px;
      color: var(--muted);
      padding: 6px 10px;
      background: var(--surface-low);
      border: 1px solid var(--border);
      border-radius: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ai-summary.no-errors { color: #15803d; background: #f0fdf4; border-color: #86efac; }

    .ai-error-list { display: flex; flex-direction: column; gap: 8px; }

    .ai-error-item {
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: var(--surface);
      opacity: 0;
      transform: translateY(6px);
      transition: opacity .25s ease, transform .25s ease;
    }
    .ai-error-item.visible { opacity: 1; transform: none; }

    .ai-error-accent {
      height: 3px;
      width: 100%;
    }
    .ai-error-body { padding: 10px 12px; }
    .ai-error-top {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 7px;
    }
    .ai-error-fragment {
      font-size: 12px;
      font-style: italic;
      color: var(--text-2);
      line-height: 1.45;
      flex: 1;
      word-break: break-word;
    }
    .ai-error-fragment::before { content: '\201C'; }
    .ai-error-fragment::after  { content: '\201D'; }
    .ai-type-badge {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .06em;
      padding: 2px 6px;
      border-radius: 3px;
      white-space: nowrap;
      flex-shrink: 0;
      color: #fff;
    }
    .ai-error-explanation {
      font-size: 12px;
      color: var(--text);
      line-height: 1.5;
      margin-bottom: 5px;
    }
    .ai-error-suggestion {
      font-size: 11px;
      color: var(--text-2);
      background: var(--surface-low);
      border-radius: 4px;
      padding: 5px 8px;
      border-left: 2px solid var(--border-hi);
      line-height: 1.45;
    }
    .ai-error-suggestion::before {
      content: '→ ';
      color: var(--muted);
      font-style: normal;
    }

    /* ── AI error state ── */
    .ai-error-state {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      padding: 24px 16px;
      text-align: center;
    }
    .ai-error-msg { font-size: 12px; color: #dc2626; line-height: 1.5; }
    .btn-retry {
      font-size: 12px;
      color: var(--text-2);
      background: var(--surface-low);
      border: 1px solid var(--border-hi);
      border-radius: 5px;
      padding: 5px 12px;
      cursor: pointer;
      font-family: inherit;
      transition: background .1s;
    }
    .btn-retry:hover { background: var(--bg); }

    /* ── Unsure errors section ── */
    .ai-unsure-section { margin-top: 4px; }
    .ai-unsure-toggle {
      display: flex;
      align-items: center;
      gap: 6px;
      width: 100%;
      padding: 7px 10px;
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 11px;
      color: var(--muted);
      font-weight: 500;
      cursor: pointer;
      text-align: left;
      font-family: inherit;
      transition: background .1s, color .1s;
    }
    .ai-unsure-toggle:hover { background: #f1f5f9; color: var(--text-2); }
    .ai-unsure-list { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
    .ai-error-item.unsure { opacity: 0.55; }
    .ai-error-item.unsure .ai-error-accent { opacity: 0.5; }

    /* ── Re-analyze button ── */
    .btn-reanalyze {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px; height: 24px;
      border: 1px solid transparent;
      border-radius: 4px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      flex-shrink: 0;
      transition: background .1s, color .1s, border-color .1s;
    }
    .btn-reanalyze:hover { background: #ede9fe; color: #7c3aed; border-color: #c4b5fd; }

    /* ── Toolbar sections ── */
    .toolbar-left  { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; overflow: hidden; }
    .toolbar-center { display: flex; align-items: center; flex-shrink: 0; }
    .toolbar-right { display: flex; align-items: center; gap: 8px; flex: 1; justify-content: flex-end; min-width: 0; }
    .panel.hidden { display: none; }

    /* ── View toggle (segmented control) ── */
    .view-toggle-group {
      display: inline-flex;
      background: #f1f5f9;
      border: 1px solid #dde3ec;
      border-radius: 8px;
      padding: 3px;
      gap: 2px;
    }
    .view-toggle-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      height: 28px;
      padding: 0 13px;
      border: 0;
      border-radius: 5px;
      background: transparent;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
      font-family: inherit;
      transition: background .13s, color .13s, box-shadow .13s;
      user-select: none;
    }
    .view-toggle-btn:hover:not(.active) { background: rgba(0,0,0,.05); color: var(--text-2); }
    .view-toggle-btn.active {
      background: var(--accent);
      color: #fff;
      box-shadow: 0 1px 3px rgba(0,0,0,.18);
      font-weight: 600;
    }

    /* ── Responsive ── */
    @media (max-width: 860px) {
      .app { grid-template-columns: 1fr; height: auto; }
      .app.sidebar-collapsed { grid-template-columns: 1fr; }
      aside.sidebar { height: 40vh; border-right: 0; border-bottom: 1px solid var(--sb-border); }
      main { height: 60vh; }
      .viewer { grid-template-columns: 1fr; }
      .preview-scroll { padding: 12px; }
      .preview { padding: 20px; min-height: auto; }
      .original-pane { border-left: 0; border-top: 1px solid var(--border); }
    }
"""

_ARCHIVE_VIEWER_JS = r"""
    const docs          = JSON.parse(document.getElementById('docs-data').textContent);
    const tree          = document.getElementById('tree');
    const search        = document.getElementById('search');
    const previewContent= document.getElementById('previewContent');
    const title         = document.getElementById('title');
    const path          = document.getElementById('path');
    const status        = document.getElementById('status');
    const app           = document.querySelector('.app');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const viewer        = document.getElementById('viewer');
    const originalPane  = document.getElementById('originalPane');
    const originalPages = document.getElementById('originalPages');
    const origWrap      = document.getElementById('origWrap');
    const toggleOriginal= document.getElementById('toggleOriginal');
    const textPane      = document.getElementById('textPane');
    const toggleText    = document.getElementById('toggleText');
    const aiPane        = document.getElementById('aiPane');
    const toggleAi      = document.getElementById('toggleAi');
    let activeIndex = 0;
    const collapsedFolders = new Set();
    let textZoom         = Number(localStorage.getItem('docxTextZoom')        || '100');
    let origZoom         = Number(localStorage.getItem('docxOrigZoom')        || '100');
    let showOriginal     = localStorage.getItem('docxShowOriginal')           === '1';
    let showTextPanel    = localStorage.getItem('docxShowText')               !== '0';
    let showAiPanel      = localStorage.getItem('docxShowAi')                 === '1';
    let sidebarCollapsed = localStorage.getItem('docxSidebarCollapsed')       === '1';
    let sidebarBeforeOriginal = sidebarCollapsed;

    const ICO_OPEN   = '<span class="ms" style="font-size:18px">chevron_left</span>';
    const ICO_CLOSED = '<span class="ms" style="font-size:18px">chevron_right</span>';

    function clampZoom(v) { return Math.min(200, Math.max(50, v)); }

    function applyTextZoom() {
      textZoom = clampZoom(textZoom);
      const scale = textZoom / 100;
      previewContent.style.transform = `scale(${scale})`;
      previewContent.style.width = `calc(${100 / scale}%)`;
      const wrapper = previewContent.parentElement;
      const cs = window.getComputedStyle(wrapper);
      const extraH = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) +
                     parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
      wrapper.style.height = Math.ceil(previewContent.scrollHeight * scale + extraH) + 'px';
      document.getElementById('textZoomValue').textContent = textZoom + '%';
      localStorage.setItem('docxTextZoom', String(textZoom));
    }

    function applyOrigZoom() {
      origZoom = clampZoom(origZoom);
      const scale = origZoom / 100;
      origWrap.style.width = '';
      originalPages.style.width = '';
      originalPages.style.transform = '';
      const naturalW = originalPages.scrollWidth;
      const naturalH = originalPages.scrollHeight;
      originalPages.style.width = naturalW + 'px';
      originalPages.style.transform = `scale(${scale})`;
      origWrap.style.width  = Math.ceil(naturalW * scale) + 'px';
      origWrap.style.height = Math.ceil(naturalH * scale) + 'px';
      document.getElementById('origZoomValue').textContent = origZoom + '%';
      localStorage.setItem('docxOrigZoom', String(origZoom));
    }

    function applySidebar() {
      app.classList.toggle('sidebar-collapsed', sidebarCollapsed);
      sidebarToggle.innerHTML = sidebarCollapsed ? ICO_CLOSED : ICO_OPEN;
      sidebarToggle.setAttribute('aria-pressed', sidebarCollapsed ? 'true' : 'false');
      localStorage.setItem('docxSidebarCollapsed', sidebarCollapsed ? '1' : '0');
    }

    function applyViewerLayout() {
      const contentCount = (showTextPanel ? 1 : 0) + (showOriginal ? 1 : 0);
      textPane.classList.toggle('hidden', !showTextPanel);
      originalPane.classList.toggle('hidden', !showOriginal);
      aiPane.classList.toggle('hidden', !showAiPanel);
      viewer.classList.toggle('split', contentCount === 2);
      viewer.classList.toggle('ai-open', showAiPanel);
      toggleText.classList.toggle('active', showTextPanel);
      toggleOriginal.classList.toggle('active', showOriginal);
      toggleAi.classList.toggle('active', showAiPanel);
      toggleAi.setAttribute('aria-pressed', showAiPanel ? 'true' : 'false');
      localStorage.setItem('docxShowText', showTextPanel ? '1' : '0');
      localStorage.setItem('docxShowOriginal', showOriginal ? '1' : '0');
      localStorage.setItem('docxShowAi', showAiPanel ? '1' : '0');
    }

    function renderOriginalPages(doc) {
      const pages = doc.originalPages || [];
      if (!pages.length) {
        originalPages.innerHTML = '<div style="padding:20px;background:#fff;border:1px solid #e2e8f0;border-radius:4px;"><strong>Оригинал недоступен</strong><div style="color:#75777d;font-size:12px;margin-top:6px;">PDF-рендер LibreOffice не был создан.</div></div>';
        return;
      }
      originalPages.innerHTML = pages.map((page, idx) => (
        `<figure class="original-page"><img src="${page}" alt="Страница ${idx + 1}"></figure>`
      )).join('');
      let pending = originalPages.querySelectorAll('img').length;
      if (!pending) { applyOrigZoom(); return; }
      originalPages.querySelectorAll('img').forEach(img => {
        const done = () => { if (--pending === 0) applyOrigZoom(); };
        img.addEventListener('load', done, { once: true });
        img.addEventListener('error', done, { once: true });
      });
    }

    function renderTree() {
      const query = search.value.trim().toLowerCase();
      tree.innerHTML = '';
      const groups = new Map();
      docs.forEach((doc, index) => {
        const haystack = doc.source.toLowerCase();
        if (query && !haystack.includes(query)) return;
        const folder = doc.pathParts.slice(0, -1).join(' / ') || 'Корень';
        if (!groups.has(folder)) groups.set(folder, []);
        groups.get(folder).push({ doc, index });
      });
      groups.forEach((items, folder) => {
        const group = document.createElement('div');
        group.className = 'tree-group' + (collapsedFolders.has(folder) && !query ? ' collapsed' : '');
        const folderBtn = document.createElement('button');
        folderBtn.className = 'folder';
        folderBtn.type = 'button';
        folderBtn.innerHTML =
          '<span class="folder-toggle ms" style="font-size:15px">expand_more</span>' +
          '<span class="ms" style="font-size:14px;color:#7dd3fc">folder</span>' +
          '<span></span>';
        folderBtn.lastChild.textContent = folder;
        folderBtn.addEventListener('click', () => {
          if (collapsedFolders.has(folder)) collapsedFolders.delete(folder);
          else collapsedFolders.add(folder);
          renderTree();
        });
        group.appendChild(folderBtn);
        const children = document.createElement('div');
        children.className = 'tree-children';
        items.forEach(({ doc, index }) => {
          const btn = document.createElement('button');
          btn.className = 'doc' + (index === activeIndex ? ' active' : '');
          btn.type = 'button';
          btn.innerHTML =
            '<span class="ms" style="font-size:13px;flex-shrink:0;opacity:.7">description</span>' +
            '<span></span>';
          btn.lastChild.textContent = doc.pathParts[doc.pathParts.length - 1].replace(/\.docx$/i, '');
          btn.addEventListener('click', () => selectDoc(index));
          children.appendChild(btn);
        });
        group.appendChild(children);
        tree.appendChild(group);
      });
    }

    function selectDoc(index) {
      activeIndex = index;
      const doc = docs[index];
      title.textContent = doc.title;
      path.textContent  = doc.source;
      previewContent.innerHTML = doc.html || '';
      applyTextZoom();
      renderOriginalPages(doc);
      status.textContent = '';
      if (showAiPanel) {
        const cached = getCachedResult(index);
        if (cached) renderAiResult(cached); else setAiState('idle');
      }
      renderTree();
    }

    async function copy(value, message) {
      await navigator.clipboard.writeText(value);
      status.textContent = message;
      setTimeout(() => status.textContent = '', 1600);
    }

    document.getElementById('copyMarkdown').addEventListener('click', () => copy(docs[activeIndex].content || '', 'Markdown скопирован'));
    document.getElementById('copyText').addEventListener('click', () => {
      let text = previewContent.innerText || '';
      text = text.replace(/\[IMAGE:\d+:\s*([^\]]*)\]/g, '$1');
      text = text.replace(/https?:\/\/\S+/g, '');
      text = text.replace(/\n{3,}/g, '\n\n').trim();
      copy(text, 'Текст скопирован');
    });
    document.getElementById('textZoomOut').addEventListener('click', () => { textZoom -= 10; applyTextZoom(); });
    document.getElementById('textZoomIn').addEventListener('click',  () => { textZoom += 10; applyTextZoom(); });
    document.getElementById('origZoomOut').addEventListener('click', () => { origZoom -= 10; applyOrigZoom(); });
    document.getElementById('origZoomIn').addEventListener('click',  () => { origZoom += 10; applyOrigZoom(); });
    sidebarToggle.addEventListener('click', () => { sidebarCollapsed = !sidebarCollapsed; if (showOriginal) sidebarBeforeOriginal = sidebarCollapsed; applySidebar(); });
    toggleOriginal.addEventListener('click', () => {
      showOriginal = !showOriginal;
      if (showOriginal) { sidebarBeforeOriginal = sidebarCollapsed; sidebarCollapsed = true; }
      else { sidebarCollapsed = sidebarBeforeOriginal; }
      applySidebar();
      applyViewerLayout();
      if (showOriginal && docs[activeIndex]) renderOriginalPages(docs[activeIndex]);
    });
    toggleText.addEventListener('click', () => { showTextPanel = !showTextPanel; applyViewerLayout(); });
    search.addEventListener('input', renderTree);
    applyTextZoom();
    applySidebar();
    applyViewerLayout();
    renderTree();

    // ── AI Panel ─────────────────────────────────────────────────────────────
    const archiveName   = document.querySelector('.app').dataset.archiveName || '';
    const promptSelect  = document.getElementById('promptSelect');
    const modelSelect   = document.getElementById('modelSelect');
    const btnAnalyze    = document.getElementById('btnAnalyze');
    const aiIdleEl      = document.getElementById('aiIdle');
    const aiLoadingEl   = document.getElementById('aiLoading');
    const aiResultEl    = document.getElementById('aiResult');
    const aiErrorEl     = document.getElementById('aiErrorState');
    let aiAnalyzing      = false;
    let lastAnalyzedIndex = -1;
    const aiResultCache    = new Map();
    const btnReAnalyze     = document.getElementById('btnReAnalyze');
    const aiLoadingTextEl  = document.getElementById('aiLoadingText');
    const LOADING_PHRASES  = [
      '🤓 Читаю…', '🧐 Смотрю внимательно…', '💭 Думаю…',
      '🔍 Ищу косяки…', '📝 Проверяю…', '🤔 Хм-м…',
      '💡 Что-то нашёл…', '🧠 Ещё секунду…'
    ];
    let _loadingTimer = null;

    function lsKey(index)       { return 'umnik:' + archiveName + ':' + index; }
    function lsSave(index, res) { try { localStorage.setItem(lsKey(index), JSON.stringify(res)); } catch (_) {} }
    function lsLoad(index)      { try { const v = localStorage.getItem(lsKey(index)); return v ? JSON.parse(v) : null; } catch (_) { return null; } }

    function getCachedResult(index) {
      if (aiResultCache.has(index)) return aiResultCache.get(index);
      const stored = lsLoad(index);
      if (stored) { aiResultCache.set(index, stored); return stored; }
      return null;
    }

    const ERROR_TYPE_LABELS = {
      typo: 'Опечатка', grammar: 'Грамматика', punctuation: 'Пунктуация',
      repetition: 'Повтор', agreement: 'Согласование', formula: 'Формула',
      logic: 'Логика', other: 'Прочее'
    };
    const ERROR_TYPE_COLORS = {
      typo: '#ef4444', grammar: '#f97316', punctuation: '#ca8a04',
      repetition: '#3b82f6', agreement: '#8b5cf6', formula: '#10b981',
      logic: '#f59e0b', other: '#94a3b8'
    };

    async function loadAiModels() {
      try {
        const res  = await fetch('/api/models');
        const data = await res.json();
        const models  = data.models  || [];
        const defId   = data.default || '';
        modelSelect.innerHTML = '';
        models.forEach(m => {
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.name;
          if (m.id === defId) opt.selected = true;
          modelSelect.appendChild(opt);
        });
      } catch (_) {}
    }

    async function loadAiPrompts() {
      try {
        const res  = await fetch('/api/prompts');
        const data = await res.json();
        const prompts = data.prompts || [];
        promptSelect.innerHTML = '';
        prompts.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = p.name;
          if (p.is_default) opt.selected = true;
          promptSelect.appendChild(opt);
        });
      } catch (_) {}
    }

    function applyAiPanel() {
      applyViewerLayout();
    }

    function setAiState(state) {
      aiIdleEl.hidden     = state !== 'idle';
      aiLoadingEl.hidden  = state !== 'loading';
      aiResultEl.hidden   = state !== 'result';
      aiErrorEl.hidden    = state !== 'error';
      btnReAnalyze.hidden = state !== 'result';
      if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
      if (state === 'loading') {
        let i = 0;
        aiLoadingTextEl.textContent = LOADING_PHRASES[0];
        _loadingTimer = setInterval(() => {
          i = (i + 1) % LOADING_PHRASES.length;
          aiLoadingTextEl.textContent = LOADING_PHRASES[i];
        }, 1800);
      }
    }

    function makeErrorItem(err, idx, isUnsure) {
      const color = ERROR_TYPE_COLORS[err.error_type] || ERROR_TYPE_COLORS.other;
      const label = ERROR_TYPE_LABELS[err.error_type] || 'Прочее';
      const item = document.createElement('div');
      item.className = 'ai-error-item' + (isUnsure ? ' unsure' : '');
      item.innerHTML = `
        <div class="ai-error-accent" style="background:${color}"></div>
        <div class="ai-error-body">
          <div class="ai-error-top">
            <div class="ai-error-fragment">${escHtml(err.fragment || '')}</div>
            <span class="ai-type-badge" style="background:${color}">${label}</span>
          </div>
          <div class="ai-error-explanation">${escHtml(err.explanation || '')}</div>
          ${err.suggestion ? `<div class="ai-error-suggestion">${escHtml(err.suggestion)}</div>` : ''}
        </div>`;
      return item;
    }

    function renderAiResult(result) {
      aiResultEl.innerHTML = '';

      const allErrors    = result.errors || [];
      const sureErrors   = allErrors.filter(e => e.confidence !== 'unsure');
      const unsureErrors = allErrors.filter(e => e.confidence === 'unsure');
      const hasAny       = allErrors.length > 0;

      let summaryText;
      if (!hasAny) {
        summaryText = 'Ошибок не найдено';
      } else if (sureErrors.length > 0 && unsureErrors.length > 0) {
        summaryText = `${sureErrors.length} ${sureErrors.length === 1 ? 'ошибка' : 'ошибок'} найдено`;
      } else if (sureErrors.length > 0) {
        summaryText = `${sureErrors.length} ${sureErrors.length === 1 ? 'ошибка' : 'ошибок'} найдено`;
      } else {
        summaryText = 'Явных ошибок нет';
      }

      const summary = document.createElement('div');
      summary.className = 'ai-summary' + (hasAny ? '' : ' no-errors');
      const icon = hasAny ? 'error_outline' : 'check_circle';
      const iconColor = hasAny ? '#f97316' : '#15803d';
      summary.innerHTML = `<span class="ms" style="font-size:15px;color:${iconColor}">${icon}</span>` +
                          `<span>${summaryText}</span>`;
      aiResultEl.appendChild(summary);

      if (sureErrors.length > 0) {
        const list = document.createElement('div');
        list.className = 'ai-error-list';
        sureErrors.forEach((err, idx) => {
          const item = makeErrorItem(err, idx, false);
          list.appendChild(item);
          setTimeout(() => item.classList.add('visible'), 60 + idx * 40);
        });
        aiResultEl.appendChild(list);
      }

      if (unsureErrors.length > 0) {
        const section = document.createElement('div');
        section.className = 'ai-unsure-section';

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'ai-unsure-toggle';
        toggle.innerHTML =
          `<span class="ms" style="font-size:13px">help_outline</span>` +
          `<span>На всякий глянуть (${unsureErrors.length})</span>` +
          `<span class="ms" style="font-size:13px;margin-left:auto" data-arrow>expand_more</span>`;

        const listWrap = document.createElement('div');
        listWrap.className = 'ai-unsure-list';
        listWrap.hidden = true;

        toggle.addEventListener('click', () => {
          listWrap.hidden = !listWrap.hidden;
          toggle.querySelector('[data-arrow]').textContent =
            listWrap.hidden ? 'expand_more' : 'expand_less';
        });

        unsureErrors.forEach((err, idx) => {
          const item = makeErrorItem(err, idx, true);
          listWrap.appendChild(item);
          setTimeout(() => item.classList.add('visible'), 60 + idx * 40);
        });

        section.appendChild(toggle);
        section.appendChild(listWrap);
        aiResultEl.appendChild(section);
      }

      setAiState('result');
    }

    function escHtml(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    async function analyzeDoc() {
      if (aiAnalyzing) return;
      aiAnalyzing = true;
      lastAnalyzedIndex = activeIndex;
      setAiState('loading');
      btnAnalyze.disabled = true;
      try {
        const res = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            archive_name: archiveName,
            doc_index: activeIndex,
            prompt_id: promptSelect.value || null,
            model_id: modelSelect.value || null,
          }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `HTTP ${res.status}`);
        }
        const result = await res.json();
        renderAiResult(result);
        aiResultCache.set(lastAnalyzedIndex, result);
        lsSave(lastAnalyzedIndex, result);
      } catch (err) {
        aiErrorEl.querySelector('.ai-error-msg').textContent = err.message || 'Что-то пошло не так';
        setAiState('error');
      } finally {
        aiAnalyzing = false;
        btnAnalyze.disabled = false;
      }
    }

    toggleAi.addEventListener('click', () => {
      showAiPanel = !showAiPanel;
      applyAiPanel();
      if (showAiPanel) {
        loadAiModels();
        loadAiPrompts();
        const cached = getCachedResult(activeIndex);
        if (cached) renderAiResult(cached); else setAiState('idle');
      }
    });
    btnAnalyze.addEventListener('click', analyzeDoc);
    document.getElementById('aiRetry').addEventListener('click', analyzeDoc);
    btnReAnalyze.addEventListener('click', analyzeDoc);

    loadAiModels();
    loadAiPrompts();
    applyAiPanel();
    if (showAiPanel) setAiState('idle');
    if (docs.length) selectDoc(0);
"""


def write_archive_index(out_dir: Path, archive_path: Path, results: list[dict[str, Any]]) -> None:
    md_lines = [f"# {archive_path.name}", "", f"Processed DOCX files: {len(results)}", ""]
    for idx, item in enumerate(results, 1):
        source = item["source"]
        output = Path(item["output"])
        rel_output = output.relative_to(out_dir).as_posix()
        md_lines.extend([f"## {idx}. {source}", "", f"- Result: [{rel_output}]({rel_output})", ""])
    (out_dir / "index.md").write_text("\n".join(md_lines), encoding="utf-8")

    docs: list[dict[str, Any]] = []
    for item in results:
        output = Path(item["output"])
        source = item["source"]
        source_docx = item.get("source_docx", "")
        original_pdf = item.get("original_pdf", "")
        original_pages = item.get("original_pages", [])
        original_fallback = item.get("original_fallback", "")
        try:
            content = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        docs.append({
            "source": source,
            "title": PurePosixPath(source).stem,
            "pathParts": list(PurePosixPath(source).parts),
            "output": output.relative_to(out_dir).as_posix(),
            "sourceDocx": Path(source_docx).relative_to(out_dir).as_posix() if source_docx else "",
            "originalPdf": Path(original_pdf).relative_to(out_dir).as_posix() if original_pdf else "",
            "originalPages": [Path(page).relative_to(out_dir).as_posix() for page in original_pages],
            "originalFallback": Path(original_fallback).relative_to(out_dir).as_posix() if original_fallback else "",
            "originalError": item.get("original_error", ""),
            "content": content,
            "html": md_to_html(content),
        })

    docs_json = json.dumps(docs, ensure_ascii=False)
    fallback_lines: list[str] = []
    last_folder = None
    for doc in docs:
        parts = doc["pathParts"]
        folder = " / ".join(parts[:-1]) or "Root"
        if folder != last_folder:
            fallback_lines.append(
                f'<details class="tree-group" open><summary class="folder">▾ {html.escape(folder)}</summary><div class="tree-children">'
            )
            if last_folder is not None:
                fallback_lines.insert(-1, "</div></details>")
            last_folder = folder
        fallback_lines.append(
            '<a class="doc" href="{href}">{label}</a>'.format(
                href=html.escape(doc["output"], quote=True),
                label=html.escape(Path(parts[-1]).stem),
            )
        )
    if last_folder is not None:
        fallback_lines.append("</div></details>")
    tree_fallback = "\n".join(fallback_lines)

    html_doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(archive_path.name)} — HYURazberesh</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Literata:ital,opsz,wght@0,7..72,400;1,7..72,400&family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0&display=swap" rel="stylesheet">
  <style>{_ARCHIVE_VIEWER_CSS}</style>
</head>
<body>
  <div class="app" data-archive-name="{html.escape(out_dir.name, quote=True)}">

    <!-- ── Sidebar ── -->
    <aside class="sidebar">
      <div class="sb-header">
        <div class="sb-brand">
          <a href="/" class="btn-home" title="На главную">
            <span class="ms" style="font-size:16px">arrow_back</span>
          </a>
          <span class="sb-brand-name">HYURazberesh</span>
        </div>
        <button id="sidebarToggle" type="button" class="btn-sb-toggle" aria-label="Свернуть панель">
          <span class="ms" style="font-size:18px">chevron_left</span>
        </button>
      </div>
      <div class="sb-archive-name">{html.escape(archive_path.name)}</div>
      <div class="sb-search-wrap">
        <input id="search" class="sb-search" type="search" placeholder="Поиск документов…">
      </div>
      <div class="sb-tree-wrap">
        <div id="tree" class="tree">{tree_fallback}</div>
      </div>
    </aside>

    <!-- ── Main ── -->
    <main>
      <div class="toolbar">
        <div class="toolbar-left">
          <div class="toolbar-title">
            <div id="title" class="toolbar-doc-name"></div>
            <div id="path"  class="toolbar-doc-path"></div>
          </div>
        </div>
        <div class="toolbar-center">
          <div class="view-toggle-group">
            <button id="toggleText" type="button" class="view-toggle-btn active">
              <span class="ms" style="font-size:14px">article</span>Разбор
            </button>
            <button id="toggleOriginal" type="button" class="view-toggle-btn">
              <span class="ms" style="font-size:14px">chrome_reader_mode</span>Оригинал
            </button>
          </div>
        </div>
        <div class="toolbar-right">
          <button id="copyText" class="toolbar-btn">
            <span class="ms" style="font-size:15px">content_copy</span>Текст
          </button>
          <button id="copyMarkdown" class="toolbar-btn">
            <span class="ms" style="font-size:15px">code</span>Markdown
          </button>
          <button id="toggleAi" type="button" class="toolbar-btn btn-ai" aria-pressed="false">
            <span class="ms" style="font-size:15px">auto_awesome</span>Умник
          </button>
          <span id="status" class="status-msg"></span>
        </div>
      </div>

      <div id="viewer" class="viewer">

        <!-- Markdown preview panel -->
        <div id="textPane" class="panel">
          <div class="panel-header">
            <span class="panel-label">Предпросмотр</span>
            <div class="zoom-row">
              <button id="textZoomOut" class="zoom-btn" title="Уменьшить">
                <span class="ms" style="font-size:14px">remove</span>
              </button>
              <span id="textZoomValue" class="zoom-val">100%</span>
              <button id="textZoomIn" class="zoom-btn" title="Увеличить">
                <span class="ms" style="font-size:14px">add</span>
              </button>
            </div>
          </div>
          <div class="preview-scroll">
            <article class="preview">
              <div id="previewContent" class="preview-content"></div>
            </article>
          </div>
        </div>

        <!-- Original document panel -->
        <aside id="originalPane" class="original-pane hidden" aria-label="Оригинал документа">
          <div class="panel-header">
            <span class="panel-label">Оригинал</span>
            <div class="zoom-row">
              <button id="origZoomOut" class="zoom-btn" title="Уменьшить">
                <span class="ms" style="font-size:14px">remove</span>
              </button>
              <span id="origZoomValue" class="zoom-val">100%</span>
              <button id="origZoomIn" class="zoom-btn" title="Увеличить">
                <span class="ms" style="font-size:14px">add</span>
              </button>
            </div>
          </div>
          <div class="preview-scroll">
            <div id="origWrap" class="orig-wrap">
              <div id="originalPages" class="original-pages"></div>
            </div>
          </div>
        </aside>

        <!-- AI panel -->
        <aside id="aiPane" class="ai-pane hidden" aria-label="Умник">
          <div class="panel-header">
            <div style="display:flex;align-items:center;gap:7px">
              <span class="ms" style="font-size:15px;color:#7c3aed">auto_awesome</span>
              <span class="panel-label">Умник</span>
            </div>
            <div style="display:flex;align-items:center;gap:4px">
              <select id="modelSelect" class="prompt-select" title="Выбрать модель"></select>
              <select id="promptSelect" class="prompt-select" title="Выбрать промт"></select>
              <button id="btnReAnalyze" class="btn-reanalyze" type="button" hidden title="Запустить снова">
                <span class="ms" style="font-size:15px">refresh</span>
              </button>
            </div>
          </div>
          <div class="ai-panel-body">

            <!-- idle state -->
            <div id="aiIdle" class="ai-idle">
              <span class="ms ai-idle-icon">auto_awesome</span>
              <div class="ai-idle-hint">Нажми — и умник разберёт текст на ошибки</div>
              <button id="btnAnalyze" class="btn-analyze" type="button">
                <span class="ms" style="font-size:15px">play_arrow</span>
                Разобрать
              </button>
            </div>

            <!-- loading state -->
            <div id="aiLoading" class="ai-loading" hidden>
              <div class="ai-dots"><span></span><span></span><span></span></div>
              <div id="aiLoadingText" class="ai-loading-text">🤓 Читаю…</div>
            </div>

            <!-- result state -->
            <div id="aiResult" hidden></div>

            <!-- error state -->
            <div id="aiErrorState" class="ai-error-state" hidden>
              <span class="ms" style="font-size:28px;color:#fca5a5">error_outline</span>
              <div class="ai-error-msg"></div>
              <button id="aiRetry" class="btn-retry" type="button">Давай ещё раз</button>
            </div>

          </div>
        </aside>

      </div>
    </main>
  </div>
  <script id="docs-data" type="application/json">{docs_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")}</script>
  <script>{_ARCHIVE_VIEWER_JS}</script>
</body>
</html>"""
    (out_dir / "index.html").write_text(html_doc, encoding="utf-8")
