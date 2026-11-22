# P2S Studio

> Nome do produto desde 2026-11-22 (escolha do Felipe). O repositório continua `Plan2Space/agent-bar` e o pacote Python, `agent_bar`.

> **Conhecimento compartilhado:** skills reutilizáveis e contratos entre repositórios ficam em
> `Plan2Space/claude-engineering-skills`; SPECs e testes da interface permanecem aqui.
> Ver `CLAUDE.md` e `AGENTS.md` para a referência versionada e os próximos passos.

> **Estado (S00-07):** inventário funcional com evidência e proposta de Mission Control em
> [`docs/audit/S00-07_inventario_agent_bar.md`](docs/audit/S00-07_inventario_agent_bar.md).
> O leitor somente-leitura do EventStore do `agent-runtime` está em `agent_bar/store_reader.py`
> (contrato: [`docs/contracts/READER_CONTRACT.md`](docs/contracts/READER_CONTRACT.md)). O Agent Bar **nunca** grava no EventStore.

Barra flutuante do Windows (PySide6) que mostra, num relance, se o **Claude Code** e o **GitHub** estão conectados, a fila de PRs da org Plan2Space e o que espera por você. É só observação: não faz merge, não faz push e não inicia nem para agentes. Conversar com agentes, rodar fluxos e revisar runs visualmente é no **cockpit ACP** (botão **Cockpit**).

> **Limpeza do legado (2026-11-22):** o orquestrador FP-057/FP-060 do `sketchup-mcp` (listener/event bridge, laço de revisão do Codex, passo "Dar andamento" e chip GPT) saiu da barra. Inventário e decisões: [`docs/audit/legacy-cleanup-2026-11-22.md`](docs/audit/legacy-cleanup-2026-11-22.md). Partes de `docs/` que descrevem essas peças são histórico.

> **Documentação completa:** [`docs/README.md`](docs/README.md): arquitetura, fontes de dados, regras exatas de cada estado, interface, fila de PRs, passo do Claude, segurança, testes, limitações/roadmap e [ADRs](docs/adr/README.md) (13 decisões), com diagramas Mermaid.

## Como ler a barra

| Visual | Significa |
| --- | --- |
| bolinha **verde** | conectado (CLI logada / processo vivo) |
| bolinha **cinza** | desconectado, sem CLI ou sem integração (nunca é "sucesso") |
| bolinha **âmbar** | atenção (por exemplo: sessão do Claude viva com login não confirmado) |
| bolinha **vermelha** | erro (por exemplo: CLI sem login) |
| **logo animado** do agente | está **trabalhando** agora |

O GitHub fica verde só quando uma leitura real da coleta (fila de PRs ou Project) funcionou; quando nada foi lido, o `gh auth status` explica o porquê. Para usar logos oficiais, coloque `claude.png` ou `github.png` em `%APPDATA%\agent_bar\icons\` (ou aponte `AGENT_BAR_ICONS`).

## Painel (clique na barra)

- estado detalhado de cada agente e legenda das cores;
- **"Precisa de você"**: PRs HIGH esperando a sua aprovação no HEAD atual;
- **fila de PRs** abertos da org (pronto para mergear / a corrigir / conflito / CI falhando / aguardando revisão / rascunho), com links;
- Project #2 e, na **Central completa**, o EventStore do agent-runtime, as execuções do motor, o benchmark e a aprovação S00-14;
- botão **Cockpit ACP** (também **Cockpit** na própria barra): abre o cockpit (veja abaixo).

## Cockpit ACP

Janela própria, aberta pelo botão **Cockpit ACP** do painel ou com `python -m agent_bar.app --cockpit`. Nela você conversa com um agente de código pelo [Agent Client Protocol](https://agentclientprotocol.com). O cockpit mostra o texto e o raciocínio em streaming, um cartão por ferramenta e o plano do agente. Toda permissão pedida abre um diálogo, e fechar o diálogo recusa. O histórico fica em `%LOCALAPPDATA%\agent_bar\cockpit\chats\`.

- **Agente:** o real é o adaptador ACP do Claude Code (binário `claude-agent-acp` do pacote npm oficial `@agentclientprotocol/claude-agent-acp`, ou outro comando em `AGENT_BAR_COCKPIT_AGENT`). Enquanto ele não estiver instalado, aparece como indisponível e o cockpit usa o agente **Demonstração (sem LLM)**, que não lê nem executa nada.
- **Workspace:** as pastas de `E:\Claude\apps` com `.git` (ou `AGENT_BAR_COCKPIT_WORKSPACES`, separadas por `;`).
- **Limites:** o agente roda com o ambiente filtrado, sem `ANTHROPIC_*`, `GH_*` nem nada com `TOKEN`, `SECRET` ou `KEY`. O app nunca faz commit, push nem abre PR. Fechar o cockpit encerra só os agentes que ele iniciou.
- **SPEC:** `agent-bar/AGENT-BAR-COCKPIT-ACP.md` no `Plan2Space/engineering-specs`.

## Instalar e rodar

```powershell
py -3.12 -m venv .venv                                  # o python global desta máquina está quebrado: use py/uv
.venv\Scripts\python -m pip install -e .[gui]
.venv\Scripts\python -m agent_bar.app                   # abre a barra
.venv\Scripts\python -m agent_bar.app --once-json       # diagnóstico em texto, sem interface
powershell -NoProfile -File launchers\run_agent_bar.ps1 # o mesmo, sem console; sem .venv explica e sai com 2
```

### Atualizar depois de um `git pull`

O `agent-runtime` é fixado por SHA no `pyproject.toml`. **`pip install -e .[gui]` não o atualiza quando só o SHA muda**, porque a versão continua `0.1.0`. Depois de um pull que mude essa linha, rode, a partir do checkout:

```powershell
.venv\Scripts\python -m pip install -e .[gui]
.venv\Scripts\python -m pip install --force-reinstall --no-deps "agent-runtime @ git+https://github.com/Plan2Space/agent-runtime@<SHA do pyproject>"
```

Feche e reabra a barra. Ao iniciar, ela confere o SHA instalado (`direct_url.json` do pip) contra o do `pyproject.toml`. Se forem diferentes, mostra um aviso vermelho no topo do painel com o comando exato, já com o SHA certo, e registra no log (`%LOCALAPPDATA%\agent_bar\logs\agent_bar.log`). O tooltip da engrenagem também mostra a conferência.

### Atalho na Área de Trabalho

```powershell
powershell -NoProfile -File launchers\install_shortcut.ps1     # cria/recria "P2S Studio" apontando para ESTE checkout (e remove o antigo "Plan2Space Agent Bar")
powershell -NoProfile -File launchers\uninstall_shortcut.ps1   # remove só esse atalho
```

- O atalho roda o `pythonw.exe` do `.venv` deste checkout, sem console. A pasta de trabalho é o checkout e o ícone é `launchers\agent_bar.ico` (gerado por `launchers\make_icon.py`). Sem `.venv`, o script recusa e explica como criar.
- **Um P2S Studio por usuário:** abrir de novo traz o existente para a frente e abre o painel. Se o aberto for de um commit mais velho que o do checkout, aparece "versão nova disponível — fechar a antiga e abrir a nova?"; sim fecha a antiga normalmente (inclusive os agentes do cockpit) e abre a nova. A chave de instância única não mudou com o nome novo, então versão antiga e nova nunca rodam juntas.
- **Log:** `%LOCALAPPDATA%\agent_bar\logs\agent_bar.log`. Se a barra falhar antes de aparecer, surge uma caixa de erro com o caminho do log.
- **Fontes de dados da máquina** (EventStore, execuções do motor, board): `launchers\local.env`, fora do git. O modelo é `launchers\local.example.env`. Só valem chaves `AGENT_BAR_*` e `EVENT_STORE_DB_PATH`, e elas nunca sobrescrevem o ambiente.
- Pelo Explorer também funciona `launchers\AGENT-BAR.cmd` (deste repositório).
- Para trocar o checkout que o atalho abre (ex.: de uma worktree para `apps\agent-bar` depois do merge), rode o `install_shortcut.ps1` do checkout novo.

Para demonstrar o Sprint 00, siga [`docs/demo/S00_demo_script.md`](docs/demo/S00_demo_script.md). Proposta de empacotamento (não implementada): [`docs/proposals/empacotamento-windows.md`](docs/proposals/empacotamento-windows.md).

Requer Windows, Python 3.11+ e, para os estados reais, as CLIs `claude` e `gh` no PATH.

## Configuração (variáveis de ambiente, todas opcionais)

| Variável | Para quê |
| --- | --- |
| `EVENT_STORE_DB_PATH` | banco SQLite do EventStore do agent-runtime, lido em modo somente leitura (sem ele: "não configurado"; não há outra pasta de busca) |
| `AGENT_BAR_RUNS_DIR` | execuções do motor (`artifacts\<cômodo>\<run_id>\run_manifest.json`) |
| `AGENT_BAR_ORG` / `AGENT_BAR_PROJECT` / `AGENT_BAR_COLUMNS` | org, número do Project e colunas (padrão: Plan2Space, #2) |
| `AGENT_BAR_EVIDENCE_DIR` | checkout local do `Plan2Space/evidence` lido pela Revisão visual do cockpit |
| `AGENT_BAR_SETTINGS` | caminho do `settings.json` (padrão: `%APPDATA%\agent_bar\settings.json`) |
| `AGENT_BAR_ICONS` | pasta com logos próprios |
| `AGENT_BAR_OBSERVE_ONLY=1` | desativa o botão de aprovação (S00-14) |
| `AGENT_BAR_DEMO=1` | usa uma fixture rotulada "DEMONSTRAÇÃO" (nunca confundir com dado real) |

## Testes

```powershell
.venv\Scripts\pip install -e .[dev]
$env:QT_QPA_PLATFORM = "offscreen"; .venv\Scripts\python -m pytest -q
```

## Origem e limites

Extraído do repositório [GFCDOTA/sketchup-mcp](https://github.com/GFCDOTA/sketchup-mcp) (frentes FP-058 e FP-061). Veja `docs/agent_bar.md`, a [SPEC FP-061](https://github.com/Plan2Space/engineering-specs/blob/40b76f9ce10ab7e9a51e2a1e03495a9cd1ba2b72/agent-bar/FP-061_agent_bar_control_center.md) (no repo privado `Plan2Space/engineering-specs`, índice em `docs/specs/INDEX.md`) e as capturas de demonstração em `docs/reports/fp061/`.

- **Mensagens entre agentes** (por exemplo, ChatGPT para Claude Code sem copiar e colar): ainda **não existem**; dependem de um contrato persistido e de uma API de comandos com autorização.
- A evidência visual dos testes é de renderização *offscreen*; a validação em desktop real é do dono do projeto.
- `tests/support/event_store.py` é uma cópia do EventStore do `sketchup-mcp`, usada só para criar o banco de fixture dos testes.
- Ainda não há licença definida neste repositório.
