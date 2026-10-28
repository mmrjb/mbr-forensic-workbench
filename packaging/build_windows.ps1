<#
.SYNOPSIS
    Build the MBR Forensic Workbench one-folder Windows package.

.DESCRIPTION
    Run from the project root on Windows 10/11 x64 with Python 3.12 (python.org or py launcher):

        powershell -NoProfile -File packaging\build_windows.ps1

    Steps: create build\venv, install the pinned requirements, run the test suite, run
    PyInstaller (one-folder), write SHA256SUMS.txt and a portable zip under dist\.
    The script does not change the execution policy, does not download anything except the
    pinned Python packages from your configured package index, and never bundles EZ Tools or KAPE.
#>
[CmdletBinding()]
param(
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "-3.12",
    [switch]$SkipTests,
    [switch]$Portable
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root "build\venv"
if (-not (Test-Path $venv)) {
    & $PythonLauncher $PythonVersion -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment with $PythonLauncher $PythonVersion" }
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements\build-windows.txt -r requirements\dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; fix them before packaging (or pass -SkipTests deliberately)." }
    Remove-Item Env:\QT_QPA_PLATFORM
}

& $py -m PyInstaller --noconfirm --clean packaging\mbr_workbench.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\MBR Forensic Workbench"
if ($Portable) {
    New-Item -ItemType File -Force -Path (Join-Path $app "portable.flag") | Out-Null
}
$sums = Join-Path $root "dist\SHA256SUMS.txt"
Get-ChildItem -Recurse -File $app | ForEach-Object {
    $h = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
    "{0}  {1}" -f $h.Hash.ToLower(), $_.FullName.Substring($app.Length + 1)
} | Set-Content -Encoding UTF8 $sums

$version = & $py -c "import sys; sys.path.insert(0, 'src'); import mbrwb; print(mbrwb.__version__)"
$zip = Join-Path $root ("dist\MBR-Forensic-Workbench-{0}-win-x64.zip" -f $version.Trim())
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $app -DestinationPath $zip
$zh = Get-FileHash -Algorithm SHA256 -LiteralPath $zip
Write-Host ("Built {0}`nSHA-256 {1}" -f $zip, $zh.Hash.ToLower())
Write-Host "Next: copy the folder to a clean Windows profile and run 'MBR Forensic Workbench.exe' (see TEST_RESULTS.md, Windows package checklist)."
