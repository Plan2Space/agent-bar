# Inicia o Agent Bar com o Python do .venv DESTE checkout. ASCII (PowerShell 5.1). Uso:  powershell -NoProfile -File launchers\run_agent_bar.ps1
# Nunca cai no python global (quebrado nesta maquina): sem .venv, explica como criar e sai com 2.
# Variaveis opcionais: veja docs\reference\configuracao.md (AGENT_BAR_GOV_DIR, EVENT_STORE_DB_PATH, AGENT_BAR_RUNS_DIR, ...).
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv nao encontrado: $repo\.venv"
    Write-Host "Crie uma vez, a partir de ${repo}:"
    Write-Host "   py -3.12 -m venv .venv"
    Write-Host "   .venv\Scripts\python -m pip install -e .[gui]"
    exit 2
}
$env:GH_TOKEN = $null
$local = Join-Path $PSScriptRoot "local.cmd"
if (Test-Path $local) { Write-Host "Aviso: launchers\local.cmd so e lido pelo AGENT-BAR.cmd e pelo atalho; aqui valem as variaveis desta sessao." }
Set-Location $repo
Start-Process -FilePath $py -ArgumentList @("-m", "agent_bar.app") -WorkingDirectory $repo
exit 0
