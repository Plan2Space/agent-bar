# Linha de comando

## Entrada

```powershell
python -m agent_bar.app [--once-json] [--screenshot ARQ.png] [--open-panel]
```

| Opção | O que faz | Interface? |
| --- | --- | --- |
| *(nenhuma)* | abre a barra e fica residente (não encerra ao fechar a última janela; só **Encerrar barra** sai) | sim |
| `--once-json` | faz **uma** coleta completa (inclusive o login do Claude e do Codex) e imprime o diagnóstico em texto; sai com código 0 | não |
| `--open-panel` | abre o painel 4 s depois de iniciar (evidência e teste) | sim |
| `--screenshot ARQ.png` | 7 s após iniciar, grava um PNG da barra (`ARQ.png`) e do painel (`ARQ_panel.png`) e encerra | sim |

Código de saída `2` se o PySide6 não estiver instalado (mensagem em `stderr`). `--once-json` **não** exige PySide6.

## Diagnóstico em texto (`--once-json`)

A saída é o mesmo texto do botão **Copiar diagnóstico** (`Snapshot.diagnostic()`), sem segredos:

```
Agent bar snapshot 2026-11-19T00:59:22+00:00
Claude: Conectado - logado (claude.ai); 1 sessao(oes) interativa(s) viva(s) fora do orquestrador (pid 23936); ...
Codex: Limite de uso - PR #641 2742ab1: revisor suspenso ate o reset da conta; RATE_LIMITED: ...
GPT: Indisponível - Nenhuma fonte de estado GPT contratada pelo Harness
Listener: Laco de revisao ativo pid 39360 - pr_lifecycle run (pid 39360, ha 21191s); listener FP-057 desligado
GitHub: Conectado - login <usuario>; sem PR em andamento
modo: modo indisponível
trabalho: indisponivel
PR atual: indisponivel
checks: sem PR
heartbeat: indisponivel
kill switch: inativo
instancias do executor: 0
lock: ...
backlog: Ready=3, In Progress=18, GPT Review=16, Blocked=31, Done=29 (sync ...)
Event Store: real (Event Store FP-060, leitura SQLite)
fila: {'DLQ': 2, 'DONE': 279, 'PENDING': 1, ...}
mensagens: Mensagens entre agentes indisponíveis: sem contrato persistido
```

Uso típico: conferir o que a barra **veria** sem abrir a interface, ou anexar a um relato de problema. Combine com `AGENT_BAR_GOV_DIR` e `EVENT_STORE_DB_PATH` para apontar a fonte.

## Launcher do Windows

`launchers\run_agent_bar.ps1` (ASCII, PowerShell 5.1): resolve a raiz do repositório, usa `.venv\Scripts\python.exe` se existir (senão `python`) e executa `python -m agent_bar.app`. Sem privilégios de administrador, sem `-ExecutionPolicy Bypass` (alguns antivírus sinalizam esse parâmetro).

```powershell
powershell -File launchers\run_agent_bar.ps1
```

### Atalho na área de trabalho
O repositório não cria atalhos automaticamente. Para um duplo clique, aponte um atalho para `cmd.exe /c "<caminho>\launcher.cmd"` ou para `powershell.exe -NoProfile -File "<caminho>\launchers\run_agent_bar.ps1"` e use `pythonw.exe` se não quiser janela de console. Variáveis de ambiente (veja [Configuração](configuracao.md)) devem ser definidas no próprio launcher.
