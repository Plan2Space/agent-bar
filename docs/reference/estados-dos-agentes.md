# Estados dos agentes

Esta página responde: **por que a bolinha de cada agente está daquela cor?** As regras estão em `collectors.py` e são avaliadas **em ordem de prioridade** (a primeira que casa vence). Para a semântica geral das cores, ver [Modelo de dados](../architecture/04-modelo-de-dados.md#level-a-semântica-das-cores) e [ADR 0002](../adr/0002-cor-igual-conexao-logo-igual-trabalho.md).

## A regra única

| O que você vê na barra | Significa |
| --- | --- |
| bolinha **verde** | conectado |
| bolinha **cinza** | desconectado, sem CLI, sem integração ou sem dado (**nunca** é sucesso) |
| bolinha **âmbar** | atenção |
| bolinha **vermelha** | erro |
| **logo animado** do agente | trabalhando agora |

Internamente "trabalhando" é `Level.BLUE`; a interface troca a bolinha pelo logo.

---

## Claude (`collect_claude`)

**Entradas:** tabela de processos, raízes do executor (`listener_roots`), CLI instalada?, login (`True`/`False`/`None`), último log do executor.

Classificação dos processos `claude`/`claude.exe`:

- **filho do executor:** descendente (por PPID) de uma raiz do executor.
- **auxiliar do app Desktop:** linha de comando casa `--type=`, `AnthropicClaude` ou `--startup` (não conta como execução).
- **sessão:** qualquer outro `claude` (por exemplo, o Claude Code interativo).
- **passo do botão:** sessão cuja linha de comando contém `fp060-passo.md` (o passo que a barra inicia).

```mermaid
flowchart TD
    A{"tabela de processos<br/>disponível?"}
    A -- não --> A1["GRAY: Indisponível"]
    A -- sim --> B{"há claude filho<br/>do executor?"}
    B -- sim --> B1["BLUE: Executando<br/>(logo animado)"]
    B -- não --> C{"há sessão com<br/>fp060-passo.md na<br/>linha de comando?"}
    C -- sim --> C1["BLUE: Passo FP-060 (botão)<br/>(logo animado)"]
    C -- não --> D{"login conhecido?<br/>(True ou False)"}
    D -- sim --> E{"CLI instalada?"}
    E -- não --> E1["GRAY: Não instalado"]
    E -- sim --> F{"login é False?"}
    F -- sim --> F1["RED: Sem login"]
    F -- não --> G{"há executor e a última<br/>execução falhou?"}
    G -- sim --> G1["RED: Última execução falhou"]
    G -- não --> G2["GREEN: Conectado"]
    D -- não --> H{"há executor?"}
    H -- não --> I{"há sessão viva?"}
    I -- sim --> I1["YELLOW: Sessão interativa"]
    I -- não --> I2["GRAY: Sem orquestrador"]
    H -- sim --> J{"última execução falhou?"}
    J -- sim --> J1["RED: Última execução falhou"]
    J -- não --> J2["YELLOW: Ocioso"]
```

| Estado | Nível | Quando | Detalhe típico |
| --- | --- | --- | --- |
| Indisponível | cinza | tabela de processos não observável | "tabela de processos nao observavel" |
| Executando | azul | claude descendente do executor | "filho do executor, ha Ns" |
| Passo FP-060 (botão) | azul | sessão com `fp060-passo.md` | "sessao aberta pelo botao, ha Ns" (+ "sem orquestrador" se não há executor) |
| Não instalado | cinza | login conhecido, mas `claude` fora do PATH | "CLI nao encontrada" |
| Sem login | vermelho | `claude auth status` diz `loggedIn: false` | "rode `claude auth login`" |
| Última execução falhou | vermelho | há executor e o log mais recente do executor tem falha | nome do log |
| **Conectado** | **verde** | logado (login `True`) | "logado (claude.ai)" + sessões vivas |
| Sessão interativa | âmbar | login desconhecido, sem executor, mas há sessão | "Claude Code vivo, sem vinculo com o executor" |
| Sem orquestrador | cinza | login desconhecido, sem executor, sem sessão | "CLI instalada/nao instalada" |
| Ocioso | âmbar | login desconhecido, com executor, sem falha | "ultima execucao: ..." |

> O ramo "login desconhecido" (`None`) existe por segurança: sem saber se está logado, a barra **não** pinta verde. Em uso normal a barra consulta o login (cache 300 s), então o ramo verde/vermelho é o que vale.

---

## Codex (`collect_codex`)

**Entradas:** registro `codex/last_review.json`, processos, raízes (executor **e** laço de revisão), CLI instalada?, login.

Processo "Codex": nome `codex`/`codex.exe`, ou `node` cuja linha de comando casa o *wrapper* do Codex. **Trabalho** = linha de comando com `exec` ou `review` (o app Codex aberto, `app-server`, **não** conta como trabalho).

```mermaid
flowchart TD
    A{"last_review.json<br/>com status failed?"}
    A -- sim --> A1{"erro começa com<br/>RATE_LIMITED?"}
    A1 -- sim --> A2["YELLOW: Limite de uso"]
    A1 -- não --> A3["RED: Falhou"]
    A -- não --> B{"tabela de processos<br/>disponível?"}
    B -- não --> B1["GRAY: Indisponível"]
    B -- sim --> C{"codex filho do executor<br/>ou do laço de revisão?"}
    C -- sim --> C1["BLUE: Revisando PR (vinculado)<br/>se exec ou review, senão<br/>Vinculado ao orquestrador"]
    C -- não --> D{"codex fora do orquestrador<br/>com exec ou review?"}
    D -- sim --> D1["BLUE: Análise manual"]
    D -- não --> E{"login conhecido?"}
    E -- sim --> F{"CLI instalada?"}
    F -- não --> F1["GRAY: Não instalado"]
    F -- sim --> G{"login é False?"}
    G -- sim --> G1["RED: Sem login"]
    G -- não --> G2["GREEN: Conectado"]
    E -- não --> H{"registro com status done?"}
    H -- sim --> H1["YELLOW: Ocioso"]
    H -- não --> H2["GRAY: Não integrado"]
```

| Estado | Nível | Observações |
| --- | --- | --- |
| Limite de uso | âmbar | a conta do revisor atingiu o limite de uso; **volta sozinha** no reset. Não é erro nem desconexão ([ADR 0013](../adr/0013-limite-de-uso-do-revisor-e-atencao-nao-erro.md)) |
| Falhou | vermelho | falha real registrada pelo revisor |
| Revisando PR (vinculado) / Vinculado ao orquestrador / Análise manual | azul | trabalhando (logo animado) |
| **Conectado** | **verde** | logado (ChatGPT). O detalhe inclui "app aberto (pid N)" se houver processo do app, o último parecer (se `done`) e o aviso "revisor automatico NAO operacional (E2E Claude<->Codex nao comprovado)" |
| Sem login / Não instalado | vermelho / cinza | |
| Ocioso / Não integrado | âmbar / cinza | só no ramo "login desconhecido" |

> O aviso "revisor automático NÃO operacional" é **texto fixo** do detalhe: a barra não afirma que o E2E Claude↔Codex está comprovado.

---

## GPT (fixo)

Sempre **cinza "Indisponível"**: "Nenhuma fonte de estado GPT contratada pelo Harness". Não existe fonte de estado para o GPT; a barra **não finge** uma conexão. É o único agente que não tem lógica de coleta.

---

## Listener / Automação (`collect_listener` + sobrescrita pelo laço de revisão)

O chip **Listener** representa a automação da FP-060. Existem dois "motores" possíveis: o **listener** (`event_executor run`) e o **laço de revisão** (`pr_lifecycle run`).

**Modo (`mode`)** é derivado de `event_bridge.json` e do último registro do log:

| bridge.mode | último log `execute` | Resultado |
| --- | --- | --- |
| `execute` | verdadeiro | `EXECUTE` |
| `dry-run` | falso | `DRY_RUN` |
| `execute`/`dry-run` | discordam | `ambiguo` |
| ausente | presente | pelo log |
| presente | ausente | pelo bridge |
| nenhum | nenhum | `indisponivel` |

```mermaid
flowchart TD
    A{"tabela de processos<br/>disponível?"}
    A -- não --> A1["GRAY: Indisponível"]
    A -- sim --> B{"quantos executores<br/>(raízes event_executor run)?"}
    B -- "mais de 1" --> B1["RED: Instâncias concorrentes"]
    B -- "0" --> C{"Tarefa Running<br/>ou launcher vivo?"}
    C -- sim --> C1["RED: Tarefa ativa sem processo"]
    C -- não --> C2["GRAY: Desligado"]
    B -- "1" --> D{"EXECUTOR_DISABLED<br/>existe?"}
    D -- sim --> D1["YELLOW: Kill switch ativo"]
    D -- não --> E{"bridge ilegível ou<br/>log só com linhas ruins?"}
    E -- sim --> E1["YELLOW: Estado ilegível"]
    E -- não --> F{"last_poll válido?"}
    F -- não --> F1["YELLOW: Sem heartbeat"]
    F -- sim --> G{"heartbeat maior<br/>que 600 s?"}
    G -- sim --> G1["RED: Heartbeat velho"]
    G -- não --> H{"modo ambíguo?"}
    H -- sim --> H1["YELLOW: Modo ambíguo"]
    H -- não --> H2["GREEN: DRY_RUN, EXECUTE ou Online"]
    A1 --> L{"laço de revisão<br/>(pr_lifecycle run) vivo?"}
    C2 --> L
    D1 --> L
    E1 --> L
    F1 --> L
    H1 --> L
    L -- sim --> L1["GREEN: Laço de revisão ativo"]
```

**Sobrescrita pelo laço de revisão:** se um `pr_lifecycle run` está vivo e o estado do listener **não** é verde nem vermelho (por exemplo, "Desligado"), o chip passa a **verde "Laço de revisão ativo"**, com o detalhe `pr_lifecycle run (pid N, há Ns); listener FP-057 <estado do listener>`. O motivo: a automação viva da FP-060 é o laço de revisão; o listener antigo fica desligado de propósito, e mostrar "Desligado" enganaria. Vermelho (por exemplo, instâncias concorrentes) **não** é escondido por essa regra.

---

## GitHub (`collect_github`)

Refere-se ao **PR atual do listener** (o mais recente em andamento na fila do `event_bridge.json`), **não** à fila de PRs do painel.

| Condição | Nível | Rótulo |
| --- | --- | --- |
| `gh api user` falha | vermelho | "Sem conexão/auth" |
| sem PR em andamento | verde | "Conectado" (detalhe: "login X; sem PR em andamento") |
| `gh pr view` ilegível | âmbar | "PR ilegível" |
| algum check com falha (`FAILURE`, `CANCELLED`, `TIMED_OUT`, `ACTION_REQUIRED`, `STARTUP_FAILURE`) | vermelho | "Falha em checks" |
| algum check pendente | âmbar | "Checks pendentes" |
| review com `CHANGES_REQUESTED` | âmbar | "Review pede mudanças" |
| PR sem nenhum check | âmbar | "Conectado" (detalhe: "sem checks") |
| tudo ok (`SUCCESS`, `NEUTRAL`, `SKIPPED`) | verde | "Conectado" |

> Detalhe do texto: o resumo conta `ok / pendente(s) / falha(s)`. O caso "PR sem checks" usa o rótulo "Conectado" com nível âmbar: o nível é a informação confiável.

---

## Exemplo real (observado num dia de uso)

| Agente | Nível | Rótulo | Por quê |
| --- | --- | --- | --- |
| Claude | verde | Conectado | `claude auth status` → logado; sessão interativa viva |
| Codex | âmbar | Limite de uso | revisor suspenso até o reset da conta |
| GPT | cinza | Indisponível | sem fonte de estado |
| Listener | verde | Laço de revisão ativo | `pr_lifecycle run` vivo; listener FP-057 desligado |
| GitHub | verde | Conectado | `gh` autenticado; sem PR do listener em andamento |

## Como o painel mostra cada agente

Cada linha: bolinha (ou glifo `✻` de trabalho na cor do agente) + **nome** + rótulo + `pid` (se houver), e embaixo o `detail` em cinza. O tooltip da barra mostra `Nome: rótulo pid N - detalhe` e o nome do nível (`conectado`, `trabalhando`, `atencao`, `erro`, `desconectado / sem integracao`).
