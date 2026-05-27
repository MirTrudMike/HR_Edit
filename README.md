# DOCX Text Extractor

Локальный прототип для извлечения текста из `.docx` с сохранением порядка inline-изображений и OCR для картинок с формулами.

## Быстрый запуск

Без OCR, только структурный разбор и маркеры изображений:

```bash
python3 docx_extract.py test.docx --ocr none --format md
```

Гибридный режим: текст берется из DOCX XML, маленькие inline-картинки отправляются в OpenAI OCR, крупные изображения остаются маркерами:

```bash
OPENAI_API_KEY=... python3 docx_extract.py test.docx --ocr hybrid --format md
```

Готовый чистый TXT:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format txt
```

Готовый Markdown:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format md
```

Можно положить ключ в `.env`:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_STRONG_MODEL=gpt-5.5
```

## Полезные опции

```bash
python3 docx_extract.py test.docx \
  --ocr hybrid \
  --format md \
  --out work/runs/test_extracted.md \
  --debug-json work/runs/test_extracted.ocr.json \
  --images-dir work/runs/test_images
```

- `--ocr none|openai|hybrid` - режим OCR.
- `--model` - основная модель для OCR простых inline-картинок.
- `--strong-model` - модель для автоматической перепроверки сомнительных результатов.
- `--escalate-threshold` - порог уверенности для перепроверки, по умолчанию `0.85`.
- `--format md|txt|json` - формат результата.
- `--keep-image-markers` - оставлять маркер рядом с распознанным текстом.
- `--describe-diagrams` - отправлять крупные нетекстовые изображения в модель и вставлять короткое описание.
- `--page-fallback off|auto|always` - заготовка fallback-рендера через LibreOffice.

Пример с описанием нетекстовых изображений:

```bash
python3 docx_extract.py test.docx --ocr hybrid --format txt --describe-diagrams
```

## Рабочие файлы

По умолчанию все результаты складываются в `work/`:

- `work/runs/` - выходные `txt`, `md`, `json`, debug JSON и извлеченные картинки.
- `work/archive_runs/` - результаты обработки ZIP-архивов.
- `work/cache/ocr/` - кэш OCR по SHA-256 изображения.

Папка `work/` добавлена в `.gitignore`. Ее можно удалить целиком, когда промежуточные результаты больше не нужны.

## Обработка ZIP-архива

Архивный режим берет ZIP, находит внутри все `.docx`, прогоняет каждый через тот же extractor и создает индекс:

```bash
python3 archive_extract.py "/path/to/full 02.1-05-00055.zip" \
  --format md \
  --ocr hybrid \
  --describe-diagrams \
  --clean
```

Результаты появятся в:

```text
work/archive_runs/<archive-name>/
  index.html
  index.md
  manifest.json
  ...результаты с сохранением структуры папок архива...
```

`index.html` - основная страница для работы: слева дерево папок как в архиве, справа preview выбранного Markdown, есть кнопки Copy text и Copy Markdown. По умолчанию в папке результата остаются только `.md`, `index.html`, `index.md` и `manifest.json`; debug JSON и извлеченные картинки удаляются после обработки. Для отладки можно добавить `--keep-artifacts`.

Для короткой проверки без API можно запустить:

```bash
python3 archive_extract.py "/path/to/archive.zip" --ocr none --limit 2 --clean
```

## Текущая механика

1. `.docx` открывается как zip.
2. `word/document.xml` парсится через `lxml`.
3. Таблицы, строки, ячейки, абзацы и runs обходятся в порядке Word.
4. Обычный текст берется напрямую из XML без OCR.
5. Inline-изображения находятся через `r:embed` и `word/_rels/document.xml.rels`.
6. Изображения нормализуются через Pillow: прозрачность заменяется белым фоном, маленькие картинки увеличиваются.
7. В `hybrid` режиме OCR применяется только к небольшим inline-картинкам, похожим на формулы или текст.
8. Если включен `--describe-diagrams`, крупные изображения тоже отправляются в модель и вставляются как `[IMAGE:номер: короткое описание]`.
9. Если OCR вернул низкую уверенность или `kind=unknown`, картинка автоматически перепроверяется через `OPENAI_STRONG_MODEL`.
10. Результаты OCR кэшируются в `.ocr_cache` по SHA-256 исходного изображения.

## Промежуточные данные

Основной внутренний формат - список `blocks`:

- `paragraph` - обычный абзац с уже подставленными OCR-фрагментами.
- `table` - таблица Word; каждая строка содержит ячейки, а каждая ячейка содержит свои `paragraph`/`table`.
- `images` в debug JSON - список всех найденных картинок, их размеры, source path, hash, OCR-результат и модель.

Из этого одного внутреннего формата строятся три результата:

- `md` - Markdown с таблицами.
- `txt` - чистый текст без Markdown-разметки.
- `json` - финальный `text`, финальный `markdown`, исходные `blocks`, `images`, `warnings`.

## Fallback по страницам

В коде есть заготовка `--page-fallback`, но на текущей машине нет `libreoffice/soffice`, поэтому рендер страниц пока недоступен. Когда LibreOffice будет установлен, fallback сможет конвертировать DOCX в PDF. Следующий шаг после этого - добавить PDF-to-PNG и OCR целой страницы для подозрительных документов.
