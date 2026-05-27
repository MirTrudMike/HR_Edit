#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import docx_extract


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


def write_index(out_dir: Path, archive_path: Path, results: list[dict[str, str]]) -> None:
    md_lines = [
        f"# {archive_path.name}",
        "",
        f"Processed DOCX files: {len(results)}",
        "",
    ]
    for idx, item in enumerate(results, 1):
        source = item["source"]
        output = Path(item["output"])
        rel_output = output.relative_to(out_dir).as_posix()
        md_lines.extend(
            [
                f"## {idx}. {source}",
                "",
                f"- Result: [{rel_output}]({rel_output})",
                "",
            ]
        )

    (out_dir / "index.md").write_text("\n".join(md_lines), encoding="utf-8")
    docs: list[dict[str, str]] = []
    for item in results:
        output = Path(item["output"])
        source = item["source"]
        try:
            content = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        docs.append(
            {
                "source": source,
                "title": PurePosixPath(source).stem,
                "pathParts": list(PurePosixPath(source).parts),
                "output": output.relative_to(out_dir).as_posix(),
                "content": content,
            }
        )

    docs_json = json.dumps(docs, ensure_ascii=False)
    fallback_lines: list[str] = []
    last_folder = None
    for doc in docs:
        parts = doc["pathParts"]
        folder = " / ".join(parts[:-1]) or "Root"
        if folder != last_folder:
            fallback_lines.append(f'<details class="tree-group" open><summary class="folder">▾ {html.escape(folder)}</summary><div class="tree-children">')
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
    html_doc = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef2f7;
      --sidebar: #101827;
      --sidebar-panel: #172033;
      --sidebar-border: #26344d;
      --panel: #ffffff;
      --border: #d6deeb;
      --text: #162033;
      --muted: #6a7688;
      --muted-on-dark: #a9b6ca;
      --accent: #2563eb;
      --accent-strong: #1d4ed8;
      --accent-soft: #eaf1ff;
      --folder: #7dd3fc;
      --code: #f4f7fb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .app {
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
      height: 100vh;
    }
    aside {
      border-right: 1px solid var(--sidebar-border);
      background: linear-gradient(180deg, #101827 0%, #121a2b 100%);
      color: #edf4ff;
      overflow: auto;
      padding: 18px;
    }
    main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      height: 100vh;
    }
    .brand {
      margin-bottom: 18px;
    }
    .brand h1 {
      margin: 0 0 4px;
      font-size: 19px;
      line-height: 1.25;
      color: #ffffff;
    }
    .brand p {
      margin: 0;
      color: var(--muted-on-dark);
      font-size: 13px;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
    }
    .toolbar-title {
      min-width: 0;
      flex: 1;
    }
    .toolbar-title h2 {
      margin: 0;
      font-size: 19px;
      line-height: 1.25;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .toolbar-title p {
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    button {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      font-size: 13px;
      cursor: pointer;
    }
    button:hover { border-color: #aeb8c8; }
    button.primary {
      background: var(--accent-strong);
      border-color: var(--accent-strong);
      color: #fff;
    }
    .zoom-controls {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #f8fafc;
    }
    .zoom-controls button {
      width: 30px;
      height: 30px;
      padding: 0;
      border: 0;
      background: transparent;
      font-size: 16px;
      line-height: 1;
    }
    .zoom-controls button:hover {
      background: #e8eef7;
    }
    .zoom-value {
      min-width: 46px;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .search {
      width: 100%;
      border: 1px solid #33435f;
      border-radius: 6px;
      padding: 9px 10px;
      font-size: 14px;
      margin-bottom: 12px;
      background: #0d1423;
      color: #f8fbff;
      outline: none;
    }
    .search::placeholder {
      color: #8fa0b9;
    }
    .search:focus {
      border-color: var(--folder);
      box-shadow: 0 0 0 3px rgba(125, 211, 252, 0.15);
    }
    .tree {
      font-size: 14px;
    }
    .tree-group {
      margin: 8px 0;
    }
    .folder {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 7px;
      margin: 12px 0 5px;
      color: var(--folder);
      font-weight: 750;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      border-top: 1px solid rgba(148, 163, 184, 0.2);
      padding-top: 12px;
      background: transparent;
      border-left: 0;
      border-right: 0;
      border-bottom: 0;
      cursor: pointer;
      text-align: left;
    }
    .folder:first-of-type {
      border-top: 0;
      padding-top: 0;
    }
    .folder:hover {
      color: #d6f3ff;
    }
    .folder-toggle {
      width: 14px;
      display: inline-block;
      color: #c5e9ff;
      transition: transform 0.14s ease;
    }
    .tree-group.collapsed .folder-toggle {
      transform: rotate(-90deg);
    }
    .tree-children {
      margin-left: 10px;
      padding-left: 10px;
      border-left: 1px solid rgba(148, 163, 184, 0.18);
    }
    .tree-group.collapsed .tree-children {
      display: none;
    }
    .doc {
      width: 100%;
      display: block;
      text-align: left;
      border: 0;
      background: transparent;
      border-radius: 6px;
      padding: 9px 10px;
      line-height: 1.3;
      color: #dce7f6;
      text-decoration: none;
      border-left: 3px solid transparent;
      margin: 2px 0;
    }
    .doc:hover {
      background: rgba(255, 255, 255, 0.07);
      color: #ffffff;
    }
    .doc.active {
      background: rgba(37, 99, 235, 0.22);
      color: #ffffff;
      font-weight: 650;
      border-left-color: var(--folder);
    }
    .preview-shell {
      overflow: auto;
      padding: 24px;
    }
    .preview {
      max-width: 1180px;
      min-height: calc(100vh - 94px);
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 30px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.08);
      overflow: hidden;
    }
    .preview h1, .preview h2, .preview h3 {
      margin: 22px 0 10px;
      line-height: 1.25;
    }
    .preview h1:first-child, .preview h2:first-child, .preview h3:first-child {
      margin-top: 0;
    }
    .preview p {
      line-height: 1.55;
      margin: 10px 0;
    }
    .preview table {
      width: 100%;
      border-collapse: collapse;
      margin: 14px 0;
      font-size: 14px;
      display: block;
      overflow-x: auto;
      max-width: 100%;
    }
    .preview th, .preview td {
      border: 1px solid var(--border);
      padding: 9px 10px;
      vertical-align: top;
      min-width: 110px;
    }
    .preview th {
      background: #f6f8fb;
      font-weight: 650;
    }
    .preview tr:nth-child(even) td {
      background: #fbfcff;
    }
    .nested-table-wrap {
      max-width: 100%;
      overflow-x: auto;
      margin: 8px 0;
      border: 1px solid var(--border);
      border-radius: 6px;
    }
    .preview table.nested-table {
      display: table;
      width: max-content;
      min-width: 100%;
      margin: 0;
      border: 0;
      font-size: 13px;
      box-shadow: none;
    }
    .preview table.nested-table td {
      min-width: 72px;
      padding: 7px 8px;
      white-space: nowrap;
    }
    .preview table.nested-table td:first-child {
      font-weight: 650;
      background: #f3f7fc;
      white-space: normal;
      min-width: 120px;
    }
    .preview pre {
      white-space: pre-wrap;
      background: var(--code);
      border: 1px solid var(--border);
      padding: 12px;
      border-radius: 6px;
      overflow: auto;
    }
    .status {
      color: var(--muted);
      font-size: 12px;
      min-width: 92px;
      text-align: right;
    }
    @media (max-width: 860px) {
      .app { grid-template-columns: 1fr; height: auto; }
      aside { height: 42vh; border-right: 0; border-bottom: 1px solid var(--sidebar-border); }
      main { height: 58vh; }
      .preview-shell { padding: 12px; }
      .preview { padding: 18px; min-height: auto; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <h1>__ARCHIVE__</h1>
        <p>__COUNT__ DOCX files processed</p>
      </div>
      <input id="search" class="search" type="search" placeholder="Search documents">
      <div id="tree" class="tree">__TREE_FALLBACK__</div>
    </aside>
    <main>
      <div class="toolbar">
        <div class="toolbar-title">
          <h2 id="title"></h2>
          <p id="path"></p>
        </div>
        <button id="copyText">Copy text</button>
        <button id="copyMarkdown" class="primary">Copy Markdown</button>
        <div class="zoom-controls" aria-label="Preview zoom">
          <button id="zoomOut" type="button" title="Уменьшить preview">−</button>
          <span id="zoomValue" class="zoom-value">100%</span>
          <button id="zoomIn" type="button" title="Увеличить preview">+</button>
        </div>
        <span id="status" class="status"></span>
      </div>
      <div class="preview-shell">
        <article id="preview" class="preview"></article>
      </div>
    </main>
  </div>
  <script id="docs-data" type="application/json">__DOCS_JSON__</script>
  <script>
    const docs = JSON.parse(document.getElementById('docs-data').textContent);
    const tree = document.getElementById('tree');
    const search = document.getElementById('search');
    const preview = document.getElementById('preview');
    const title = document.getElementById('title');
    const path = document.getElementById('path');
    const status = document.getElementById('status');
    const zoomValue = document.getElementById('zoomValue');
    let activeIndex = 0;
    const collapsedFolders = new Set();
    let previewZoom = Number(localStorage.getItem('docxPreviewZoom') || '100');

    function clampZoom(value) {
      return Math.min(180, Math.max(70, value));
    }

    function applyZoom() {
      previewZoom = clampZoom(previewZoom);
      preview.style.fontSize = previewZoom + '%';
      zoomValue.textContent = previewZoom + '%';
      localStorage.setItem('docxPreviewZoom', String(previewZoom));
    }

    function escapeHtml(value) {
      return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function decodeHtmlEntities(value) {
      const textarea = document.createElement('textarea');
      textarea.innerHTML = value;
      return textarea.value;
    }

    function normalizePreviewText(value) {
      return decodeHtmlEntities(value)
        .replace(/<\s+(\d+)>/g, '<$1>')
        .replace(/<\s+(\d+)>\s*°/g, '<$1>°');
    }

    function inlineMarkdown(value) {
      return escapeHtml(normalizePreviewText(value))
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    }

    function normalizeBreaks(value) {
      return value
        .replaceAll('&lt;br&gt;', '\n')
        .replaceAll('&lt;br/&gt;', '\n')
        .replaceAll('&lt;br /&gt;', '\n')
        .replaceAll('<br>', '\n')
        .replaceAll('<br/>', '\n')
        .replaceAll('<br />', '\n')
        .replaceAll('\\n', '\n');
    }

    function splitMarkdownRow(row) {
      const body = row.trim().replace(/^\|/, '').replace(/\|$/, '');
      const cells = [];
      let current = '';
      let escaped = false;
      for (const char of body) {
        if (escaped) {
          current += char;
          escaped = false;
          continue;
        }
        if (char === '\\') {
          escaped = true;
          continue;
        }
        if (char === '|') {
          cells.push(current.trim());
          current = '';
          continue;
        }
        current += char;
      }
      cells.push(current.trim());
      return cells;
    }

    function pipeCount(value) {
      let count = 0;
      let escaped = false;
      for (const char of value) {
        if (escaped) {
          escaped = false;
          continue;
        }
        if (char === '\\') {
          escaped = true;
          continue;
        }
        if (char === '|') count++;
      }
      return count;
    }

    function renderNestedTable(lines) {
      const rows = lines
        .filter(line => pipeCount(line) > 0)
        .map(line => splitMarkdownRow(line.includes('|') && !line.trim().startsWith('|') ? '|' + line + '|' : line));
      if (!rows.length) return '';
      return '<div class="nested-table-wrap"><table class="nested-table"><tbody>' +
        rows.map(row => '<tr>' + row.map(cell => '<td>' + inlineMarkdown(cell) + '</td>').join('') + '</tr>').join('') +
        '</tbody></table></div>';
    }

    function renderCellContent(cell) {
      const normalized = normalizeBreaks(cell);
      const lines = normalized.split('\n').map(line => line.trim()).filter(Boolean);
      const pipeLines = lines.filter(line => pipeCount(line) > 0);
      if (pipeLines.length >= 2) {
        const before = [];
        const tableLines = [];
        const after = [];
        let seenTable = false;
        let doneTable = false;
        for (const line of lines) {
          if (!doneTable && pipeCount(line) > 0) {
            seenTable = true;
            tableLines.push(line);
          } else if (!seenTable) {
            before.push(line);
          } else {
            doneTable = true;
            after.push(line);
          }
        }
        return [
          ...before.map(line => '<p>' + inlineMarkdown(line) + '</p>'),
          renderNestedTable(tableLines),
          ...after.map(line => '<p>' + inlineMarkdown(line) + '</p>'),
        ].join('');
      }
      return inlineMarkdown(normalized).replaceAll('\n', '<br>');
    }

    function markdownToHtml(md) {
      const lines = md.replace(/\r\n/g, '\n').split('\n');
      const html = [];
      let paragraph = [];
      function flushParagraph() {
        if (paragraph.length) {
          html.push('<p>' + inlineMarkdown(paragraph.join(' ')) + '</p>');
          paragraph = [];
        }
      }
      for (let i = 0; i < lines.length; i++) {
        const line = normalizeBreaks(lines[i]);
        if (!line.trim()) {
          flushParagraph();
          continue;
        }
        if (/^\|.*\|$/.test(line.trim())) {
          flushParagraph();
          const tableLines = [];
          while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
            tableLines.push(lines[i].trim());
            i++;
          }
          i--;
          const rows = tableLines
            .filter((row, idx) => !(idx === 1 && /^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(row)))
            .map(row => splitMarkdownRow(row));
          html.push('<table><tbody>' + rows.map(row => '<tr>' + row.map(cell => '<td>' + renderCellContent(cell) + '</td>').join('') + '</tr>').join('') + '</tbody></table>');
          continue;
        }
        const heading = line.match(/^(#{1,3})\s+(.+)$/);
        if (heading) {
          flushParagraph();
          const level = heading[1].length;
          html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
          continue;
        }
        if (/^-\s+/.test(line)) {
          flushParagraph();
          html.push('<p>' + inlineMarkdown(line.replace(/^-\s+/, '• ')) + '</p>');
          continue;
        }
        paragraph.push(line);
      }
      flushParagraph();
      return html.join('\n');
    }

    function renderTree() {
      const query = search.value.trim().toLowerCase();
      tree.innerHTML = '';
      const groups = new Map();
      docs.forEach((doc, index) => {
        const haystack = doc.source.toLowerCase();
        if (query && !haystack.includes(query)) return;
        const folder = doc.pathParts.slice(0, -1).join(' / ') || 'Root';
        if (!groups.has(folder)) groups.set(folder, []);
        groups.get(folder).push({ doc, index });
      });

      groups.forEach((items, folder) => {
        const group = document.createElement('div');
        group.className = 'tree-group' + (collapsedFolders.has(folder) && !query ? ' collapsed' : '');

        const folderButton = document.createElement('button');
        folderButton.className = 'folder';
        folderButton.type = 'button';
        folderButton.title = collapsedFolders.has(folder) ? 'Expand folder' : 'Collapse folder';
        folderButton.innerHTML = '<span class="folder-toggle">▾</span><span></span>';
        folderButton.lastChild.textContent = folder;
        folderButton.addEventListener('click', () => {
          if (collapsedFolders.has(folder)) {
            collapsedFolders.delete(folder);
          } else {
            collapsedFolders.add(folder);
          }
          renderTree();
        });
        group.appendChild(folderButton);

        const children = document.createElement('div');
        children.className = 'tree-children';
        items.forEach(({ doc, index }) => {
        const button = document.createElement('button');
        button.className = 'doc' + (index === activeIndex ? ' active' : '');
        button.type = 'button';
        button.textContent = doc.pathParts[doc.pathParts.length - 1].replace(/\\.docx$/i, '');
        button.addEventListener('click', () => selectDoc(index));
          children.appendChild(button);
        });
        group.appendChild(children);
        tree.appendChild(group);
      });
    }

    function selectDoc(index) {
      activeIndex = index;
      const doc = docs[index];
      title.textContent = doc.title;
      path.textContent = doc.source;
      preview.innerHTML = markdownToHtml(doc.content || '');
      status.textContent = '';
      renderTree();
    }

    async function copy(value, message) {
      await navigator.clipboard.writeText(value);
      status.textContent = message;
      setTimeout(() => status.textContent = '', 1600);
    }

    document.getElementById('copyMarkdown').addEventListener('click', () => {
      copy(docs[activeIndex].content || '', 'Markdown copied');
    });
    document.getElementById('copyText').addEventListener('click', () => {
      const text = preview.innerText || '';
      copy(text, 'Text copied');
    });
    document.getElementById('zoomOut').addEventListener('click', () => {
      previewZoom -= 10;
      applyZoom();
    });
    document.getElementById('zoomIn').addEventListener('click', () => {
      previewZoom += 10;
      applyZoom();
    });
    search.addEventListener('input', renderTree);
    applyZoom();
    renderTree();
    if (docs.length) selectDoc(0);
  </script>
</body>
</html>
"""
    html_doc = (
        html_doc
        .replace("__TITLE__", html.escape(archive_path.name))
        .replace("__ARCHIVE__", html.escape(archive_path.name))
        .replace("__COUNT__", str(len(results)))
        .replace("__DOCS_JSON__", html.escape(docs_json, quote=False))
        .replace("__TREE_FALLBACK__", tree_fallback)
    )
    (out_dir / "index.html").write_text(html_doc, encoding="utf-8")


def extract_archive(args: argparse.Namespace) -> None:
    archive_path = Path(args.archive).resolve()
    if not archive_path.exists():
        raise FileNotFoundError(archive_path)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path("work/archive_runs") / archive_path.stem
    out_dir = out_dir.resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else Path("work/cache/ocr").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as tmp:
        entries = iter_docx_entries(archive)
        if args.limit:
            entries = entries[: args.limit]

        tmp_dir = Path(tmp)
        for index, info in enumerate(entries, 1):
            rel = safe_rel_path(info.filename)
            temp_docx = tmp_dir / rel
            temp_docx.parent.mkdir(parents=True, exist_ok=True)
            temp_docx.write_bytes(archive.read(info))

            output = output_path_for_entry(out_dir, info.filename, args.format)
            debug = output.with_suffix(".ocr.json")
            images_dir = output.with_suffix("").parent / f"{output.stem}_images"
            output.parent.mkdir(parents=True, exist_ok=True)
            error = None

            if args.skip_existing and output.exists() and (not args.keep_artifacts or debug.exists()):
                print(f"[{index}/{len(entries)}] skip {info.filename}", flush=True)
            else:
                print(f"[{index}/{len(entries)}] extract {info.filename}", flush=True)
                try:
                    docx_extract.extract(
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
                    output.write_text(f"# Ошибка обработки\n\n{error}\n", encoding="utf-8")
                    print(f"[{index}/{len(entries)}] error {info.filename}: {error}", flush=True)

            item = {"source": info.filename, "output": str(output)}
            if error:
                item["error"] = error
            if args.keep_artifacts:
                item["debug"] = str(debug)
                item["images_dir"] = str(images_dir)
            results.append(item)

    write_index(out_dir, archive_path, results)
    manifest = {
        "archive": str(archive_path),
        "out_dir": str(out_dir),
        "format": args.format,
        "ocr": args.ocr,
        "describe_diagrams": args.describe_diagrams,
        "count": len(results),
        "results": results,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Archive result: {out_dir}", flush=True)
    print(f"Index: {out_dir / 'index.html'}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract all DOCX files from a ZIP archive.")
    parser.add_argument("archive", help="Path to .zip archive.")
    parser.add_argument("--out-dir", help="Output directory. Defaults to work/archive_runs/<archive-name>.")
    parser.add_argument("--format", choices=["md", "txt", "json"], default="md")
    parser.add_argument("--ocr", choices=["none", "openai", "hybrid"], default="hybrid")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", docx_extract.DEFAULT_MODEL))
    parser.add_argument("--strong-model", default=os.getenv("OPENAI_STRONG_MODEL", docx_extract.DEFAULT_STRONG_MODEL))
    parser.add_argument("--escalate-threshold", type=float, default=0.85)
    parser.add_argument("--cache-dir", help="Shared OCR cache directory.")
    parser.add_argument("--keep-image-markers", action="store_true")
    parser.add_argument("--describe-diagrams", action="store_true")
    parser.add_argument("--page-fallback", choices=["off", "auto", "always"], default="auto")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--clean", action="store_true", help="Delete output directory before processing.")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep debug JSON and extracted images.")
    parser.add_argument("--limit", type=int, help="Process only first N DOCX files, useful for tests.")
    return parser


def main() -> None:
    extract_archive(build_parser().parse_args())


if __name__ == "__main__":
    main()
