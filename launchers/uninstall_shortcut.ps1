# Remove o atalho "P2S Studio" (e o antigo "Plan2Space Agent Bar", se existir) da Area de Trabalho do usuario atual. ASCII (PowerShell 5.1), sem contornar a ExecutionPolicy.
# Uso:   powershell -NoProfile -File launchers\uninstall_shortcut.ps1
# So apaga esses dois atalhos (nomes exatos). Nao fecha a barra aberta, nao toca no .venv nem no log.
$desktop = [Environment]::GetFolderPath("Desktop")
$removed = 0
foreach ($name in @("P2S Studio.lnk", "Plan2Space Agent Bar.lnk")) {
    $lnkPath = Join-Path $desktop $name
    if (Test-Path $lnkPath) {
        Remove-Item -LiteralPath $lnkPath
        Write-Host "Atalho removido: $lnkPath"
        $removed++
    }
}
if ($removed -eq 0) { Write-Host "Nada a remover: nenhum atalho do P2S Studio na Area de Trabalho" }
exit 0
