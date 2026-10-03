<#
.SYNOPSIS
  Build dist\MOG-Client.exe (single file, windowed GUI).

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

Set-Content -Path mog_client\_version.py -Value "__version__ = `"$Version`"" -Encoding ascii

if (Test-Path build\exe) { Remove-Item -Recurse -Force build\exe }
New-Item -ItemType Directory -Force build, dist | Out-Null

python -m venv build\venv
$py = "build\venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet ".[gui,build]"

# Absolute paths: PyInstaller resolves relative ones against the spec dir (build\exe).
$root = (Get-Location).Path
$icon = Join-Path $root "packaging\mog-client.png"
$entry = Join-Path $root "packaging\entry.py"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name "MOG-Client" `
  --icon $icon `
  --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\exe\work") `
  --specpath (Join-Path $root "build\exe") `
  --paths $root $entry
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "Built dist\MOG-Client.exe"
