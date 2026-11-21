# Cria (ou recria) o atalho "Plan2Space Agent Bar" na Area de Trabalho do usuario atual. ASCII (PowerShell 5.1), sem contornar a ExecutionPolicy.
# Uso, a partir do checkout que o atalho deve abrir:   powershell -NoProfile -File launchers\install_shortcut.ps1
# O atalho roda o pythonw.exe do .venv DESTE checkout (sem console), com a pasta de trabalho no checkout e o icone launchers\agent_bar.ico.
# Fontes de dados da maquina: launchers\local.env (fora do git). Remover: launchers\uninstall_shortcut.ps1
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyw = Join-Path $repo ".venv\Scripts\pythonw.exe"
$icon = Join-Path $repo "launchers\agent_bar.ico"
if (-not (Test-Path $pyw)) {
    Write-Host "venv nao encontrado: $repo\.venv  (o atalho NAO foi criado)"
    Write-Host "Crie uma vez, a partir de ${repo}:"
    Write-Host "   py -3.12 -m venv .venv"
    Write-Host "   .venv\Scripts\python -m pip install -e .[gui]"
    exit 2
}
if (-not (Test-Path $icon)) { Write-Host "icone nao encontrado: $icon  (rode: .venv\Scripts\python launchers\make_icon.py)"; exit 2 }
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Plan2Space Agent Bar.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $pyw
$lnk.Arguments = "-m agent_bar.app"
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$icon,0"
$lnk.Description = "Plan2Space Agent Bar (somente observacao). Checkout: $repo"
$lnk.WindowStyle = 1
$lnk.Save()
Write-Host "Atalho criado: $lnkPath"
Write-Host "  abre: $pyw -m agent_bar.app"
Write-Host "  pasta de trabalho: $repo"
exit 0
