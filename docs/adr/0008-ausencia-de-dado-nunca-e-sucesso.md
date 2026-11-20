# ADR 0008: Ausência de dado nunca é sucesso nem zero

- **Status:** Aceito
- **Data:** 2026-11-18 (origem na FP-058)

## Contexto
Um painel de status que mostra verde ou "0" quando **não conseguiu ler** é pior do que nenhum painel: passa confiança falsa. Casos reais que motivaram a regra: `event_bridge.json` ilegível, tabela de processos indisponível, banco de eventos ausente, API do GitHub com limite de taxa, fila de PRs vazia por falha (não por falta de PRs).

## Decisão
1. **Sem dado = cinza ou "indisponível"**, com o **motivo técnico específico** (nunca um "indisponível" genérico quando o motivo é conhecido).
2. **Nunca** inventa número: backlog/fila com falha mostram o **erro**; uma lista vazia **não** significa "sem itens" (`PrQueue.error`).
3. **Contagem incompleta é recusada** (por exemplo, mais de 1000 itens no Project ou 200 PRs): melhor um erro do que um total errado.
4. Quando existe uma **última leitura boa**, ela é exibida **marcada como desatualizada**, junto com o erro.
5. Canais que **não existem** (mensagens entre agentes, decisões humanas, heartbeat por worker, estado do GPT) dizem isso em texto fixo; **eventos e logs não são apresentados como mensagens**.
6. Dados sintéticos são **rotulados DEMONSTRAÇÃO**, nunca confundíveis com reais.
7. Estado ambíguo (bridge e log discordam, instâncias concorrentes) é **amarelo/vermelho**, nunca verde.

## Alternativas consideradas
- **Mostrar o último valor conhecido sem avisar:** descartada: esconde a falha.
- **Zero/vazio como padrão:** descartada: indistinguível de "tudo certo".
- **Esconder o componente quando não há dado:** descartada: some a informação de que algo está faltando.

## Consequências
- ✅ O que está verde está verde por **evidência**; o que falta é visível.
- ✅ Diagnóstico mais rápido: o erro técnico já está na tela.
- ⚠️ A interface mostra mais "indisponível" do que um painel otimista mostraria (é intencional).
- ⚠️ Mensagens de erro técnicas exigem escapar HTML e limitar tamanho.

## Evidência
- `test_corrupted_state_or_log_is_never_green`, `test_process_table_unavailable_is_gray`
- `test_pr_queue_failure_is_an_explicit_error_never_an_empty_list`, `test_pr_queue_follows_pagination_and_refuses_an_incomplete_count`
- `test_backlog_refuses_an_incomplete_count_instead_of_guessing`, `test_backlog_error_is_specific_never_a_bare_indisponivel`
- `test_missing_and_incompatible_store_never_create_or_claim_healthy`, `test_demo_is_explicit_and_cannot_be_mistaken_for_real`
- `test_panel_shows_the_queue_error_instead_of_a_zero`
