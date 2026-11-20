# ADR 0013: Limite de uso do revisor é atenção (âmbar), não erro

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O revisor independente (Codex) roda numa conta com **cota de uso**. Ao atingi-la, o laço de revisão registra `failed` com erro `RATE_LIMITED: limite de uso da conta do revisor...` e **suspende** novas revisões até o reset da conta (dias, no caso real observado). Mostrar isso como **vermelho "Falhou"** passava a ideia de quebra técnica (e de que algo precisava ser consertado), quando na verdade nada está quebrado: a automação volta sozinha no reset. Mas esconder o estado seria pior: **nenhum PR é revisado enquanto o limite durar**, e o dono precisa saber.

## Decisão
1. Um registro de falha do Codex cujo erro **começa com `RATE_LIMITED`** vira **âmbar "Limite de uso"**, com detalhe `PR #N <sha>: revisor suspenso até o reset da conta; <erro>`.
2. **Qualquer outra falha** continua **vermelha "Falhou"**.
3. O estado âmbar tem prioridade sobre "conectado": o dono vê, de relance, que o revisor está parado.

## Alternativas consideradas
- **Vermelho (como qualquer falha):** descartada: confunde pausa por cota com defeito.
- **Verde/conectado (a conta está logada):** descartada: esconde que as revisões estão suspensas.
- **Ignorar (nenhum estado):** descartada: viola a regra de [ADR 0008](0008-ausencia-de-dado-nunca-e-sucesso.md).

## Consequências
- ✅ A cor comunica a ação certa: **atenção/esperar** e não **consertar**.
- ✅ Explica por que a fila de PRs não avança.
- ⚠️ A detecção é pelo **prefixo do texto do erro** (contrato informal com o revisor).
- ⚠️ A hora do reset **não** é mostrada hoje (está no log do laço, não no registro lido); ver [roadmap](../limitacoes-e-roadmap.md).

## Evidência
- `test_codex_usage_limit_is_amber_attention_and_a_real_failure_stays_red`
- Regras em [Estados dos agentes](../reference/estados-dos-agentes.md#codex-collect_codex).
