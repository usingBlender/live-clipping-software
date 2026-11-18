# Run from a source checkout (development). Installed copies start from the Start menu instead.
#   .\run.ps1            app in this window
#   .\run.ps1 n8n        optional n8n (for auto-upload workflows)
param([ValidateSet("app", "n8n")][string]$What = "app")
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue   # claude -p must use the Pro subscription
if ($What -eq "n8n") {
  $env:N8N_LISTEN_ADDRESS = "127.0.0.1"; $env:N8N_PORT = "5678"; $env:GENERIC_TIMEZONE = "America/Sao_Paulo"
  & n8n start
} else {
  & "$PSScriptRoot\.venv\Scripts\python.exe" -m web --supervise
}
