# Remove o atalho "Plan2Space Agent Bar" da Area de Trabalho do usuario atual. ASCII (PowerShell 5.1), sem contornar a ExecutionPolicy.
# Uso:   powershell -NoProfile -File launchers\uninstall_shortcut.ps1
# So apaga esse atalho (nome exato). Nao fecha a barra aberta, nao toca no .venv, no log nem no atalho antigo "Agent Bar".
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Plan2Space Agent Bar.lnk"
if (Test-Path $lnkPath) {
    Remove-Item -LiteralPath $lnkPath
    Write-Host "Atalho removido: $lnkPath"
} else {
    Write-Host "Nada a remover: $lnkPath nao existe"
}
exit 0
