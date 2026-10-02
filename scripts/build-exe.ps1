<#
.SYNOPSIS
  Build dist\MOG-Client-<version>.exe (single file, windowed GUI).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
  powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Version 1.2.3

  Needs Python >= 3.10 on PATH. PyInstaller and PySide6 are installed into .\build\venv.
#>
param([string]$Version = "")

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $Version) {
  $Version = python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
}

if (Test-Path build\exe) { Remove-Item -Recurse -Force build\exe }
New-Item -ItemType Directory -Force build, dist | Out-Null

python -m venv build\venv
$py = "build\venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet ".[gui,build]"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name "MOG-Client-$Version" `
  --icon packaging\mog-client.png `
  --distpath dist --workpath build\exe\work --specpath build\exe `
  --paths . packaging\entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "Built dist\MOG-Client-$Version.exe"
