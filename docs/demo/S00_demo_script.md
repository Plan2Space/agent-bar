# S00: roteiro de demonstração do Agent Bar (~10 min)

> Sprint 00 · Plan2Space Rebuild · Frente C · issues [#7](https://github.com/Plan2Space/agent-bar/issues/7) e [#8](https://github.com/Plan2Space/agent-bar/issues/8).
> Objetivo: o Felipe abre a barra, usa e responde se ela ajuda. **O veredito é dele.**
> Tudo o que a barra mostra é lido de uma fonte real ou aparece escrito como ausente (`INDISPONÍVEL`, `SEM FONTE`, `SEM DADOS`, `sem dados`). Nenhum número é inventado.

## 1. Preparar uma vez

Requisitos: Windows 11, Python 3.11+ (nesta máquina, o CPython 3.12 do `uv`, porque o `python` global está quebrado) e as CLIs `gh`, `claude` e `codex` no PATH. As CLIs são opcionais: sem uma delas, o agente correspondente fica cinza.

```powershell
cd <checkout do agent-bar>
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .[gui]
copy launchers\local.example.cmd launchers\local.cmd   # ajuste os caminhos desta máquina (fora do git)
```

O `launchers\local.cmd` diz onde estão as fontes de dados:

| Variável | Para a demo | Sem ela |
| --- | --- | --- |
| `EVENT_STORE_DB_PATH` (+ `AGENT_BAR_GOV_DIR`) | `store.db` do laço de revisão FP-060 (schema 1, o mesmo do `agent-runtime`) | `Event Store · INDISPONÍVEL` |
| `AGENT_BAR_HARNESS_LOG` | `run*.log` mais recente do laço | terminal: "Saída de terminal indisponível" |
| `AGENT_BAR_RUNS_DIR` | `sketchup-mcp-next\runs` | card de execuções: `SEM FONTE` |

## 2. Iniciar e encerrar

- **Iniciar (Explorer):** duplo clique em `launchers\AGENT-BAR.cmd`. Com uma barra já aberta, o duplo clique não abre outra.
- **Iniciar (PowerShell, a partir do repo):**
  ```powershell
  .venv\Scripts\python -m agent_bar.app
  ```
- **Diagnóstico sem janela** (o mesmo estado, em texto UTF-8):
  ```powershell
  .venv\Scripts\python -m agent_bar.app --once-json
  ```
- **Encerrar:** clique direito na barra → *Encerrar barra (fecha a barra inteira)*. O `✕` do painel fecha só o painel. Nenhum dos dois para Claude, Codex ou o laço de revisão.
- **Não há componente web.** É uma janela PySide6: não existe porta nem URL local.

## 3. Roteiro (~10 min)

| Min | Passo | Onde olhar | O que deve aparecer |
| --- | --- | --- | --- |
| 0–1 | Abrir a barra | barra flutuante | 5 indicadores. Cor = conexão (verde logado, vermelho sem login, cinza sem fonte); logo animado = trabalhando |
| 1–2 | **Achar uma tarefa em execução** | clique na barra → painel; *Central completa ↗* → "Fila e dependências" e "Workers e leases" | eventos `PENDING`/`RUNNING` do EventStore lido, com lease. "Registro aberto não comprova processo vivo." |
| 2–3 | **Ver qual agente** | painel, linhas Claude/Codex | Claude: sessões interativas vivas (pids), separadas das do orquestrador. Codex: "Limite de uso", com a idade do registro |
| 3–4 | **Ver logs e erros** | Central completa → "Saída observável dos terminais", "Retries, falhas e DLQ" | as últimas linhas do log do coordenador; DLQ com tentativa `5/5` |
| 4–5 | **Saber de onde vem o dado** | painel → linha `Event Store · LIDO` | caminho do `store.db`, `schema 1`, "último evento há …" e "lido não comprova produção" |
| 5–6 | **Abrir evidências** | Central completa → "Execuções do motor" | hoje: `SEM DADOS` (o motor novo ainda não gravou `runs/`). Quando a Frente A gravar: run, HEAD, verdict, `visual_verdict = PENDING_HUMAN`, links para manifest e relatórios, preview conferido por sha256 |
| 6–7 | **Comparar resultados** | Central completa → "Benchmark Claude \| Qwen \| Llama" | hoje: `sem dados` nas três colunas, porque o contrato `benchmark_result` v1 da Frente B ainda não saiu |
| 7–8 | **Identificar PR bloqueado** | painel → linha "PRs" | PRs abertos de **todos os repos Plan2Space**, classificados (conflito, CI, a corrigir, aguarda revisão, rascunho). Link direto para os que pedem ação |
| 8–9 | **Ver o board** | painel → "Backlog"; botão *Project #2* | contagem por coluna do Project #2 |
| 9–10 | **Decisão humana pendente?** | Central completa → "Decisões humanas" | hoje: "indisponíveis: sem contrato persistido". A tela de aprovação presa ao HEAD é a issue S00-14 (UI) |

**Botões que agem** (só com confirmação): *Dar andamento (Claude)*, *Parar*, *Reiniciar DRY_RUN*. Fora da demo. Não clique sem decidir: o primeiro roda um passo real de `claude -p`.

## 4. Execução real registrada em 2026-11-20 (Frente C)

Rodado a partir do worktree `E:\Claude\worktrees\p2s-c-s00-13` (branch `docs/s00-13-demo`), com o `local.cmd` da tabela acima:

1. `launchers\AGENT-BAR.cmd` iniciou a barra (venv `pythonw.exe` → CPython 3.12.13, saída 0). Um segundo duplo clique **não** abriu outra (2 processos antes e depois: o lançador do venv e o filho).
2. `--once-json` com as fontes reais:
   - backlog do Project #2 lido (`Backlog=0, Ready=0, In Progress=7, Review=3, Blocked=0, Done=4`);
   - 5 PRs abertos da org (`claude-engineering-skills#42=conflito`, `agent-bar#13`, `agent-bar#14`, `sketchup-mcp#685`, `engineering-specs#13=rascunho`);
   - EventStore `lido (schema 1)` com `DONE 281, SUPERSEDED 48, PENDING 2, DLQ 2, RECORDED 3`;
   - `execucoes do motor: sem_dados (pasta de execucoes nao existe)` e `benchmark: sem_dados`.
3. Capturas (Qt `offscreen`, mesmas fontes, via `python -m agent_bar.app --screenshot X.png --full`): barra, painel, Central completa e o widget inteiro de operações. Elas mostram o terminal embutido com o log real do coordenador. As capturas trazem caminhos locais, pids e o login do GitHub, então **não** foram versionadas neste repo público: ficam na pasta de notas da sessão coordenadora (`frente-c-evidencias/`). O veredito visual no desktop real é do Felipe.

**Conexão com o runtime: honesta, não completa.** Ainda não existe um EventStore do `agent-runtime` com job real (a Frente B não publicou um). A barra lê o `store.db` do laço FP-060, que usa o mesmo schema 1 (o `event_store.py` do runtime foi portado dele), e o rotula `LIDO`, nunca "produção". Quando o runtime gravar um banco real, basta apontar `EVENT_STORE_DB_PATH` para ele.

**Terminais existentes continuam funcionando:** o terminal embutido mostra as linhas do log real (captura `_ops`). A suíte inteira (incluindo `test_stream_lines_become_readable_terminal_lines` e os testes do passo com streaming) passa no HEAD. Nenhuma funcionalidade do inventário S00-07 foi removida.

## 5. Reproduzir

```powershell
cd <checkout do agent-bar>
.venv\Scripts\python -m pytest -q                                  # QT_QPA_PLATFORM=offscreen para não abrir janelas
cmd /c "call launchers\local.cmd & set "QT_QPA_PLATFORM=offscreen" & .venv\Scripts\python -m agent_bar.app --screenshot %TEMP%\abar.png --full"
.venv\Scripts\python -m agent_bar.app --once-json
```

## 6. Perguntas para o Felipe

1. **Ajuda?** Depois de 10 minutos, a barra respondeu "o que está rodando, quem está rodando e o que está travado" mais rápido que abrir GitHub e terminais?
2. **Confuso?** Algum rótulo ou cor induziu ao erro (ex.: `LIDO` vs. produção, Codex "Limite de uso" com dado de 1 dia)?
3. **Falta?** O que você procurou e não achou?
4. **Remover?** Algum card ou botão que só ocupa espaço (ex.: Listener FP-057 desligado, Pipeline PR/CI do laço antigo)?
5. **Prioridade da próxima sprint:** (a) execuções reais do motor com preview lado a lado; (b) benchmark Claude×Qwen×Llama; (c) tela de aprovação presa ao HEAD (S00-14); (d) repontar o atalho da área de trabalho para este repo (M0b).

As sugestões de UI só viram issue depois da resposta dele.
