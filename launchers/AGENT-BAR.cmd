@echo off
rem P2S Studio (Plan2Space): abre a barra DESTE checkout com o pythonw.exe do proprio .venv (nunca o python global). Somente observacao.
rem Abrir de novo com a barra aberta so traz a existente para a frente (instancia unica no proprio app).
rem Fontes de dados da maquina: launchers\local.env (fora do git; modelo em local.example.env). Log: %LOCALAPPDATA%\agent_bar\logs
rem ASCII puro (cmd). Sem contornar a ExecutionPolicy do PowerShell. Atalho na Area de Trabalho: launchers\install_shortcut.ps1
setlocal
for %%i in ("%~dp0..") do set "REPO=%%~fi"
set "PYW=%REPO%\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo venv nao encontrado: %REPO%\.venv
  echo Crie uma vez, a partir de %REPO%:
  echo    py -3.12 -m venv .venv
  echo    .venv\Scripts\python -m pip install -e .[gui]
  pause
  exit /b 2
)
set "PYTHONUTF8=1"
set "GH_TOKEN="
cd /d "%REPO%"
start "" "%PYW%" -m agent_bar.app
exit /b 0
