# agent-bar

Barra flutuante do Windows (PySide6) que mostra, num relance, se **Claude Code, Codex, GPT, a automação (laço de revisão / listener) e o GitHub** estão conectados, e quando um deles está **trabalhando**. É só observação: não faz merge, não faz push e não altera o que os agentes estão fazendo (a única ação é o botão de "dar andamento", abaixo, sempre com confirmação).

> **Documentação completa:** [`docs/README.md`](docs/README.md): arquitetura, fontes de dados, regras exatas de cada estado, interface, fila de PRs, passo do Claude, segurança, testes, limitações/roadmap e [ADRs](docs/adr/README.md) (13 decisões), com diagramas Mermaid.

## Como ler a barra

| Visual | Significa |
| --- | --- |
| bolinha **verde** | conectado (CLI logada / processo vivo) |
| bolinha **cinza** | desconectado, sem CLI ou sem integração (nunca é "sucesso") |
| bolinha **âmbar** | atenção (por exemplo: limite de uso do revisor, heartbeat atrasado) |
| bolinha **vermelha** | erro (por exemplo: CLI sem login) |
| **logo animado** do agente | está **trabalhando** agora |

Cada agente tem o seu logo animado (estrela do Claude, balão `>_` do Codex, roseta do GPT, arcos do laço de revisão, órbita do GitHub). Para usar logos oficiais, coloque `claude.png`, `codex.png`, `gpt.png`, `listener.png` ou `github.png` em `%APPDATA%\agent_bar\icons\` (ou aponte `AGENT_BAR_ICONS`).

## Painel (clique na barra)

- estado detalhado de cada agente e legenda das cores;
- **fila de PRs** abertos do repositório (pronto para mergear / a corrigir / conflito / CI falhando / aguardando revisão / rascunho), com links;
- backlog do Project, eventos recentes e visão do EventStore (modo "Central completa");
- **terminal embutido (somente leitura)** com o progresso do passo em andamento e botão **Parar passo**;
- botão **Dar andamento (Claude)** (também como ▶ na própria barra): depois de confirmar, roda **um** passo (`claude -p`, modo auto) com a missão definida em `AGENT_BAR_MISSION`. Nunca faz merge.

## Instalar e rodar

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[gui]
.venv\Scripts\python -m agent_bar.app                 # abre a barra
.venv\Scripts\python -m agent_bar.app --once-json     # diagnóstico em texto, sem interface
powershell -File launchers\run_agent_bar.ps1          # atalho para o mesmo
```

Requer Windows, Python 3.11+ e, para os estados reais, as CLIs `claude`, `codex` e `gh` no PATH.

## Configuração (variáveis de ambiente, todas opcionais)

| Variável | Para quê |
| --- | --- |
| `AGENT_BAR_GOV_DIR` | pasta de estado do orquestrador observado (fila do bridge, logs) |
| `EVENT_STORE_DB_PATH` | banco SQLite do EventStore lido em modo somente leitura |
| `AGENT_BAR_HARNESS_LOG` | log do laço de revisão, usado como fallback do terminal |
| `AGENT_BAR_MISSION` | arquivo `.md` com a missão do botão "Dar andamento" |
| `AGENT_BAR_WORKSPACE` | pasta de trabalho do passo (padrão: a primeira pasta acima que tem `ops/launchers`, senão o diretório atual) |
| `AGENT_BAR_SETTINGS` | caminho do `settings.json` (padrão: `%APPDATA%\agent_bar\settings.json`) |
| `AGENT_BAR_ICONS` | pasta com logos próprios |
| `AGENT_BAR_OBSERVE_ONLY=1` | desativa todos os controles (parar/reiniciar listener, dar andamento) |
| `AGENT_BAR_DEMO=1` | usa uma fixture rotulada "DEMONSTRAÇÃO" (nunca confundir com dado real) |

## Testes

```powershell
.venv\Scripts\pip install -e .[dev]
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python -m pytest -q
```

## Origem e limites

Extraído do repositório [GFCDOTA/sketchup-mcp](https://github.com/GFCDOTA/sketchup-mcp) (frentes FP-058 e FP-061). Veja `docs/agent_bar.md`, `docs/specs/FP-061_agent_bar_control_center.md` e as capturas de demonstração em `docs/reports/fp061/`.

- **GPT**: ainda sem fonte de estado; aparece cinza ("sem integração").
- **Mensagens entre agentes** (por exemplo, ChatGPT para Claude Code sem copiar e colar): ainda **não existem**; dependem de um contrato persistido e de uma API de comandos com autorização.
- A evidência visual dos testes é de renderização *offscreen*; a validação em desktop real é do dono do projeto.
- `tests/support/event_store.py` é uma cópia do EventStore do `sketchup-mcp`, usada só para criar o banco de fixture dos testes.
- Ainda não há licença definida neste repositório.
