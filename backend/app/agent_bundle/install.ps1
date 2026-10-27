# Convenience launcher for Windows PowerShell.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
if (-not (Test-Path config.json)) {
  Write-Host "Creating config.json from template - edit it with your pairing details."
  Copy-Item config.example.json config.json
}
python mestinghem_agent.py @args
