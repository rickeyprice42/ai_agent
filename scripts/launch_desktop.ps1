[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$LocalPreview,
    [ValidateRange(1024, 65535)]
    [int]$PreviewPort = 5173
)

$ErrorActionPreference = "Stop"
$url = if ($LocalPreview) { "http://127.0.0.1:${PreviewPort}/" } else { "https://app.avelin.tech/" }
$installRoots = @(
    ${env:ProgramFiles(x86)}
    $env:ProgramFiles
    $env:LOCALAPPDATA
)
$edge = $installRoots | Where-Object { $_ } | ForEach-Object {
    Join-Path $_ "Microsoft\Edge\Application\msedge.exe"
} | Where-Object {
    Test-Path -LiteralPath $_ -PathType Leaf
} | Select-Object -First 1

if (-not $edge) {
    throw "Microsoft Edge не найден. Для запуска Avelin в отдельном окне установите Microsoft Edge."
}

if ($PSCmdlet.ShouldProcess($url, "Открыть окно Avelin")) {
    Start-Process -FilePath $edge -ArgumentList "--app=$url"
}
