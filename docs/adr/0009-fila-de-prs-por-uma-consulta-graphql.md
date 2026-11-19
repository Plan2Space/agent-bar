# ADR 0009: Fila de PRs por uma consulta GraphQL e classificação local

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O dono precisava ver **de relance** o estado dos PRs abertos (27 na época) e o que cada um espera: correção, revisão, conflito, CI, ou mergear. Uma consulta por PR (`gh pr view`) multiplicaria chamadas (3 por PR a cada minuto estoura a cota de 5000/h). Uma consulta `gh pr list --json` com revisões e commits de **todos** os PRs estourou o limite de nós do GraphQL.

## Decisão
1. **Uma consulta GraphQL paginada** (50 PRs por página, até 4 páginas), pedindo só o necessário: `number`, `title`, `url`, `isDraft`, `mergeable`, `headRefOid`, `reviews(last:1)` e `commits(last:1)` com o resultado agregado dos checks.
2. **Classificação local e determinística** (`classify_pr`) em seis estados, por ordem de prioridade: `rascunho`, `conflito`, `ci`, `pronto`, `corrigir`, `aguarda`.
3. **`pronto` exige** revisão `APPROVED` **no HEAD atual** e checks `SUCCESS`: aprovação de outro commit e CI ausente ou pendente nunca contam.
4. A barra mostra **fatos e sugestões de ação**, e **nunca** oferece merge ([ADR 0001](0001-somente-observacao-com-uma-unica-acao.md)). O rótulo diz "pronto para **você** mergear".
5. Cache de 60 s, "última fila boa" em falha, e recusa de contagem incompleta ([ADR 0008](0008-ausencia-de-dado-nunca-e-sucesso.md)).
6. Os links só são clicáveis para URLs `https://github.com/`.

## Alternativas consideradas
- **`gh pr view` por PR:** descartada: custo de API e latência.
- **`gh pr list --json ...reviews,commits`:** descartada: excedeu o limite de nós do GraphQL.
- **Usar `reviewDecision` do GitHub:** descartada: depende da configuração de proteção de branch e não distingue o HEAD revisado; a regra local é mais explícita.
- **Considerar todas as revisões:** descartada: complexidade sem ganho; a **última** revisão, no HEAD, é o que decide a próxima ação.

## Consequências
- ✅ Um painel que responde "o que falta em cada PR" com ~1–4 chamadas por minuto.
- ✅ Regras pequenas, previsíveis e testáveis.
- ⚠️ Considera só a última revisão (de qualquer autor) e só o último commit; não verifica revisores obrigatórios nem a proteção de branch: **"pronto" é heurística**.
- ⚠️ Repositório fixo no código hoje ([roadmap](../limitacoes-e-roadmap.md)).

## Evidência
- `test_each_open_pr_is_classified_by_what_has_to_happen_next` (9 casos, inclusive aprovação em commit antigo e CI vermelho)
- `test_a_pr_without_any_check_rollup_is_never_ready_to_merge`
- `test_pr_queue_follows_pagination_and_refuses_an_incomplete_count`
- `test_panel_shows_the_pr_queue_with_links_and_what_needs_the_owner`, `test_pr_links_are_rendered_only_for_github_urls`
- Detalhes em [Fila de PRs](../reference/fila-de-prs.md).
