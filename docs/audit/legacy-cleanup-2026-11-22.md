# Limpeza do legado do Agent Bar (2026-11-22)

Pedido do Felipe em 2026-11-22: "vai precisar fazer um refactor nele ne? tem muita coisa do legado que ja nao faz mais
sentido nenhum estar nele agora" (`origin = "human:Felipe/2026-11-22"`). Frente `P2S-S01-E-REVIEW-STUDIO`, que é a única
escritora do agent-bar.

**Fontes vivas hoje:** Project #2, EventStore do agent-runtime, execuções do motor (`sketchup-mcp-next`), benchmark do
agent-runtime, PRs da org Plan2Space, sessões Claude locais, `gh`, o evidence (Revisão visual) e o cockpit ACP.

**Fonte morta:** o orquestrador FP-057/FP-060 do legado `sketchup-mcp`: tarefa agendada "sketchup-mcp event bridge"
(**desabilitada em 2026-11-22**), `runs/governance/*`, laço de revisão do Codex e missão "FP-060 passo".

## Inventário

| Peça | Onde | O que lê | Fonte viva? | Decisão | PR |
|---|---|---|---|---|---|
| Listener / event bridge (chip "Listener", saúde, heartbeat, kill switch `EXECUTOR_DISABLED`, lock, instâncias, banner DRY_RUN/EXECUTE, "trabalho") | `collectors.collect_listener`, `task_state`, `read_bridge`, `read_log_tail`, `lock_holder`, `executor_roots`, `lock_status`, `executor_summary`; `window.Details.health/banner` | `runs/governance/event_bridge.json`, `.log`, `event_executor.instance.lock`, tarefa agendada | **não**, tarefa desabilitada | remover | L2 |
| Eventos do bridge (lista "eventos recentes") | `collectors.recent_events`, `group_events`; `window.Details.events` | `event_bridge.log` | não | remover (a timeline viva é a do EventStore, na Central completa) | L2 |
| "PR atual" (um PR do `GFCDOTA/sketchup-mcp`) e o status do chip GitHub derivado dele | `collectors.current_pr`, `collect_github`; botão "PR atual" | bridge + `gh pr view` no repositório do legado | não | remover. O chip GitHub passa a vir de `gh auth status` mais a fila de PRs da org | L2 |
| Botões "Logs", "Reiniciar DRY_RUN" e "Parar" | `controls.stop_listener`, `restart_listener_dry_run`, `preflight`, `active_execution`, `task_has_execute`, `latest_log` | tarefa agendada, logs do executor | não | remover. A barra volta a só observar (ADR 0001) | L2 |
| `AGENT_BAR_GOV_DIR` / `default_gov_dir()` (procura `runs/governance` no checkout principal) | `collectors`, `controls`, `window`, `observability` | pasta do legado | não | remover. O EventStore vem **só** de `EVENT_STORE_DB_PATH`, sem fallback para a pasta do legado | L2 |
| `AGENT_BAR_HARNESS_LOG` (cauda do log do coordenador FP-060) | `observability` | `data\runs\fp060_lifecycle` | não | remover | L2 |
| Fila de PRs do `GFCDOTA/sketchup-mcp` | `collectors.collect_pr_queue` | GraphQL do repositório do legado | não, substituída por `collect_org_pr_queue` | remover | L2 |
| Board antigo (`AGENT_BAR_BOARD=legacy`, GFCDOTA Project #1) | `targets.LEGACY` | Project #1 do legado | não | remover. Só Project #2 | L2 |
| Chip "Codex", laço de revisão (`tools.orchestration.pr_lifecycle`), `codex login status`, registro `runs/governance/codex` | `collectors.collect_codex`, `codex_login_state`, `review_loop`, `read_codex_record`, `is_codex`; o "Listener verde pelo laço" | processos e arquivos do legado | não | remover | L1 |
| Chip "GPT" (sempre cinza, "nenhuma fonte contratada") | `models.Snapshot.gpt` | nada | não, é cartão morto | remover | L1 |
| Botão "Dar andamento (Claude)", ▶ na barra, terminal do passo, "Parar passo" e missão `fp060-passo.md` | `controls.progress_command`, `step_running`, `format_stream_line`, `kill_tree`; `window.Bar.action_progress/stop_step`; `AGENT_BAR_MISSION`; `collectors.STEP_RE` | `ops\launchers\fp060-passo.md`, `claude -p` | não | remover. Conversar com agente é o cockpit ACP | L1 |
| Nomes "FP-061" / "Harness Control Center" nos títulos | `window` | — | — | renomear para "Agent Bar" / "Central completa" | L1 |
| `AGENT_BAR_OBSERVE_ONLY` (instância FP-061 somente leitura) | `window` (botões de controle e aprovar) | — | parcial: ainda desliga o botão Aprovar (S00-14) | manter só para Aprovar | — |
| Sessões Claude locais (chip "Claude") | `collectors.collect_claude`, `claude_login_state` | processos locais, `claude auth status` | **sim** | manter, sem a parte "dentro/fora do orquestrador" | L1/L2 |
| Fila de PRs da org, Project #2, EventStore, execuções do motor, benchmark, aprovação S00-14, "Precisa de você", conferência do runtime fixado, cockpit ACP, Revisão visual | `collect_org_pr_queue`, `collect_board`, `observability`/`store_reader`, `runs`, `benchmark`, `approvals`, `inbox`, `runtime_pin`, `cockpit/*` | fontes vivas | **sim** | manter | — |
| `launchers\local.example.env`, `run_agent_bar.ps1`, README, `docs/reference/*` e ADRs 0005/0010/0013 (passo FP-060, laço de revisão, limite do revisor) | docs | — | — | tirar as variáveis e seções do FP-060 e marcar as ADRs como **substituídas** (sem apagar o histórico) | L3 |
| `E:\Claude\ops\launchers\AGENT-BAR.cmd` (fora do repo; sem atalho desde o arquivamento do coordenador) | workspace | descreve "observa Claude, Codex e a automação FP-060" | não, o atalho oficial é `launchers\install_shortcut.ps1` deste repo | **propor** mover para `E:\Claude\archive\` junto com `FP060-PASSO.cmd` e `fp060-passo.md`. Decisão do coordenador; esta frente não toca o workspace | — |

## Regras da limpeza

- Um PR por área, removendo código e testes juntos. Teste removido só se testava código removido; nenhum teste de
  código que fica é enfraquecido.
- Ausência de dado continua nunca sendo sucesso (ADR 0008).
- A barra fica com: chip Claude, chip GitHub, fila de PRs da org, contador "Precisa de você" e engrenagem. O painel
  fica com estado, fila de PRs, Project #2, EventStore, execuções do motor, benchmark, aprovação e o botão do cockpit.
