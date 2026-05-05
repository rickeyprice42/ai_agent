param(
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all",

    [switch]$Install,

    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $Root "frontend"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

function Write-Step {
    param([string]$Message)
    Write-Host "[Avelin dev] $Message" -ForegroundColor Cyan
}

function Quote-ForPowerShell {
    param([string]$Value)
    "'" + $Value.Replace("'", "''") + "'"
}

function Start-AvelinProcess {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [string]$Command
    )

    Write-Step "Starting $Name..."
    $safeWorkingDirectory = Quote-ForPowerShell $WorkingDirectory
    $windowTitle = "Avelin $Name"
    $wrappedCommand = @"
`$Host.UI.RawUI.WindowTitle = '$windowTitle'
Set-Location $safeWorkingDirectory
$Command
"@

    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $wrappedCommand
    )
}

if ($Install) {
    Write-Step "Installing Python dependencies..."
    Push-Location $Root
    try {
        & $Python -m pip install -r requirements.txt
    }
    finally {
        Pop-Location
    }

    Write-Step "Installing frontend dependencies..."
    Push-Location $FrontendDir
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

if ($Target -in @("all", "backend")) {
    $safePython = Quote-ForPowerShell $Python
    Start-AvelinProcess `
        -Name "backend" `
        -WorkingDirectory $Root `
        -Command "& $safePython -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000"
}

if ($Target -in @("all", "frontend")) {
    Start-AvelinProcess `
        -Name "frontend" `
        -WorkingDirectory $FrontendDir `
        -Command "npm run dev -- --host 127.0.0.1 --port 5173"
}

Write-Host ""
Write-Step "Backend:  http://127.0.0.1:8000"
Write-Step "Frontend: http://127.0.0.1:5173"
Write-Step "Servers run in separate PowerShell windows."
Write-Step "Press Ctrl+C in a server window to stop that server."
Write-Host ""

if (-not $NoPause) {
    Read-Host "Press Enter to close this launcher window"
}
