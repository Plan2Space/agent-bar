# ADR 0010: O laço de revisão é a automação viva exibida no chip Listener

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O chip **Listener** foi desenhado para o listener da FP-057 (`event_executor run`). Na FP-060 a automação que realmente roda é o **laço de revisão** (`pr_lifecycle run`), que enfileira e executa revisões do Codex. Com o listener antigo desligado **de propósito**, a barra mostrava **"Listener: Desligado" (cinza)** e "modo indisponível" mesmo com o laço revisando PRs; e o Codex filho do laço aparecia como "análise manual".

## Decisão
1. A barra reconhece o processo `python`/`pythonw` cuja linha de comando casa `tools.orchestration.pr_lifecycle run` (`review_loop`), escolhendo a **raiz** (o launcher do venv e o filho python casam os dois).
2. Se o laço está vivo e o estado do listener **não é verde nem vermelho**, o chip vira **verde "Laço de revisão ativo"**, com detalhe `pr_lifecycle run (pid N, há Ns); listener FP-057 <estado>`.
3. O **pid do laço entra como raiz de vínculo** do Codex: um `codex exec/review` filho dele é **"Revisando PR (vinculado)"** (trabalhando).
4. **Vermelho nunca é escondido** pela regra (instâncias concorrentes do listener continuam vermelhas).

## Alternativas consideradas
- **Criar um sexto chip "Laço de revisão":** descartada: o dono pediu cinco ícones e a regra "um chip por agente".
- **Manter "Desligado":** descartada: informação enganosa.
- **Reconhecer por Tarefa Agendada:** descartada: o laço roda como processo avulso.

## Consequências
- ✅ O chip reflete a automação real e o Codex aparece vinculado ao trabalho dela.
- ⚠️ Acoplamento ao nome do módulo (`LOOP_RE` fixo): em outro projeto, é preciso configurar ([roadmap](../limitacoes-e-roadmap.md)).
- ⚠️ A barra só vê "o processo está vivo", não "o laço está revisando algo" (o trabalho do Codex é visto pelo processo filho).

## Evidência
- `test_the_review_loop_process_is_found_and_marks_the_automation_green` (inclui o Codex filho como "Revisando PR")
- Regras em [Estados dos agentes](../reference/estados-dos-agentes.md#listener--automação-collect_listener--sobrescrita-pelo-laço-de-revisão).
