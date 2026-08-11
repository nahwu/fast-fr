$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "[1/5] Installing deterministic UI dependencies"
npm.cmd ci --no-audit --no-fund

Write-Host "[2/5] Building the operator UI"
npm.cmd run build

Write-Host "[3/5] Downloading and verifying recognition models"
python scripts/download_models.py

Write-Host "[4/5] Building the portable Windows application"
$buildVenv = Join-Path $repoRoot "build\build-venv"
python -m venv --clear $buildVenv
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
& $buildPython -m pip install ".[build]"
& $buildPython -m PyInstaller --noconfirm --clean --distpath build --workpath build/pyinstaller packaging/FastFR.spec

$innoCandidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "[5/5] Building the installer"
    & $iscc "packaging\FastFR.iss"
    Write-Host "Installer: build\installer\FastFR-Setup.exe"
} else {
    Write-Warning "Inno Setup 6 was not found. Portable build is ready in build\FastFR."
    Write-Host "Install Inno Setup 6 and rerun this script to produce FastFR-Setup.exe."
}
