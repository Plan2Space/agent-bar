# Aprovação humana no HEAD exato (S00-14, UI)

> Issue [#10](https://github.com/Plan2Space/agent-bar/issues/10) · principal [agent-runtime#58](https://github.com/Plan2Space/agent-runtime/issues/58).
> Regras: SPEC S00-05 §5 C9 (`Plan2Space/engineering-specs` `plan2space/S00-05_agent_records_and_adapter.md`, main `8ed1543`).
> Código das regras: `agent_runtime.approval` em `Plan2Space/agent-runtime@92acdcdd8cd8dd6a69152c79f5bd304280733235`, fixado no `pyproject.toml`.

## O que a tela mostra

Na Central completa, card **"Aprovação humana no HEAD exato"**: um item por PR aberto da org (sem rascunhos) que não é LOW automático.

| Campo | De onde vem |
| --- | --- |
| PR (link) | busca de PRs abertos da org (a mesma fila do painel) |
| **Risco** e motivos | `classify_risk(arquivos, labels, status+patch)` do runtime. A barra não tem regra própria |
| **HEAD** (SHA completo) | `headRefOid` lido do GitHub |
| **CI** | `statusCheckRollup` do último commit (`success` / `failure` / `pending` / `none`) |
| **Evidência** | `sketchup-mcp-next`: precisa de uma execução do motor **neste HEAD**, PASS e sem problema (card "Execuções do motor"). Outros repos: "não se aplica (sem motor): vale o CI", escrito assim, nunca como verde |
| **Aprovação** | comentários e reviews do PR reconhecidos por `from_github_comment` / `from_github_review`, mais os fatos `GITHUB_REVIEW_SUBMITTED` que o runtime gravou no EventStore (lidos em `mode=ro`) |
| **Estado** | `merge_decision` do runtime: `AGUARDANDO`, `APROVADO no HEAD`, `BLOQUEADO` (CI ou evidência) ou `ERRO de leitura` |

Um commit novo muda o HEAD e a aprovação anterior deixa de valer (R9): o item volta para **AGUARDANDO**. Uma aprovação com risco menor que o do PR não cobre o PR (R9a). Uma aprovação de outro PR não conta (R9b).

## O botão "Aprovar <RISCO> no HEAD <sha>"

1. Só fica habilitado quando o PR está **AGUARDANDO**, o CI está `success`, a evidência não falta, e o login atual do `gh` está em `AGENT_BAR_APPROVERS`. Fora disso fica desabilitado, e o motivo aparece embaixo. No modo `AGENT_BAR_OBSERVE_ONLY=1` ele nunca habilita.
2. Clicar abre uma **confirmação** com o PR, o risco, o SHA completo, o texto exato que será postado e o aviso de credencial (abaixo).
3. Depois do "Sim", a barra **lê de novo** o HEAD e o CI. HEAD diferente: recusa com o motivo, e a tela volta a aguardar o HEAD novo. CI fora de `success`: recusa.
4. Só então posta **um** comentário no PR, pela conta do `gh`, com uma linha exatamente igual a `APROVADO <RISCO> <sha40>` (S00-05 C9, fonte 2).
5. **Nada mais.** A barra não grava no EventStore (R12) e não faz merge (ADR-0001). Quem lê o comentário e decide o merge é o runtime.

Por que comentário e não review: todo PR do rebuild é aberto pela conta `fmodesto30`, e o GitHub não deixa o autor aprovar o próprio PR com review (S00-05 C9).

## Risco aberto: a mesma credencial

Os agentes usam a mesma conta do GitHub (`fmodesto30`) que posta esta aprovação. **O comentário não prova que foi o Felipe quem clicou.** Uma sessão de agente com o `gh` pode postar o mesmo texto. O R16 impede só o runtime de forjar a aprovação, não um agente usando o `gh` diretamente. A separação real exige uma conta própria para os agentes, fora da lista de aprovadores. É decisão do Felipe (S00-05, "Limite conhecido") e não é resolvida aqui. A tela e o diálogo de confirmação mostram este aviso sempre.

## Configuração

| Variável | Para quê |
| --- | --- |
| `AGENT_BAR_APPROVERS` | logins aceitos como aprovadores, separados por vírgula (ex.: `fmodesto30`). Vazio: ninguém aprova pela barra, e a tela diz isso. O runtime ainda não tem uma lista configurada própria; quando tiver, esta variável deve seguir a dele |
| `EVENT_STORE_DB_PATH` | EventStore do runtime, de onde vêm os fatos `GITHUB_REVIEW_SUBMITTED` (somente leitura) |
| `AGENT_BAR_RUNS_DIR` | execuções do motor, usadas como evidência dos PRs do `sketchup-mcp-next` |

## Limites conhecidos (para decidir)

- A SPEC S00-05 §2 diz que ela **não** define o protocolo de comandos da UI para o runtime. Esta tela não manda comando ao runtime: posta no GitHub, o canal auditável da C9. Se o Felipe quiser outro canal, ele precisa de contrato próprio.
- O runtime hoje não grava o risco no EventStore e nenhum processo dele registra as aprovações (`ApprovalStore.record`) ainda. Por isso a barra aplica as funções do runtime direto sobre os dados do GitHub. Quando o runtime passar a gravar os fatos, eles aparecem aqui sem mudança.
- A regra "evidência não se aplica fora do motor" é proposta desta tela, para o Felipe confirmar.
