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
copy launchers\local.example.env launchers\local.env       # ajuste os caminhos desta máquina (fora do git)
powershell -NoProfile -File launchers\install_shortcut.ps1  # atalho "Plan2Space Agent Bar" na Área de Trabalho
```

O `launchers\local.env` diz onde estão as fontes de dados:

| Chave | Para a demo | Sem ela |
| --- | --- | --- |
| `EVENT_STORE_DB_PATH` (+ `AGENT_BAR_GOV_DIR`) | `store.db` do laço de revisão FP-060 (schema 1, o mesmo do `agent-runtime`) | `Event Store · INDISPONÍVEL` |
| `AGENT_BAR_HARNESS_LOG` | `run*.log` do laço | terminal: "Saída de terminal indisponível" |
| `AGENT_BAR_RUNS_DIR` | `artifacts\` do `sketchup-mcp-next` (cópia somente leitura via `git archive`) | card de execuções: `SEM FONTE` |
| `AGENT_BAR_BOARD` | omitido = Plan2Space Project #2 | — (`legacy` mostra o board antigo, marcado LEGADO) |

## 2. Iniciar e encerrar

- **Iniciar:** duplo clique no atalho **Plan2Space Agent Bar** da Área de Trabalho. Também funcionam `launchers\AGENT-BAR.cmd` (Explorer) ou, a partir do repo, no PowerShell:
  ```powershell
  .venv\Scripts\python -m agent_bar.app
  ```
- **Abrir de novo** com a barra aberta não cria outra: a existente vem para a frente e abre o painel.
- **Diagnóstico sem janela** (o mesmo estado, em texto UTF-8):
  ```powershell
  .venv\Scripts\python -m agent_bar.app --once-json
  ```
- **Encerrar:** clique direito na barra → *Encerrar barra (fecha a barra inteira)*. O `✕` do painel fecha só o painel. Nenhum dos dois para Claude, Codex ou o laço de revisão.
- **Log:** `%LOCALAPPDATA%\agent_bar\logs\agent_bar.log`. Uma falha antes da UI aparece numa caixa de erro.
- **Não há componente web.** É uma janela PySide6: não existe porta nem URL local.

## 3. Roteiro (~10 min)

| Min | Passo | Onde olhar | O que deve aparecer |
| --- | --- | --- | --- |
| 0–1 | Abrir pelo atalho | barra flutuante (sobre a barra de tarefas) | 5 indicadores. Cor = conexão (verde logado, vermelho sem login, cinza sem fonte); logo animado = trabalhando |
| 1–2 | **Ver o board do rebuild** | clique na barra → painel → "Board" | `Plan2Space Rebuild` (link), `#2 · PVT_kwDOFV8G784BsjNn`, contagem por coluna e cada item com Task ID, status e `repo#n`, lidos no momento da consulta |
| 2–3 | **Achar uma tarefa em execução** | Board (In Progress); *Central completa ↗* → "Fila e dependências", "Workers e leases" | Task IDs em In Progress; eventos `PENDING`/`RUNNING` do EventStore lido, com lease. "Registro aberto não comprova processo vivo." |
| 3–4 | **Ver qual agente** | painel, linhas Claude/Codex | Claude: sessões interativas vivas (pids). Codex: "Limite de uso", com a idade do registro |
| 4–5 | **Ver logs e erros** | Central completa → "Saída observável dos terminais", "Retries, falhas e DLQ" | as últimas linhas do log do coordenador; DLQ com tentativa `5/5` |
| 5–6 | **Saber de onde vem o dado** | painel → `Event Store · LIDO` | caminho do `store.db`, `schema 1`, "último evento há …" e "lido não comprova produção" |
| 6–8 | **Abrir evidências e comparar resultados** | Central completa → "Execuções do motor" | as 3 execuções reais do `sketchup-mcp-next@938e6df`: `room_001` PASS, `room_alt_5x3` PASS, `room_001-planner_error` FAIL (`plano forçado`, check reprovado `opening.door_01.width: esperado 0.8, medido 0.82`). HEAD `43b0cef`, `visual PENDING_HUMAN`, links para manifest e relatórios, preview do `room_001` conferido por sha256 |
| 8 | **Benchmark** | Central completa → "Benchmark Claude \| Qwen \| Llama" | `sem dados` nas três colunas: os resultados da Frente B ainda não foram publicados |
| 8–9 | **Identificar PR bloqueado** | painel → linha "PRs" | PRs abertos de **todos os repos Plan2Space**, classificados (conflito, CI, a corrigir, aguarda revisão, rascunho). Link para os que pedem ação |
| 9–10 | **Decisão humana pendente?** | Central completa → "Decisões humanas" | hoje: "indisponíveis: sem contrato persistido". A tela de aprovação presa ao HEAD é a [#10](https://github.com/Plan2Space/agent-bar/issues/10) (S00-14 UI) |

**Botões que agem** (só com confirmação): *Dar andamento (Claude)*, *Parar*, *Reiniciar DRY_RUN*. Fora da demo. Não clique sem decidir: o primeiro roda um passo real de `claude -p`.

## 4. Execução real registrada em 2026-11-20 (Frente C)

A partir da worktree `E:\Claude\worktrees\p2s-c-s00-13`, com o `local.env` da tabela acima:

1. `install_shortcut.ps1` criou `Desktop\Plan2Space Agent Bar.lnk` (alvo: `.venv\Scripts\pythonw.exe -m agent_bar.app`; pasta de trabalho: o checkout; ícone `launchers\agent_bar.ico`).
2. Abrir pelo atalho iniciou a barra (log: `iniciando … local.env=[EVENT_STORE_DB_PATH, AGENT_BAR_GOV_DIR, AGENT_BAR_HARNESS_LOG, AGENT_BAR_RUNS_DIR]`, `barra visivel`). Abrir pela segunda vez registrou `segunda abertura: trazendo a barra existente para a frente`, e a segunda instância saiu: continuaram 2 processos, o lançador do venv e o filho.
3. `--once-json` com as fontes reais: Project #2 com 15 itens e Task IDs; 5–6 PRs abertos da org; EventStore `lido (schema 1)`; `execucoes do motor: lido (3 execucao(oes))`, todas com `evidencia conferida (sha256)`; `benchmark: sem_dados`.
4. Capturas da **janela real aberta pelo atalho** (barra e painel) e capturas *offscreen* da Central completa. Elas trazem caminhos locais, pids e o login do GitHub, então **não** foram versionadas neste repo público: ficam na pasta de notas da sessão coordenadora (`frente-c-evidencias/`). O veredito visual é do Felipe.

**Conexão com o runtime: honesta, não completa.** Ainda não existe um EventStore do `agent-runtime` com job real. A Frente B avisou que o benchmark do Llama vai gerar um. A barra lê o `store.db` do laço FP-060, que usa o mesmo schema 1, e o rotula `LIDO`, nunca "produção". Quando o banco real sair, basta apontar `EVENT_STORE_DB_PATH` para ele no `local.env`.

**Terminais existentes continuam funcionando:** o terminal embutido mostra as linhas do log real, e a suíte inteira passa (inclui streaming do passo e terminal somente leitura). Nenhuma funcionalidade do inventário S00-07 foi removida.

## 5. Reproduzir

```powershell
cd <checkout do agent-bar>
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python -m pytest -q; Remove-Item Env:QT_QPA_PLATFORM
.venv\Scripts\python -m agent_bar.app --once-json
.venv\Scripts\python -m agent_bar.app --screenshot $env:TEMP\abar.png --full   # grava abar.png, _panel, _full e _ops
```

## 6. Perguntas para o Felipe

1. **Ajuda?** Depois de 10 minutos, a barra respondeu "o que está rodando, quem está rodando e o que está travado" mais rápido que abrir GitHub e terminais?
2. **Confuso?** Algum rótulo ou cor induziu ao erro (ex.: `LIDO` vs. produção, Codex "Limite de uso" com dado de 1 dia, `plano forçado`)?
3. **Falta?** O que você procurou e não achou?
4. **Remover?** Algum card ou botão que só ocupa espaço (ex.: Listener FP-057 desligado, Pipeline PR/CI do laço antigo)?
5. **Prioridade da próxima sprint:** (a) previews lado a lado das execuções do motor (mesma câmera); (b) benchmark Claude×Qwen×Llama; (c) tela de aprovação presa ao HEAD (S00-14); (d) empacotar como `.exe` (ver `docs/proposals/empacotamento-windows.md`).

As sugestões de UI só viram issue depois da resposta dele.
