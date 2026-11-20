# ADR 0003: Coleta em thread própria, com caches por TTL

- **Status:** Aceito
- **Data:** 2026-11-18 (origem na FP-058)

## Contexto
Coletar o estado envolve operações lentas e variáveis: PowerShell/CIM para a tabela de processos (segundos), `gh` (rede), GraphQL (rede e custo de API), CLIs (`claude auth status`, ~1 s). Fazer isso na thread da interface congelaria a barra; consultar o GitHub a cada 3 s estouraria a cota da API.

## Decisão
1. Toda coleta roda num `Worker` (thread `agent-bar-collector`), que entrega um `Snapshot` imutável por **sinal Qt**. A UI nunca faz I/O.
2. Camadas de frequência diferentes: **local** (processos e arquivos) a cada `local_interval_s` (3 s); **GitHub** (PR atual e fila) com TTL `github_interval_s` (60 s); **backlog** com TTL `backlog_interval_s` (300 s); **login** com TTL de 300 s (30 s se desconhecido).
3. Erro em backlog ou fila **mantém a última leitura boa**, marcada como desatualizada e com o erro visível.
4. Qualquer exceção na coleta é capturada: a coleta continua no ciclo seguinte e **a UI nunca cai**.
5. "Atualizar" invalida os caches de GitHub e acorda a thread imediatamente.
6. Ações lentas (parar/reiniciar listener) rodam em outra thread (`agent-bar-control`).

## Alternativas consideradas
- **`QTimer` na thread de UI chamando as coletas:** descartada: trava a interface durante as chamadas.
- **`QThreadPool`/`asyncio`:** descartada: uma thread única e um `Event` resolvem o problema com menos partes móveis.
- **Sem cache:** descartada: custo de API e de CPU.
- **Cache sem "última leitura boa":** descartada: uma falha passageira esvaziaria a tela e passaria a falsa impressão de "nada".

## Consequências
- ✅ A interface é sempre responsiva; o custo de API é limitado e previsível.
- ✅ Os coletores são funções puras com I/O injetável: fáceis de testar.
- ⚠️ Os dados podem estar até um TTL desatualizados (por isso o rótulo "desatualizado desde ...").
- ⚠️ O cache de login (módulo) não é invalidado por "Atualizar".

## Evidência
- `test_github_cache_avoids_repeated_queries`, `test_snapshot_keeps_the_last_good_backlog_but_flags_the_error`, `test_cli_login_state_is_read_from_the_cli_and_cached`
- `test_button_refresh_confirms_only_when_a_new_snapshot_arrives`
- `test_the_worker_keeps_running_conceptually_closing_the_panel_does_not_touch_collection`
- Detalhes em [Ciclo de coleta](../architecture/03-ciclo-de-coleta.md).
