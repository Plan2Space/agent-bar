# Fontes de dados

Tudo o que o Agent Bar mostra vem de uma destas fontes. Para cada uma: **quem lê**, **quando**, **o que acontece quando falha**. A regra geral (ADR [0008](../adr/0008-ausencia-de-dado-nunca-e-sucesso.md)): falha ou ausência de dado vira **cinza / "indisponível"** (ou erro explícito), **nunca** verde e nunca um número inventado.

## Resumo

| # | Fonte | Função | Frequência | Quando falha |
| - | --- | --- | --- | --- |
| 1 | Processos do Windows | `list_processes` | a cada coleta (3 s) | `None` → Claude/Codex/Listener "Indisponível" (cinza) |
| 2 | Tarefa Agendada | `task_state` | a cada coleta | `None` → "não observável" (não deduz que está ligada) |
| 3 | `event_bridge.json` | `read_bridge` | a cada coleta | `None` → estado "ilegível"/"indisponível" |
| 4 | `event_bridge.log` | `read_log_tail` | a cada coleta | listas vazias; linhas ruins contadas |
| 5 | `event_executor.instance.lock` | `lock_holder` | a cada coleta | `None` → "sem lock"/"indisponível" |
| 6 | `EXECUTOR_DISABLED` | `collect_listener` | a cada coleta | `None` se a pasta não existe |
| 7 | `executor/*.log` | `_last_run`, `_latest_log` | a cada coleta | `None` |
| 8 | `codex/last_review.json` | `read_codex_record` | a cada coleta | `None` → "não integrado" |
| 9 | EventStore (SQLite) | `collect_observation` | a cada coleta | `Observation` "indisponível" com o motivo |
| 10 | `gh api user` e `gh pr view` | `collect_github` | TTL = `github_interval_s` (60 s) | GitHub vermelho "Sem conexão/auth" |
| 11 | Backlog (Project, GraphQL) | `collect_backlog` | TTL = `backlog_interval_s` (300 s) | colunas `None` + erro; mantém a última leitura boa, marcada como desatualizada |
| 12 | Fila de PRs (GraphQL) | `collect_pr_queue` | TTL = `github_interval_s` (60 s) | erro explícito; mantém a última fila boa, marcada como desatualizada |
| 13 | Login do Claude | `claude_login_state` | cache 300 s (30 s se desconhecido) | `None` → estados conservadores |
| 14 | Login do Codex | `codex_login_state` | cache 300 s (30 s se desconhecido) | `None` → estados conservadores |
| 15 | Laço de revisão vivo | `review_loop` | a cada coleta (usa a tabela de processos) | `None` |

## Detalhe de cada fonte

### 1. Processos do Windows
- **Como:** PowerShell não interativo executa `Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,<criação em ISO UTC> | ConvertTo-Json -Compress`. Sem janela de console (`CREATE_NO_WINDOW`), timeout de 15 s.
- **Uso:** achar executores do listener (`event_executor run`), descendentes (filhos do executor), sessões do Claude, processos do Codex, o laço de revisão e o passo do botão. Tudo por **PID/PPID** e linha de comando.
- **Falha:** fora do Windows ou em erro/timeout devolve `None` (**não** uma lista vazia). Isso é importante: "lista vazia" significaria "ninguém está rodando"; `None` significa "não sei".
- **Redação:** a linha de comando mostrada na UI passa por `short_cmd`, que mascara tokens conhecidos (`ghp_`, `github_pat_`, `sk-`, `Bearer ...`) e limita a 120 caracteres.

### 2. Tarefa Agendada
- **Como:** `(Get-ScheduledTask -TaskName 'sketchup-mcp event bridge' -ErrorAction Stop).State`.
- **Uso:** distinguir "Desligado" (tarefa parada) de "Tarefa ativa sem processo" (tarefa `Running` mas executor morto).
- **Falha:** tarefa inexistente ou erro → `None` ("não observável"). O nome da tarefa é fixo em `TASK_NAME` (ver [limitações](../limitacoes-e-roadmap.md)).

### 3. `event_bridge.json`
Estado do listener na pasta de governança. Campos usados: `last_poll` (heartbeat), `mode` (`dry-run` ou `execute`), `queue` (itens com `state`, `pr`, `event_id`, `head_sha`, `attempts`), `runs` (instantes ISO das execuções), `diagnostics` (lista de `{message}`).
- Ausente, corrompido ou que não seja um objeto JSON → `None`. O chamador marca "ambíguo/ilegível", **nunca verde**.

### 4. `event_bridge.log`
Um objeto JSON por ciclo (`at`, `action`, `item`, `reason`, `execute`, `fatal`).
- Lê o arquivo inteiro em bytes, remove `NUL`, pega as últimas *n* linhas (40 para o estado do listener; 200 para a lista de eventos).
- Linhas que não são JSON objeto são **contadas** (`bad_lines`) e mostradas no detalhe; a presença de `NUL` também é reportada.

### 5. Trava de instância do executor
`event_executor.instance.lock`: o PID fica gravado a partir do **byte 16** (o byte 0 fica travado pelo SO no Windows). A barra lê o texto e o correlaciona com a tabela de processos (`lock_status`): "vivo e da cadeia do executor", "vivo mas FORA da cadeia (reuso de PID?)" ou "NÃO existe (lock velho; o SO libera o lock real)".

### 6. Kill switch
Existência do arquivo `EXECUTOR_DISABLED` na pasta de governança. Se a pasta não existe, o kill switch é `None` (não observável), não "inativo".

### 7. Logs do executor
- `_latest_log`: o `executor/*.log` mais recente por data de modificação (senão o `event_bridge.log`); é o arquivo aberto pelo botão **Logs**.
- `_last_run`: lê o mais recente e marca **falha** se contiver `"is_error": true` ou (`entrega` e (`blocked_guards` ou `failed_checks`)).

### 8. Registro do Codex
`codex/last_review.json` com `{status: running|done|failed, pr, sha, verdict, at, error}`. É o contrato que o revisor grava. Ausente ou com `status` desconhecido → trata como "sem registro".

### 9. EventStore (SQLite)
Lido em `mode=ro`, nunca criado nem inicializado. Ver [Observabilidade do EventStore](../reference/observabilidade-eventstore.md).

### 10. GitHub (status do PR atual)
- `gh api user --jq .login`: se falhar, GitHub vermelho "Sem conexão/auth".
- `gh pr view <N> --repo <repo> --json url,state,headRefOid,reviewDecision,statusCheckRollup` para o **PR atual** (o mais recente em andamento na fila do `event_bridge.json`). Sem fila → "sem PR em andamento".
- **Importante:** o `gh` roda com `GH_TOKEN` e `GITHUB_TOKEN` **removidos do ambiente**, então usa o login guardado pelo próprio `gh` (keyring). A barra nunca recebe nem guarda token.

### 11. Backlog do Project
Consulta GraphQL enxuta (somente o campo *Status*, 100 itens por página, até 10 páginas) e contagem por coluna: Ready, In Progress, GPT Review, Blocked, Done. Se o Project tiver mais de 1000 itens, a contagem **incompleta é recusada** (erro). Erro de escopo sugere `gh auth refresh -s read:project`.

### 12. Fila de PRs
Uma consulta GraphQL (50 PRs por página, até 4 páginas = 200). Detalhes em [Fila de PRs](../reference/fila-de-prs.md).

### 13 e 14. Login do Claude e do Codex
- Claude: `claude auth status` (saída JSON; usa `loggedIn`). Codex: `codex login status` (texto; "Logged in" = logado, "Not logged in" = deslogado).
- Roda com ambiente limpo de `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT` e `GH_TOKEN`, timeout de 25 s, sem janela.
- **Cache:** 300 s quando o resultado é conhecido (True/False); apenas 30 s quando é desconhecido (`None`), para se recuperar logo após uma falha passageira.
- **Resultado:** `True`, `False` ou `None`. `None` (CLI ausente, erro, saída ilegível) **nunca** vira "logado".

### 15. Laço de revisão
Procura um processo `python`/`pythonw` cuja linha de comando case `tools.orchestration.pr_lifecycle run`. O launcher do venv e o filho python casam os dois; vale o **de cima** (o que não tem pai também casando). É usado para marcar a automação como ativa e para vincular o Codex filho dele.

## O que a barra escreve

| O quê | Onde | Quando |
| --- | --- | --- |
| Preferências | `settings.json` (`%APPDATA%\agent_bar\`) | ao arrastar a barra, mudar notificações ou minimizar |
| Diagnóstico | área de transferência | botão "Copiar diagnóstico" (lê de volta para provar que copiou) |
| Nada nos projetos | — | a barra não grava na pasta de governança, nos bancos, nos logs nem nos repositórios |

Efeitos externos (todos por ação explícita e confirmada pelo dono): `Stop-ScheduledTask`/`Start-ScheduledTask` (parar/reiniciar o listener em DRY_RUN) e o processo do passo do Claude. Ver [Segurança](../seguranca.md).
