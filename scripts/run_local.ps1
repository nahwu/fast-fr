$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

python scripts/download_models.py
npm.cmd run build
python -m fastfr.launcher
