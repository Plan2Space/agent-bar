# S00-07 — Inventário funcional do Agent Bar

> Sprint 00 · Plan2Space Rebuild · Frente C · issue [#7](https://github.com/Plan2Space/agent-bar/issues/7)
> Auditoria feita em 2026-11-20 rodando o app de verdade. Evidência em [`docs/reports/s00-07/`](../reports/s00-07/).
> Capturas *offscreen* (Qt `offscreen` + fontes do Windows) servem de evidência de layout e de dados.
> **O veredito visual no desktop real é do Felipe.**

## 1. O que foi auditado (SHAs)

| Linha | SHA | Conteúdo |
| --- | --- | --- |
| `agent-bar` `main` | `c58efc82` | só o leitor `src/agent_bar/store_reader.py` + `READER_CONTRACT.md` + CI |
| `agent-bar` `develop` | `366b7a63` | **a UI inteira** (PySide6): barra, painel, Central completa, terminal, controles, 13 ADRs |
| cópia antiga no produto | `sketchup-mcp` worktree `w-fp061-agent-bar-2` (`9f163072` + mudanças não commitadas) | `tools/orchestration/agent_bar/`: **é o que o atalho "Agent Bar" da área de trabalho roda hoje** |
| `agent-runtime` `main` | `bdb4391` | `src/agent_runtime/event_store.py` (schema v1), usado só para o teste de compatibilidade |

`main` e `develop` partilham só o *Initial commit* (`5b2f2e9`) e divergiram desde então. O S00-01 (`engineering-specs` `plan2space/S00-01_legacy_inventory.md`, R4) deixou a decisão de branch para esta auditoria. A decisão está na seção 5.

**Preservação comprovada.** `develop` bate com a cópia que o Felipe usa, incluindo as mudanças não commitadas da worktree. Depois de trocar `tools.orchestration.agent_bar` por `agent_bar`, `diff -w` dá 0 linhas em 7 dos 9 módulos. Em `collectors.py` e `controls.py`, a única diferença é a profundidade da raiz do repo (`parents[3]` → `parents[1]`). Mover o atalho para este repo não perde funcionalidade.

## 2. Como foi rodado

```powershell
# worktree destacada de origin/develop; venv com o CPython 3.12 do uv (o Python312 do sistema está quebrado nesta máquina, ver D8)
.venv\Scripts\python -m pip install "pytest>=8.3,<9.0" ruff "PySide6-Essentials>=6.7,<7.0"   # pip install -e .[dev] FALHA (D1)
$env:AGENT_BAR_SETTINGS = "<scratch>\settings.json"   # a auditoria não toca no settings do Felipe
python -m agent_bar.app --once-json                    # 4 configurações de fonte (abaixo)
$env:QT_QPA_PLATFORM="offscreen"; $env:QT_QPA_FONTDIR="C:\Windows\Fonts"; python -m agent_bar.app --screenshot x.png
```

Fontes de dados usadas, todas **somente leitura**:

| Config | `AGENT_BAR_GOV_DIR` / `EVENT_STORE_DB_PATH` | Evidência |
| --- | --- | --- |
| padrão | nenhuma (cai em `apps/agent-bar/runs/governance`, que não existe) | `once_json_default.txt`, `01_barra_real.png`, `03_central_real_sem_eventstore.png` |
| governança legada | `sketchup-mcp/runs/governance` | `once_json_legacy.txt`, `02_painel_real_gov_legado.png`, `04_central_real_gov_legado.png` |
| a do atalho do Felipe | `data/runs/fp060_lifecycle/gov` (`store.db`, `run3.log`) | `once_json_atalho_fp060_lifecycle.txt`, `06_central_real_config_do_atalho.png` |
| banco ausente | caminho inexistente no scratch | `once_json_missing.txt` (diretório **não** foi criado) |
| compatibilidade com o runtime novo | banco **criado no scratch pelo código do `agent-runtime` `main`**, com eventos rotulados `compat-*` | `05_central_compat_schema_runtime.png`, `compat_observation_develop.txt`, `compat_store_reader_main.json` |

**Nenhuma escrita no EventStore.** O `md5` de `fp060_lifecycle/gov/store.db` (`a380d9fd…`) e o do banco de compatibilidade (`b1913d6d…`) ficaram idênticos antes e depois da leitura. O único arquivo que os coletores gravam é o `settings.json` da própria barra (`settings.py:47-50`).

O login do GitHub foi coberto nas capturas e trocado por `<usuario>` nos textos, porque o repo é público.

## 3. Inventário

Legenda: **OK** = visto funcionando com dado real · **OK\*** = funciona, com débito anotado · **TESTE** = não acionado ao vivo de propósito (age sobre agentes ou sobre a Tarefa Agendada), coberto por teste · **AUSENTE** = não existe, e a UI diz isso.

| # | Funcionalidade | Onde (`develop`) | Evidência | Estado |
| --- | --- | --- | --- | --- |
| 1 | Barra flutuante: 5 indicadores (Claude, Codex, GPT, Listener, GitHub), ▶ e engrenagem | `window.py` `Bar` | `01_barra_real.png`; `test_gui_is_frameless_topmost_with_five_status_icons_and_a_play_button` | OK |
| 2 | Claude: login via CLI + sessões interativas vivas, sem atribuir ao orquestrador | `collectors.py` | `once_json_*`: "logado (claude.ai); 4 sessao(oes)…" | OK |
| 3 | Codex: login, análise manual, limite de uso | `collectors.py` | `once_json_default.txt` / `once_json_atalho…txt` | OK\* (D5, D6) |
| 4 | GPT explicitamente indisponível (sem fonte contratada) | `collectors.py` | todas as capturas: cinza, "Indisponível" | OK |
| 5 | Listener / laço de revisão (Tarefa Agendada `sketchup-mcp event bridge`) | `collectors.py` `TASK_NAME` | "Desligado - tarefa: Ready"; `Get-ScheduledTask` = Ready | OK\* (D3) |
| 6 | GitHub: login + PR atual do listener + checks | `collect_github` | "Conectado - login <usuario>; sem PR em andamento" | OK\* (D3) |
| 7 | Fila de PRs abertos classificada (pronto / corrigir / conflito / CI / revisão / rascunho) | `collect_pr_queue` | "PRs 1 abertos · 1 aguardando revisão" (**de `GFCDOTA/sketchup-mcp`**) | OK\* (D3) |
| 8 | Backlog do Project (contagem por coluna, paginado, recusa contagem incompleta) | `collect_backlog` | "Ready=3, In Progress=15, GPT Review=10…" (**Project #1 da org `GFCDOTA`**) | OK\* (D3) |
| 9 | Saúde: heartbeat, kill switch, instâncias, lock correlacionado ao processo | `collectors.py` | "lock: pid 18380 NAO existe (lock velho…)" | OK |
| 10 | EventStore somente leitura (`mode=ro`, `query_only`), ausente = INDISPONÍVEL | `observability.py` | `03` (INDISPONÍVEL), `04`/`06` (lido), md5 inalterado | OK\* (D4) |
| 11 | Compatível com o schema do **agent-runtime novo** (v1: `events`, `transitions`, `subprocess_runs`, `outbox`, `meta`) | `observability.py` e `main:store_reader.py` | `05_…png`, `compat_observation_develop.txt`, `compat_store_reader_main.json` (RUNNING/DLQ/lease corretos) | OK |
| 12 | Central completa: timeline, pipeline PR/CI, fila, workers/leases, retries/DLQ, decisões humanas | `operations_view.py` | `03`–`06`; "Registro aberto não comprova processo vivo" | OK |
| 13 | Terminal embutido somente leitura (500 linhas, rolagem) | `window.py` `term_append` | `03`–`06` (uma linha **sintética** rotulada, injetada pela auditoria); `test_stream_lines_become_readable_terminal_lines` | OK |
| 14 | "Dar andamento (Claude)" (`claude -p`, modo auto, missão `ops/launchers/fp060-passo.md`) + "Parar passo" por PID | `controls.py` `progress_command`, `window.py` | `test_progress_button_runs_the_step_inside_the_panel_and_streams_the_output`, `test_a_declined_confirmation_starts_nothing`, `test_the_stop_button_ends_only_the_step_it_started`, `test_a_step_already_running_in_another_window_blocks_a_second_one`, `test_a_missing_claude_cli_is_reported_not_faked` | TESTE |
| 15 | Parar / Reiniciar DRY_RUN do listener (preflight único e fail-closed, nunca `taskkill` por nome) | `controls.py` | `test_stop_and_restart_share_one_fail_closed_preflight`, `test_kill_switch_blocks_both_controls…`, `test_restart_does_not_start_a_second_instance…` | TESTE |
| 16 | Botões Repositório / Project #1 / PR atual / Logs / Copiar diagnóstico / Atualizar | `window.py`, `controls.py` | `test_button_*` (8 testes); `test_open_url_only_accepts_github` | OK\* (D3: URLs `GFCDOTA`) |
| 17 | Nenhum módulo faz commit/push/merge nem mata processo por nome de imagem | todo o pacote | `test_no_module_of_the_bar_can_commit_push_merge_or_kill_by_image_name` | OK |
| 18 | Modo observação (`AGENT_BAR_OBSERVE_ONLY=1`) desliga controles | `window.py` | `test_observe_only_guards_legacy_control_actions`, `test_progress_button_is_disabled_in_the_read_only_instance` | TESTE |
| 19 | Fixture de demonstração rotulada "DEMONSTRAÇÃO" | `observability.py` | `test_demo_is_explicit_and_cannot_be_mistaken_for_real` | TESTE |
| 20 | Posição, transparência e intervalos persistidos, posicionamento multi-monitor/DPI | `settings.py`, `geometry.py` | `test_settings_roundtrip_clamp_and_corrupt_file`, 25 testes de `test_agent_bar_panel.py` | OK |
| 21 | CLI `--once-json` / `--screenshot` / `--open-panel` | `app.py` | as 5 execuções acima | OK\* (D7) |
| 22 | Mensagens entre agentes, decisões humanas, heartbeat por worker | — | "indisponíveis: sem contrato persistido" em todas as capturas | AUSENTE (honesto) |
| 23 | Execuções do motor novo, artefatos, manifest/relatórios, imagem do `room_001`, aprovação por HEAD | — | não existe em nenhuma linha | AUSENTE → S00-13 / S00-14 |

**Suítes de teste no HEAD auditado:**

| Linha | Comando | Resultado |
| --- | --- | --- |
| `develop` `366b7a63` | `pytest --ignore=tests/test_agent_bar_panel.py` | **116 passed, 1 failed** (`test_default_gov_dir_stays_inside_this_repository_when_not_configured`, D2) |
| `develop` | `QT_QPA_PLATFORM=offscreen pytest tests/test_agent_bar_panel.py` | **28 passed** (não trava com `offscreen`) |
| `develop` | `ruff check .` | limpo |
| `main` `fdbc7d1` | `pytest` | **13 passed** |

## 4. Débitos técnicos e dependências

| ID | Débito | Evidência | Impacto | Onde resolver |
| --- | --- | --- | --- | --- |
| D1 | `pip install -e .[dev]` **falha** em `develop` (`Multiple top-level packages discovered in a flat-layout: ['agent_bar', 'launchers']`). As instruções do README não funcionam | saída do pip | instalação nova quebrada | M0 |
| D2 | O teste de layout falha quando roda de uma worktree: `default_gov_dir` resolve de propósito o checkout principal (`git --git-common-dir`), mas o teste espera a raiz da worktree | `E:/Claude/apps/agent-bar/runs/governance != …/abar-develop-audit/runs/governance` | suíte vermelha conforme o diretório | M0 |
| D3 | Alvos fixos no código: `GFCDOTA/sketchup-mcp` (`collect_github`, fila de PRs), org `GFCDOTA` Project #1 com colunas `Ready/In Progress/GPT Review/Blocked/Done` (`collect_backlog`), `TASK_NAME`, `LOOP_RE`, URLs dos botões (`controls.py:19-20`) | `once_json_*`, `02_…png` | a fila de PRs e o backlog mostram **o repo e o board antigos**, não o Plan2Space Project #2 (`Backlog/Ready/In Progress/Review/Blocked/Done`) | M1 |
| D4 | O rótulo **"REAL"** significa "schema lido com sucesso", não "produção". Um banco de compatibilidade com eventos sintéticos aparece como `Event Store · REAL` | `05_central_compat_schema_runtime.png` | contradiz "nenhum resultado simulado como evidência real" | M2 (bloqueia S00-13) |
| D5 | O app desktop do Codex (`WindowsApps\OpenAI.Codex_…\codex.exe`) é classificado como "Análise manual", com o logo animado de "trabalhando" | `once_json_default.txt` | falso "trabalhando" | M2 |
| D6 | O `RATE_LIMITED` do Codex de 18/11 (PR #641) aparece como estado atual em âmbar, sem idade | `once_json_atalho_fp060_lifecycle.txt`, `06_…png` | estado velho apresentado como atual | M2 |
| D7 | `--once-json` redirecionado sai em cp1252, não em UTF-8; mojibake na mensagem do Codex (`’ve`) | `once_*.txt` brutos | diagnóstico ilegível fora do console | M0 |
| D8 | Execução depende do ambiente: o `python` do sistema (Python312) está quebrado nesta máquina; o atalho `ops\launchers\AGENT-BAR.cmd` roda a **cópia antiga** no worktree `w-fp061-agent-bar-2` do sketchup-mcp (com mudanças não commitadas), usando o venv do sketchup-mcp | `AGENT-BAR.cmd`, `py -0p` | o app do Felipe depende de uma worktree temporária de outro repo (Hard Rules #1/#2 do workspace) | M0 (repontar o atalho com OK do Felipe) |
| D9 | Dois leitores do mesmo schema: `main:src/agent_bar/store_reader.py` (sem UI) e `develop:agent_bar/observability.py` (usado pela UI) | árvores das duas linhas | contrato duplicado que vai divergir | M0 |
| D10 | `develop` não tem CI; só `main` tem `ci.yml` | PR #6 | merges sem check | M0 |
| D11 | Cache de login no módulo não é invalidado por "Atualizar"; `_UNUSED_JQ` morto (já listado em `docs/limitacoes-e-roadmap.md` no `develop`) | doc do `develop` | menor | M1 |

**Dependências externas:** `claude`, `codex`, `gh` no PATH (verificadas: `%APPDATA%\npm\claude`, `codex`, `C:\Program Files\GitHub CLI\gh.exe`); PySide6-Essentials 6.12; Windows (CIM/PowerShell para processos, Win32 para topmost); EventStore SQLite **do `agent-runtime`** (Frente B, dona do schema); schemas de `manifest`/`geometry_report`/`validation_report` **da Frente A** (`engineering-specs` #5/#6), ainda não publicados; S00-02 (`engineering-specs#8`) ainda aberto.

## 5. Proposta de Mission Control (incremental)

**Sem reescrita.** O `develop` tem 145 testes, 13 ADRs e as regras de honestidade que a Sprint exige (ausência = cinza/INDISPONÍVEL; controles com confirmação e preflight fail-closed; EventStore `mode=ro`). Reescrever jogaria isso fora sem ganho. Cada passo abaixo é um PR pequeno, com evidência real, e o anterior continua funcionando.

| Passo | O quê | Aceite | Tarefa |
| --- | --- | --- | --- |
| **M0 — uma linha só** | Trazer a UI do `develop` para o `main` (merge `develop`→`main`; as linhas partilham `5b2f2e9`). Consolidar num único leitor de schema (D9). Corrigir o empacotamento (D1), o teste dependente de worktree (D2), o UTF-8 do `--once-json` (D7) e ligar a CI para a UI (D10, `QT_QPA_PLATFORM=offscreen`) | `pip install -e .[dev]` funciona; suíte inteira verde na CI; inventário da seção 3 sem regressão | S00-07 (continuação) |
| **M0b — atalho** | Repontar `ops\launchers\AGENT-BAR.cmd` para `apps\agent-bar`, com venv próprio. **Só com o OK do Felipe** (Hard Rule #1); a cópia `tools/orchestration/agent_bar` no sketchup-mcp é aposentada lá (S00-01 L13), não por esta frente | o atalho abre a barra deste repo; a worktree antiga fica intocada | S00-07 / Felipe |
| **M1 — alvos configuráveis** | Repo, org/número do Project e colunas configuráveis, com padrão `Plan2Space` Project #2 (D3) | fila e backlog mostram Plan2Space; teste de contrato das colunas | antes do S00-13 |
| **M2 — proveniência** | Trocar "REAL" por "LIDO" + caminho do banco + `schema_version` + idade do último evento; idade em todo estado com cache (D4, D6); separar o app desktop do Codex de trabalho (D5) | banco de compatibilidade **não** aparece como produção; estado velho mostra a idade | S00-13 |
| **M3 — execuções e evidências** | Nova aba "Execuções": run → HEAD → artefatos (`manifest`, `geometry_report.json`, `validation_report.json`, PNG do `room_001`) lidos dos schemas da Frente A. Sem arquivo = "sem evidência". Fixtures marcadas **EXEMPLO** até os schemas saírem | critérios da issue [#8](https://github.com/Plan2Space/agent-bar/issues/8) | S00-13 |
| **M4 — aprovação humana** | Tela de aprovação presa ao **HEAD exato** (SHA mostrado e conferido no momento do clique; HEAD mudou = aprovação invalidada). A ação passa pelo GitHub (review) ou pela API do runtime, **nunca** por escrita no EventStore | critérios de `agent-runtime#58` (parte UI) | S00-14 (UI) |

Permanece **fora** do Mission Control: merge pela barra (ADR-0001), terminal interativo, canal ChatGPT→Claude (sem contrato).

## 6. M0 executado (`feat/s00-07-unify-develop`)

- `develop` entrou no `main` com merge (histórico preservado). O leitor `store_reader.py` foi de `src/agent_bar/` para `agent_bar/`: agora há **um pacote só**.
- **D1 resolvido:** `[tool.setuptools.packages.find] include = ["agent_bar*"]`; `pip install -e .[dev]` funciona num venv limpo.
- **D2 resolvido:** o teste dependente da worktree foi trocado por dois testes que descrevem o comportamento real: checkout principal via `git --git-common-dir` e fallback sem git.
- **D7 resolvido:** `--once-json` força UTF-8 (`test_once_json_writes_utf8_even_when_stdout_is_cp1252`, vermelho antes do fix).
- **D10 resolvido:** a CI roda em `windows-latest` (3.11 e 3.12) com `QT_QPA_PLATFORM=offscreen`, `ruff` e a suíte inteira.
- **D9 parcial:** os dois leitores estão no mesmo pacote e testados juntos; fundir as duas projeções fica para M2.
- Suíte local no HEAD do M0: **160 passed**; `ruff` limpo. Smoke real com a configuração do atalho: `--once-json` em UTF-8 e Central completa igual à captura `06`.
- M0b (repontar o atalho da área de trabalho) continua aguardando o OK do Felipe.
