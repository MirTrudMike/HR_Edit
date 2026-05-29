# Archive Workflow

## Новый входной сценарий

Файлы приходят не по одному, а ZIP-архивом из папки загрузок. Рабочая гипотеза:

- архив появляется в `Downloads`;
- имя архива начинается с `FUND` или похожего стабильного префикса;
- внутри архива лежат DOCX-файлы, иногда PDF и служебные материалы;
- обрабатывать нужно все `.docx`;
- результат нужен в Markdown/TXT/JSON с возможностью просмотра и копирования.

## Текущий пакетный слой

Скрипт:

```bash
python3 archive_extract.py "/path/to/archive.zip" --format md --ocr hybrid --describe-diagrams --clean
```

Что делает:

1. Открывает ZIP.
2. Находит все `.docx`.
3. Безопасно извлекает каждый DOCX во временную папку.
4. Для каждого DOCX вызывает `docx_extract.py`-механику.
5. Сохраняет результаты с сохранением структуры папок архива.
6. Создает:
   - `index.html`;
   - `index.md`;
   - `manifest.json`.

Выход:

```text
work/archive_runs/index.html
work/archive_runs/<archive-name>/
  index.html
  index.md
  manifest.json
  folder/file.docx
  folder/file.original.pdf
  folder/file.original.fallback.html
  folder/file.original_pages/
    page-1.png
    page-2.png
  1. .../
    ...docx result.md
  2. .../
    ...docx result.md
```

`work/archive_runs/index.html` - общая домашняя страница всех разобранных архивов. Оттуда можно перейти в любой сохраненный архив и продолжить просмотр.

Текущий `index.html` уже является рабочим self-contained интерфейсом:

- дерево папок слева повторяет структуру исходного архива;
- справа открывается preview выбранного Markdown;
- есть переключатель Original для боковой панели с изображениями страниц исходного DOCX;
- оригинал рендерится локально через LibreOffice headless в PDF, затем PDF раскладывается в PNG-страницы; при ошибке показывается fallback с причиной;
- есть поиск по документам;
- есть Copy text и Copy Markdown;
- рядом с каждым результатом сохраняется исходный `.docx`;
- содержимое Markdown встроено в HTML, поэтому страницу можно открыть отдельно;
- debug JSON и извлеченные картинки по умолчанию не сохраняются в result-папке, чтобы не плодить служебные файлы.

## Будущий Windows-режим

Минимальный устойчивый вариант:

```text
watch Downloads
  -> detect new ZIP by prefix
  -> wait until file stops changing
  -> process archive
  -> show local UI with results
```

### Watcher

Python-библиотека:

```text
watchdog
```

Логика:

- следить за `%USERPROFILE%\Downloads`;
- искать `.zip`, имя начинается с заданного префикса;
- проверять, что размер файла не меняется несколько секунд;
- не обрабатывать один и тот же архив повторно;
- писать историю в локальный `processed.json`.

### Интерфейс

Самый прагматичный первый UI:

```text
FastAPI local server + browser UI
```

Почему:

- удобно открыть на Windows без сборки desktop-приложения;
- можно показывать список архивов и документов;
- можно сделать кнопку Copy для Markdown/TXT;
- API-ключ OpenAI хранится локально в `.env`;
- позже этот же local server можно дергать из Chrome extension.

Вариант UI:

```text
http://127.0.0.1:8765

Левая колонка:
  Архивы

Средняя колонка:
  DOCX-файлы внутри выбранного архива

Правая часть:
  Preview результата
  Tabs: Markdown / Text / Debug
  Buttons: Copy, Open file, Re-run
```

## Контракт данных для UI

`manifest.json` уже подходит как основа:

```json
{
  "archive": "...",
  "out_dir": "...",
  "format": "md",
  "ocr": "hybrid",
  "describe_diagrams": true,
  "count": 18,
  "results": [
    {
      "source": "folder/file.docx",
      "output": "folder/file.md",
      "source_docx": "folder/file.docx",
      "original_pdf": "folder/file.original.pdf",
      "original_pages": ["folder/file.original_pages/page-1.png"],
      "original_fallback": "folder/file.original.fallback.html",
      "debug": "folder/file.ocr.json",
      "images_dir": "folder/file_images"
    }
  ]
}
```

Следующий логичный шаг:

1. Протестировать `archive_extract.py` на полном архиве.
2. Посмотреть качество Markdown по всем 18 DOCX.
3. Добавить watcher для Downloads.
4. Добавить локальный web UI поверх `manifest.json`.
