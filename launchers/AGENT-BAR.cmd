@echo off
rem Agent Bar (Plan2Space): abre a barra DESTE checkout com o venv do proprio checkout. Somente observacao.
rem Duplo clique com a barra ja aberta nao abre outra. ASCII puro (cmd). Sem PowerShell -ExecutionPolicy Bypass.
rem Caminhos da maquina (EventStore, pasta runs do motor) ficam em launchers\local.cmd, fora do git (ver local.example.cmd).
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
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*-m agent_bar.app*' }).Count -gt 0) { exit 1 } else { exit 0 }"
if errorlevel 1 exit /b 0
set "PYTHONUTF8=1"
set "GH_TOKEN="
if exist "C:\Program Files\GitHub CLI\gh.exe" set "GH_BIN=C:\Program Files\GitHub CLI\gh.exe"
if exist "%~dp0local.cmd" call "%~dp0local.cmd"
cd /d "%REPO%"
start "" "%PYW%" -m agent_bar.app
exit /b 0
