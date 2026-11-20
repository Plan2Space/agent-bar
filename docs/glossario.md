# Glossário

| Termo | Significado |
| --- | --- |
| **Agent Bar** | a barra flutuante deste projeto |
| **Barra** | a janela pequena e sempre visível (`Bar`) com os 5 ícones, o PR atual, o botão ▶ e a engrenagem |
| **Painel / Agent Control** | a janela que abre ao clicar na barra (`Details`) |
| **Central completa / Harness Control Center** | o **modo largo** do mesmo painel, com cartões de observabilidade |
| **Snapshot** | retrato imutável de **todo** o estado observado num ciclo de coleta; é o que a UI renderiza |
| **Level (nível)** | `GREEN`, `GRAY`, `YELLOW`, `RED`, `BLUE`: a classe de estado de um agente |
| **Trabalhando** | `Level.BLUE`; na barra vira o **logo animado** do agente |
| **Conectado** | verde: CLI logada ou processo/serviço vivo |
| **Desconectado / sem integração** | cinza: sem CLI, sem fonte de dado ou desligado; **nunca** é sucesso |
| **Atenção** | âmbar: algo precisa de olhar (limite de uso, heartbeat ausente, kill switch...) |
| **Fail-closed (falhar fechado)** | na dúvida, recusa e explica; não presume sucesso |
| **TTL** | tempo de vida de um valor em cache antes de ser consultado de novo |
| **Última leitura boa (`last_good`)** | último resultado sem erro, exibido marcado como "desatualizado" quando a leitura nova falha |
| **Listener** | o processo `event_executor run` que consome a fila do bridge; no chip "Listener" também aparece o **laço de revisão** |
| **Laço de revisão (`pr_lifecycle run`)** | processo contínuo que enfileira e executa revisões de PR pelo Codex; é a automação viva da FP-060 |
| **Bridge (`event_bridge`)** | componente que lê eventos do GitHub e grava `event_bridge.json`/`.log` |
| **Executor (`event_executor`)** | consome a fila e pode executar um passo do Claude por item (fora do escopo da barra) |
| **InstanceLock** | trava de instância única do executor; guarda o PID a partir do byte 16 |
| **Kill switch (`EXECUTOR_DISABLED`)** | arquivo cuja existência impede o executor de consumir a fila |
| **DRY_RUN / EXECUTE** | modos do listener: só planeja / executa de verdade |
| **Pasta de governança (gov)** | pasta de estado do orquestrador: bridge, logs, lock, kill switch, logs do executor, registro do Codex |
| **EventStore** | barramento de eventos durável em SQLite da orquestração FP-060; a barra o lê em modo somente leitura |
| **DLQ** | *dead-letter queue*: eventos que esgotaram as tentativas |
| **Outbox** | fila de publicações pendentes do EventStore (a barra só conta) |
| **Lease** | concessão temporária de um evento a um worker |
| **Passo** | uma execução de `claude -p` iniciada pelo botão **Dar andamento (Claude)** |
| **Missão** | arquivo `.md` que o passo lê e executa (`AGENT_BAR_MISSION`) |
| **`stream-json`** | formato de saída do `claude -p` com um evento JSON por linha, que a barra traduz em texto |
| **Fila de PRs** | lista dos PRs abertos classificada em `pronto`, `corrigir`, `conflito`, `ci`, `aguarda`, `rascunho` |
| **Revisor independente** | o Codex, que revisa PRs e publica o parecer; a barra só observa |
| **Limite de uso** | a conta do revisor atingiu a cota; é **atenção** (âmbar), volta sozinha no reset |
| **Topmost** | janela "sempre no topo" (`HWND_TOPMOST`); a barra reafirma isso a cada 500 ms |
| **Offscreen** | modo do Qt que renderiza sem janela real; usado nos testes e nas capturas |
| **Fixture / demonstração** | dados sintéticos rotulados **DEMONSTRAÇÃO**, nunca confundíveis com dado real |
| **Harness** | o conjunto de orquestração que a barra observa (FP-060) |
| **FP-058 / FP-060 / FP-061** | frentes do projeto de origem: a barra original / a orquestração dirigida por eventos / a evolução do Agent Bar |
| **ADR** | *Architecture Decision Record*: registro de uma decisão e do porquê |
