param(
    [string]$ShortcutName = "Avelin Core Agent.lnk"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Launcher = Join-Path $PSScriptRoot "launch_local.py"
$Vite = Join-Path $Root "frontend\node_modules\vite\bin\vite.js"
$NodeCommand = Get-Command node -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $Python)) { throw "Python venv не найден: $Python" }
if (-not (Test-Path -LiteralPath $Pythonw)) { throw "pythonw.exe не найден: $Pythonw" }
if (-not $NodeCommand) { throw "Node.js не найден в PATH." }
if (-not (Test-Path -LiteralPath $Vite)) { throw "Frontend-зависимости не установлены: $Vite" }

$Local = Join-Path $Root ".local"
New-Item -ItemType Directory -Path $Local -Force | Out-Null
@{
    python = $Python
    node = $NodeCommand.Source
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Local "launcher.json") -Encoding UTF8

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop $ShortcutName
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Pythonw
$Shortcut.Arguments = '"' + $Launcher + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Запустить Avelin Core Agent"
$Shortcut.IconLocation = "$Python,0"
$Shortcut.Save()

Write-Output $ShortcutPath
