# Inicia o Agent Bar. ASCII (PowerShell 5.1). Uso:  powershell -File launchers\run_agent_bar.ps1
# Variaveis opcionais: veja o README (AGENT_BAR_GOV_DIR, EVENT_STORE_DB_PATH, AGENT_BAR_MISSION, ...).
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Set-Location $repo
& $py -m agent_bar.app
exit $LASTEXITCODE
