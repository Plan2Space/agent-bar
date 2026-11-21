# "Precisa de você": PRs que esperam a decisão do Felipe

> Fluxo combinado em 2026-11-21: o agente revisor (`fmmodesto`) revisa todos os PRs. Ele aprova LOW/MEDIUM, e o runtime mergeia. Nos HIGH ele só comenta uma recomendação: quem revisa e aprova é o Felipe.

## Onde aparece

- **Topo do painel** (clique na barra), visível também na Central completa: seção amarela "Precisa de você".
- **Na barra:** contador `⚑ N para você` quando há algo esperando. Some com zero. Mostra `⚑ ?` quando a leitura está indisponível. O tooltip lista os PRs.
- **Aviso do Windows** (bandeja) quando um PR entra na lista. Clicar no aviso abre o PR.

## O que entra na lista

PR aberto de qualquer repo da org Plan2Space (sem rascunhos) com risco **HIGH** e estado **AGUARDANDO** no HEAD atual. Risco e estado vêm de `agent_runtime.approval` (`classify_risk` e `merge_decision`), não da barra. AGUARDANDO quer dizer CI verde, evidência ok e nenhuma aprovação válida neste HEAD. Um commit novo muda o HEAD, e o PR volta para a lista.

Para cada PR: `repo#N` (link), título, risco e motivo, HEAD curto, CI, **há quanto tempo espera** (desde o commit do HEAD; data desconhecida aparece como `?`) e a **recomendação do revisor**: o último review dele com `commit_id` igual ao HEAD, ou o último comentário dele que cita o SHA ou é posterior ao commit do HEAD. Sem nada disso, a tela diz "sem recomendação neste HEAD". A ordem é do mais antigo para o mais novo.

Vazio: "nada esperando você". Falha da coleta: **INDISPONÍVEL** com o motivo, nunca uma lista antiga. PRs que não puderam ser lidos são contados à parte.

## Avisos

- Um aviso por **PR@HEAD**: novas coletas não repetem o aviso, mas um commit novo pode avisar de novo. Vários PRs novos de uma vez viram um aviso só.
- Se o aviso falhar (bandeja indisponível), o erro vai para o log (`%LOCALAPPDATA%\agent_bar\logs\agent_bar.log`) e a lista continua funcionando.
- Para desligar: clique direito na barra → *Avisar quando um PR precisar de mim* (guardado em `settings.json`, campo `notify_human`).

## Configuração

| Variável / campo | Padrão | Para quê |
| --- | --- | --- |
| `AGENT_BAR_REVIEWER` | `fmmodesto` | login do agente revisor cuja recomendação aparece na lista |
| `notify_human` (`settings.json`) | `true` | aviso do Windows quando um PR passa a precisar do Felipe |

## Aprovadores por nível (agent-runtime `0de08b9`)

Desde o agent-runtime#64, `from_github_review` só aceita review `APPROVED` de um login configurado **para o nível do PR**, sem herança entre níveis, e ignora o autor do PR. A barra passa o mapa por nível (`AGENT_BAR_APPROVERS_MEDIUM` / `AGENT_BAR_APPROVERS_HIGH`, ver [aprovacao-humana.md](aprovacao-humana.md)). A conta do revisor (`AGENT_BAR_REVIEWER`) é retirada da lista HIGH mesmo se configurada lá. Resultado: um APPROVED da `fmmodesto` num HIGH **não** tira o PR desta lista, nem um comentário `APROVADO HIGH <sha>` dela. A falha descrita antes (qualquer usuário humano aprovava) está corrigida no runtime.
