# Data Flow

## Вход

Локальный файл:

```text
test.docx
```

Будущий серверный вариант:

```text
POST /extract
Content-Type: multipart/form-data
file=<docx>
format=txt|md|json
ocr=none|hybrid|openai
```

Или для будущего расширения/локального приложения:

```text
POST /extract-url
url=https://.../file.docx
format=txt|md|json
```

## Основные промежуточные данные

### 1. Relationships

Словарь связей из `word/_rels/document.xml.rels`:

```json
{
  "R13b317b400eb4b5c": "media/image2.png",
  "Rf10ccb7757fb4a19": "media/image3.png"
}
```

Он нужен, чтобы по `r:embed` внутри XML найти реальный файл изображения.

### 2. Images

Каждое найденное изображение сохраняется как объект:

```json
{
  "index": 2,
  "rel_id": "R13b317b400eb4b5c",
  "source_name": "media/image2.png",
  "saved_path": "test_images/002_image2.png.png",
  "sha256": "...",
  "width_px": 37,
  "height_px": 25,
  "format": "PNG",
  "inline": true,
  "ocr": {
    "kind": "text",
    "text": "30°",
    "latex": "",
    "confidence": 0.98,
    "model": "gpt-4.1-mini"
  }
}
```

### 3. Blocks

Главный внутренний формат результата - список блоков:

```json
[
  {
    "type": "paragraph",
    "text": "Обычный абзац"
  },
  {
    "type": "table",
    "rows": [
      [
        [
          {
            "type": "paragraph",
            "text": "Левая ячейка"
          }
        ],
        [
          {
            "type": "paragraph",
            "text": "Правая ячейка"
          }
        ]
      ]
    ]
  }
]
```

Важный момент: к моменту формирования `blocks` все inline-картинки уже заменены либо на OCR-текст, либо на маркер `[IMAGE:index:path]`.

## Выходы

### TXT

Чистый текст для копирования и ручного редактирования:

```text
Объяснение решения:
  Циферблат делится на 12 частей по 30°.
  Например, от 12 до 3 — три части: 30° · 3 = 90°
```

Команда:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format txt --out result.txt
```

Если `--out` не указан, файл будет создан в `work/runs/`.

### Markdown

Структурированный Markdown с таблицами:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format md --out result.md
```

Если `--out` не указан, файл будет создан в `work/runs/`.

### JSON

Полный результат для будущего приложения или API:

```json
{
  "text": "...готовый txt...",
  "markdown": "...готовый md...",
  "blocks": [],
  "images": [],
  "warnings": [],
  "fallback": null
}
```

Команда:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format json --out result.json
```

Если `--out` не указан, файл будет создан в `work/runs/`.

## Серверная функция в будущем

Для приложения или расширения серверной частью будет не CLI, а функция с такой логикой:

```python
result = extract_docx(
    docx_bytes,
    output_format="txt",
    ocr_mode="hybrid",
)
```

Она должна возвращать:

```json
{
  "text": "...",
  "markdown": "...",
  "images": [...],
  "warnings": [...]
}
```

CLI сейчас является оболочкой вокруг этой механики.
