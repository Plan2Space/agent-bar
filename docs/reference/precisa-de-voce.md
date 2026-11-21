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

## Limite conhecido (do runtime, não desta tela)

`agent_runtime.approval.from_github_review` aceita como aprovação **qualquer review `APPROVED` de usuário humano**: `user.type = User`, sem conferir a lista de aprovadores. A conta do revisor `fmmodesto` é `User`. Se ele der APPROVED num PR HIGH, o runtime trata como aprovação humana, e o PR sai desta lista. A correção é no runtime (filtrar reviews pela lista de aprovadores) e foi reportada ao coordenador.
