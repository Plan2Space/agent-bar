# FP-061 — Agent Bar 2.0 / Harness Observability & Control Center

Status: implementação incremental em branch isolada `feat/fp061-agent-bar-2`.
Base: `origin/develop` em `7ea99dae`. Esta frente não altera o executor, o supervisor, o Event Store, a governança nem workflows.

## 1. Auditoria da FP-058

A aplicação existente é PySide6 em `agent_bar/`. `window.py` contém a barra flutuante de 550 × 65, o painel de 560 px, coleta em thread, arraste, limites de monitor e controles antigos. `collectors.py` lê `event_bridge.json`, logs, processos CIM, Tarefa Agendada e GitHub. `models.py` guarda o snapshot. `controls.py` contém operações de abrir links e parar/reiniciar o listener em DRY_RUN. Há testes de coletores, controles e GUI offscreen.

Lacunas encontradas: não havia visão do Event Store SQLite, GPT, timeline estruturada, fila por evento, subprocessos, DLQ, outbox, métricas nem um modo completo. O painel antigo mostra histórico do bridge e resumo do backlog do Project #1, mas não mensagens reais entre agentes. Não há contrato persistido de conversas, decisões humanas ou heartbeat individual dos workers no HEAD analisado.

## 2. Experiência

| Modo | Conteúdo | Entrada |
| --- | --- | --- |
| Recolhido | Barra com Claude, Codex, GPT, Listener, GitHub e PR; cinza significa indisponível | Início ou fechar painel |
| Expandido | Estado dos agentes, saúde, backlog, eventos do bridge e resumo do Event Store | Clique na barra |
| Completo | Timeline SQLite, PR/CI, fila/causalidade, subprocessos e leases, retry/DLQ, outbox, decisões, mensagens, métricas e cauda de terminal | Botão `Central completa` |

O mesmo processo, worker de coleta e painel são reutilizados. O tema é escuro e translúcido; a abertura do painel usa fade curto. A janela continua limitada à área útil do monitor e rola internamente.

## 3. Arquitetura e contratos

`observability.py` projeta um snapshot do SQLite; `operations_view.py` só apresenta os dados. A conexão usa `file:...?...mode=ro`, `PRAGMA query_only=ON`, timeout curto e é fechada após cada coleta. O observador **não** chama `EventStore()` nem `store_factory.get_store()` porque esses caminhos inicializam schema/WAL. O banco é `EVENT_STORE_DB_PATH`, se definido, ou `AGENT_BAR_GOV_DIR/event_store.db` (o diretório padrão da barra continua o checkout principal). Ausência, erro e versão incompatível aparecem como indisponíveis; nenhuma tabela é criada.

Contrato verificado em `tools/orchestration/event_store.py`, versão 1:

- `events`: tipo, job, status `PENDING/RUNNING/DONE/DLQ/SUPERSEDED/RECORDED`, tentativas, `parent_event_id`, lease, timestamps e erro. `parent_event_id` é **causalidade**, não uma declaração genérica de dependências.
- `transitions`: `event_id`, horário, estado anterior/novo, ator e detalhe. Alimenta a timeline.
- `subprocess_runs`: agente, PID, início/fim, resultado e caminhos stdout/stderr. Uma linha sem `ended_at` significa registro aberto; não comprova que o processo ainda está vivo.
- `outbox`: contagem pendente. Não é um comando para entregar ou repetir.
- `meta.schema_version`: deve ser `1`. Versão diferente não é interpretada por tentativa.

Terminal: a cada coleta lê no máximo 8 KiB finais dos arquivos stdout/stderr registrados; somente arquivos resolvidos dentro do diretório do banco são aceitos. Mostra até 30 linhas e informa que o fluxo é polling. Nada é anexado ou escrito nos arquivos.

GitHub e Project #1 continuam com os coletores e caches existentes. O Event Store está em shadow mode no `develop` analisado: a fila JSON do bridge ainda é a execução primária. Assim, contagens SQLite são rotuladas `Event Store` e não substituem o estado do listener ou a fila JSON. A visão de PR/CI contém fatos persistidos, não um julgamento próprio de merge.

## 4. Honestidade e limites

Produção é rotulada **REAL** somente após leitura bem-sucedida do schema. `AGENT_BAR_DEMO=1` rotula uma fixture **DEMONSTRAÇÃO** de forma visível. Sem banco, o painel mostra **INDISPONÍVEL**. O GPT fica indisponível porque não há fonte de status contratada. Mensagens/conversas e decisões humanas ficam indisponíveis até existir um contrato persistido; eventos e logs não são apresentados como mensagens. Heartbeat individual dos workers não é inferido do lease. O heartbeat do listener permanece na visão FP-058.

As operações antigas de parar/reiniciar o listener continuam com o preflight FP-058. A nova central não adiciona comandos ao Harness; todos os novos cartões são somente leitura. Uma futura integração de decisões requer API de comandos, autorização e contrato próprios.

## 5. Fases e aceite

1. **Neste incremento:** projeção read-only versionada, três modos, cartões operacionais, GPT indisponível explícito, cauda limitada, testes de fixture e evidência visual offscreen.
2. **Após contrato FP-060:** ligar mensagens, decisões humanas e heartbeat por worker a fontes autoritativas, com identificadores, timestamps e proveniência; validar em ambiente isolado antes da produção.
3. **Após validação operacional:** revisar densidade, navegação, acessibilidade e desempenho com dados reais, sem acionar o Harness durante a inspeção.

Aceite do incremento: nenhum write em banco ou logs de produção, nenhuma criação de banco quando ausente, estados ausentes explícitos, troca entre os três modos sem novo processo, testes de projeção e GUI verdes, screenshot da fixture com rótulo de demonstração. Captura offscreen é evidência de layout, não veredito visual em desktop real.
