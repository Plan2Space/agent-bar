# ADR 0001: Observar, não orquestrar: só observação, com uma única ação viva

- **Status:** Aceito
- **Data:** 2026-11-18 (registro; a regra vem da FP-058 e foi reafirmada na FP-061)

## Contexto
A orquestração (listener, executor, laço de revisão, EventStore) já tem os seus próprios mecanismos de fila, lease e retry. Uma barra de status é tentadora de transformar num "painel de comando": botão para executar, aprovar, mergear. O histórico do projeto mostrou o custo disso: **mecanismos paralelos de fila/lease/claim** causam divergência e corrida, e uma interface com poder de execução vira um segundo ponto de falha e de risco.

## Decisão
1. O Agent Bar **observa**: lê estado já gravado por outros componentes e o apresenta. Não é um segundo orquestrador.
2. A barra **não faz** merge, push, commit, criar/fechar PR, alterar proteção de branch nem usa `--execute`.
3. Existe **uma** ação "viva", pedida explicitamente pelo dono: rodar **um passo** do Claude (botão Dar andamento / ▶), sempre com **confirmação** e com **botão de parada** ([ADR 0005](0005-passo-sem-tela-com-terminal-embutido-somente-leitura.md)).
4. Parar/reiniciar o listener existe, mas **só em DRY_RUN**, pela Tarefa Agendada, com **preflight fail-closed**.
5. Há um **modo somente leitura** (`AGENT_BAR_OBSERVE_ONLY=1`) que desativa todos os controles.

## Alternativas consideradas
- **Barra com botão de merge/aprovação:** descartada. Concentra poder numa janela de status; a decisão de merge é do dono, no GitHub, com a proteção de branch.
- **Barra que executa o executor (`--execute`):** descartada. Cria um segundo caminho de execução fora da orquestração.
- **Barra 100% passiva (sem nenhuma ação):** descartada pelo dono: o valor da barra cresce quando ela ajuda a destravar os PRs; por isso a única exceção, bem delimitada.

## Consequências
- ✅ Superfície de risco pequena e auditável (poucas linhas fazem algo além de ler).
- ✅ Fácil de provar por teste: o código **não pode conter** `git push`, `git commit`, `taskkill /IM`, `Stop-Process`, `gh pr merge`, `--execute`.
- ⚠️ A barra depende de os outros componentes gravarem estado confiável.
- ⚠️ A ação viva herda os riscos do que o passo pode fazer (ver [Segurança](../seguranca.md)).

## Evidência
- `test_no_module_of_the_bar_can_commit_push_merge_or_kill_by_image_name`
- `test_the_only_claude_launch_is_the_confirmed_progress_step_and_it_is_stopped_by_pid_only`
- `test_stop_and_restart_share_one_fail_closed_preflight`, `test_both_controls_require_confirmation_and_touch_nothing_without_it`
- `test_observe_only_guards_legacy_control_actions`
