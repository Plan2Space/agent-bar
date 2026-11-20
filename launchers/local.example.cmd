@echo off
rem Copie para launchers\local.cmd (ignorado pelo git) e ajuste os caminhos desta maquina. Tudo opcional.
rem Sem nada configurado a barra mostra INDISPONIVEL / SEM FONTE: ausencia de dado nunca vira sucesso.

rem EventStore do agent-runtime (schema 1), lido em mode=ro. Nunca e criado nem escrito pela barra.
rem set "EVENT_STORE_DB_PATH=C:\caminho\para\store.db"
rem set "AGENT_BAR_GOV_DIR=C:\caminho\para\gov"

rem Pasta runs\ do motor (runs\<run_id>\manifest.json, S00-02 C3/C10).
rem set "AGENT_BAR_RUNS_DIR=C:\caminho\para\sketchup-mcp-next\runs"

rem Alvos do GitHub (padrao: Plan2Space Project #2).
rem set "AGENT_BAR_ORG=Plan2Space"
rem set "AGENT_BAR_PROJECT=2"
