#Requires -Version 5.1
<#
  HR_Edit — Windows Installer
  ===========================
  Устанавливает Python, Git, зависимости, клонирует репозиторий,
  создаёт .env с API-ключом и ярлык на рабочем столе.

  Запуск (один раз, из PowerShell):

    powershell -ExecutionPolicy Bypass -File install.ps1

  Или скачать и запустить одной командой (из PowerShell или cmd):

    powershell -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm 'https://raw.githubusercontent.com/MirTrudMike/HR_Edit/main/windows/install.ps1')))"
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
$REPO_URL    = "https://github.com/MirTrudMike/HR_Edit.git"
$INSTALL_DIR = Join-Path $env:USERPROFILE "HR_Edit"
$DESKTOP     = [Environment]::GetFolderPath("Desktop")

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "  --> $Text" -ForegroundColor Cyan
}

function Write-OK([string]$Text) {
    Write-Host "  [OK] $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "  [!!] $Text" -ForegroundColor Yellow
}

function Fail([string]$Text) {
    Write-Host ""
    Write-Host "  [ОШИБКА] $Text" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Установка прервана. Исправь ошибку выше и запусти install.ps1 снова." -ForegroundColor Red
    Write-Host ""
    Read-Host "  Нажми Enter для выхода"
    exit 1
}

function Refresh-EnvPath {
    $machine = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH = "$machine;$user"
}

function Invoke-External([string]$Desc, [scriptblock]$Block) {
    Write-Host "    $Desc..." -ForegroundColor DarkGray
    & $Block 2>&1 | ForEach-Object {
        $line = $_.ToString()
        if ($line -match "(?i)error|failed|fatal") {
            Write-Host "    $line" -ForegroundColor Red
        } else {
            Write-Host "    $line" -ForegroundColor DarkGray
        }
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        Fail "$Desc завершился с кодом $LASTEXITCODE"
    }
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================" -ForegroundColor White
Write-Host "  |         HR_Edit - Установщик               |" -ForegroundColor White
Write-Host "  ================================================" -ForegroundColor White
Write-Host ""
Write-Host "  Папка установки : $INSTALL_DIR" -ForegroundColor DarkGray
Write-Host "  Рабочий стол    : $DESKTOP"     -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. winget
# ---------------------------------------------------------------------------
Write-Step "Проверяю winget..."

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail ("winget не найден.`n" +
          "  Убедись, что Windows 10 (версия 1809+) или Windows 11 обновлена,`n" +
          "  или установи 'App Installer' из Microsoft Store: https://aka.ms/getwinget")
}
$wingetVer = (winget --version).Trim()
Write-OK "winget $wingetVer"

# ---------------------------------------------------------------------------
# 2. Python 3.9+
# ---------------------------------------------------------------------------
Write-Step "Проверяю Python..."

function Get-PythonExe {
    # py launcher — самый надёжный способ на Windows
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $v = (& py --version 2>&1).ToString()
        if ($v -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 9) { return "py" }
    }
    foreach ($cmd in @("python", "python3")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $v = (& $cmd --version 2>&1).ToString()
            if ($v -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 9) { return $cmd }
        }
    }
    # Известные пути установки
    $knownPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python39\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($p in $knownPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$pythonExe = Get-PythonExe

if (-not $pythonExe) {
    Write-Warn "Python 3.9+ не найден. Устанавливаю Python 3.12..."
    Invoke-External "winget install Python 3.12" {
        winget install --id Python.Python.3.12 --source winget `
            --accept-package-agreements --accept-source-agreements --silent
    }
    Refresh-EnvPath
    $pythonExe = Get-PythonExe
    if (-not $pythonExe) {
        Fail ("Python установлен, но не появился в PATH.`n" +
              "  Закрой этот терминал, открой новый PowerShell и запусти install.ps1 снова.")
    }
}

$pyVer = (& $pythonExe --version 2>&1).ToString().Trim()
Write-OK "Python: $pyVer  ($pythonExe)"

# ---------------------------------------------------------------------------
# 3. Git
# ---------------------------------------------------------------------------
Write-Step "Проверяю Git..."

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "Git не найден. Устанавливаю..."
    Invoke-External "winget install Git" {
        winget install --id Git.Git --source winget `
            --accept-package-agreements --accept-source-agreements --silent
    }
    Refresh-EnvPath
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail ("Git установлен, но не появился в PATH.`n" +
              "  Закрой этот терминал, открой новый PowerShell и запусти install.ps1 снова.")
    }
}

$gitVer = (git --version).Trim()
Write-OK "Git: $gitVer"

# ---------------------------------------------------------------------------
# 4. Clone / update repository
# ---------------------------------------------------------------------------
Write-Step "Получаю файлы HR_Edit..."

$gitDir = Join-Path $INSTALL_DIR ".git"

if (Test-Path $gitDir) {
    Write-Warn "Папка уже существует — обновляю (git pull)..."
    Push-Location $INSTALL_DIR
    Invoke-External "git pull" { git pull }
    Pop-Location
    Write-OK "Репозиторий обновлён"
} elseif (Test-Path $INSTALL_DIR) {
    Fail ("Папка '$INSTALL_DIR' уже существует, но не является git-репозиторием.`n" +
          "  Переименуй или удали её вручную и запусти install.ps1 снова.")
} else {
    Invoke-External "git clone" { git clone $REPO_URL $INSTALL_DIR }
    Write-OK "Репозиторий клонирован: $INSTALL_DIR"
}

# ---------------------------------------------------------------------------
# 5. Virtual environment
# ---------------------------------------------------------------------------
$venvDir     = Join-Path $INSTALL_DIR ".venv"
$venvPython  = Join-Path $venvDir "Scripts\python.exe"
$venvPythonW = Join-Path $venvDir "Scripts\pythonw.exe"
$venvPip     = Join-Path $venvDir "Scripts\pip.exe"

Write-Step "Создаю виртуальное окружение..."

if (-not (Test-Path $venvPython)) {
    Invoke-External "python -m venv" { & $pythonExe -m venv $venvDir }
}

if (-not (Test-Path $venvPython)) {
    Fail "Виртуальное окружение создано, но python.exe не найден в $venvDir\Scripts\"
}
Write-OK "Виртуальное окружение: $venvDir"

# ---------------------------------------------------------------------------
# 6. Python dependencies
# ---------------------------------------------------------------------------
Write-Step "Устанавливаю зависимости (несколько минут)..."

Write-Host "    Обновляю pip..." -ForegroundColor DarkGray
& $venvPip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail "Не удалось обновить pip." }

$reqFile = Join-Path $INSTALL_DIR "requirements.txt"
Write-Host "    Устанавливаю requirements.txt..." -ForegroundColor DarkGray
& $venvPip install -r $reqFile 2>&1 | ForEach-Object {
    $line = $_.ToString()
    if ($line -match "(?i)error|failed") {
        Write-Host "    $line" -ForegroundColor Red
    } elseif ($line -match "(?i)warning") {
        Write-Host "    $line" -ForegroundColor Yellow
    }
    # Остальное — тихо
}
if ($LASTEXITCODE -ne 0) { Fail "pip install requirements.txt завершился с ошибкой." }

Write-Host "    Устанавливаю pystray и Pillow (трей)..." -ForegroundColor DarkGray
& $venvPip install pystray Pillow --quiet
if ($LASTEXITCODE -ne 0) { Fail "Не удалось установить pystray / Pillow." }

Write-OK "Все Python-зависимости установлены"

# ---------------------------------------------------------------------------
# 7. LibreOffice (optional)
# ---------------------------------------------------------------------------
Write-Step "Проверяю LibreOffice..."

$sofficePaths = @(
    "C:\Program Files\LibreOffice\program\soffice.exe",
    "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
)
$sofficeExe = $sofficePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($sofficeExe) {
    Write-OK "LibreOffice найден: $sofficeExe"
} else {
    Write-Warn "LibreOffice не найден."
    Write-Host "    Нужен для просмотра оригинальных страниц DOCX." -ForegroundColor DarkGray
    Write-Host "    Без него всё остальное работает нормально." -ForegroundColor DarkGray
    $ans = Read-Host "    Установить LibreOffice? [y/n]"
    if ($ans -eq "y") {
        Write-Host "    Загружаю LibreOffice (может занять несколько минут)..." -ForegroundColor DarkGray
        # LibreOffice installer не поддерживает --silent через winget, будет показан UI
        winget install --id TheDocumentFoundation.LibreOffice --source winget `
            --accept-package-agreements --accept-source-agreements 2>&1 |
            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -eq 0) {
            Write-OK "LibreOffice установлен"
        } else {
            Write-Warn "winget не смог установить LibreOffice."
            Write-Warn "Установи вручную позже: https://www.libreoffice.org/download/"
        }
    } else {
        Write-Warn "LibreOffice пропущен. Можно установить позже."
    }
}

# ---------------------------------------------------------------------------
# 8. OpenAI API key → .env
# ---------------------------------------------------------------------------
Write-Step "Настройка OpenAI API ключа..."

$envFile     = Join-Path $INSTALL_DIR ".env"
$existingKey = $null

if (Test-Path $envFile) {
    foreach ($line in (Get-Content $envFile)) {
        if ($line -match "^OPENAI_API_KEY=(.+)$") {
            $existingKey = $Matches[1].Trim()
            break
        }
    }
}

$apiKey = $null

if ($existingKey) {
    $preview = $existingKey.Substring(0, [Math]::Min(10, $existingKey.Length)) + "..."
    Write-Warn "Ключ уже есть в .env: $preview"
    $ans = Read-Host "    Заменить? [y/n]"
    if ($ans -ne "y") {
        Write-OK "API ключ оставлен без изменений"
        $apiKey = $existingKey
    }
}

if (-not $apiKey) {
    Write-Host ""
    do {
        $apiKey = (Read-Host "    Введи OpenAI API ключ (начинается с sk-)").Trim()
        if (-not $apiKey.StartsWith("sk-")) {
            Write-Warn "Ключ должен начинаться с 'sk-'. Попробуй ещё раз."
            $apiKey = $null
        }
    } while (-not $apiKey)
}

$envContent = "OPENAI_API_KEY=$apiKey`nOPENAI_MODEL=gpt-4.1-mini`nOPENAI_STRONG_MODEL=gpt-4o`n"
[System.IO.File]::WriteAllText($envFile, $envContent, [System.Text.Encoding]::UTF8)
Write-OK ".env создан: $envFile"

# ---------------------------------------------------------------------------
# 9. Desktop shortcut
# ---------------------------------------------------------------------------
Write-Step "Создаю ярлык на рабочем столе..."

$launcherPath = Join-Path $INSTALL_DIR "windows\launcher.py"
$shortcutPath = Join-Path $DESKTOP "HR_Edit.lnk"
$icoPath      = Join-Path $INSTALL_DIR "windows\hr_edit.ico"

if (-not (Test-Path $launcherPath)) {
    Fail "launcher.py не найден: $launcherPath`n  Возможно, репозиторий скачался не полностью."
}

$shortcutTarget = if (Test-Path $venvPythonW) { $venvPythonW } else { $venvPython }

try {
    $shell    = New-Object -ComObject WScript.Shell
    $lnk      = $shell.CreateShortcut($shortcutPath)
    $lnk.TargetPath       = $shortcutTarget
    $lnk.Arguments        = "`"$launcherPath`""
    $lnk.WorkingDirectory = $INSTALL_DIR
    $lnk.WindowStyle      = 1
    $lnk.Description      = "HR_Edit — запуск сервера"
    if (Test-Path $icoPath) { $lnk.IconLocation = $icoPath }
    $lnk.Save()
    Write-OK "Ярлык создан: $shortcutPath"
} catch {
    Write-Warn "Не удалось создать ярлык: $_"
    Write-Warn "Создай вручную:"
    Write-Warn "  Цель    : $venvPythonW"
    Write-Warn "  Аргумент: `"$launcherPath`""
    Write-Warn "  Папка   : $INSTALL_DIR"
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Green
Write-Host "  HR_Edit успешно установлен!" -ForegroundColor Green
Write-Host ""
Write-Host "  Ярлык HR_Edit — на рабочем столе." -ForegroundColor Green
Write-Host "  Двойной клик — сервер запустится в трее." -ForegroundColor Green
Write-Host "  Браузер откроется автоматически." -ForegroundColor Green
Write-Host ""
Write-Host "  Файлы: $INSTALL_DIR" -ForegroundColor DarkGray
Write-Host "  ================================================" -ForegroundColor Green
Write-Host ""
Read-Host "  Нажми Enter для выхода"
