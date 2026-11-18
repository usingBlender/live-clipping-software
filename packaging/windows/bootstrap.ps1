# Run by the installer: sets up a private Python 3.12 + dependencies with uv, and FFmpeg if missing.
# Log: %LOCALAPPDATA%\gta-clipper\install.log
$ErrorActionPreference = "Continue"
$app = $PSScriptRoot
$log = Join-Path $env:LOCALAPPDATA "gta-clipper\install.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Log($m) { "$(Get-Date -Format s) $m" | Out-File -Append -Encoding utf8 $log }

$env:UV_PYTHON_INSTALL_DIR = Join-Path $app "python"
$env:UV_CACHE_DIR = Join-Path $env:LOCALAPPDATA "gta-clipper\uv-cache"
$env:UV_NO_PROGRESS = "1"
$uv = Join-Path $app "bin\uv.exe"
$venv = Join-Path $app ".venv"

Log "creating venv"
& $uv venv --allow-existing -p 3.12 $venv 2>&1 | Out-File -Append -Encoding utf8 $log
Log "installing requirements"
& $uv pip install --python (Join-Path $venv "Scripts\python.exe") -r (Join-Path $app "requirements.txt") 2>&1 | Out-File -Append -Encoding utf8 $log
if ($LASTEXITCODE -ne 0) { Log "pip install failed ($LASTEXITCODE)"; exit 1 }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  Log "installing ffmpeg with winget"
  winget install -e --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-File -Append -Encoding utf8 $log
}
Log "done"
exit 0
