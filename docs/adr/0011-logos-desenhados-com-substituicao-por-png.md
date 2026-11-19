# ADR 0011: Logos desenhados em código, substituíveis por PNG

- **Status:** Aceito
- **Data:** 2026-11-18

## Contexto
O dono pediu que, quando um agente estivesse em trabalho, a bolinha fosse substituída pelo **logo do próprio agente**, animado (como o Claude Code mostra "cozinhando"). Não temos os arquivos oficiais dos logotipos (e redistribuí-los num repositório público traz questões de marca e licença). Binários também exigiriam registro e manutenção.

## Decisão
1. Cada agente tem um **desenho próprio feito em código** (`QPainter`), sem arquivos binários: estrela de 8 pontas (Claude), balão `>_` com cursor (Codex), roseta de 6 pétalas (GPT), dois arcos (laço de revisão), órbita (GitHub), cada um com a sua cor e animação.
2. Se o dono colocar `<agente>.png` na pasta de ícones (`AGENT_BAR_ICONS` ou `%APPDATA%\agent_bar\icons`), **o PNG substitui o desenho** e pulsa em opacidade. É assim que logos **oficiais** entram, sem passar pelo repositório.
3. A animação roda a ~11 quadros/s (90 ms) **apenas** enquanto algum agente trabalha.
4. Os desenhos são **aproximações próprias**, não os logotipos oficiais (dito na documentação).

## Alternativas consideradas
- **Embutir os logotipos oficiais:** descartada: marca/licença, e binários no repositório.
- **Emojis/glifos Unicode (`✻`...):** descartada para a barra: dependem da fonte do sistema; mantidos só no texto do painel.
- **Spinner genérico para todos:** descartada: o pedido era reconhecer cada agente.

## Consequências
- ✅ Zero binários, zero risco de marca; o dono pode personalizar sem tocar no código.
- ✅ Custo de CPU nulo em repouso.
- ⚠️ Os desenhos não são os logotipos reais.
- ⚠️ O PNG é lido uma vez por execução (reiniciar para trocar) e é escalado sem rotação.

## Evidência
- `test_each_agent_has_its_own_logo_painter_and_the_animation_advances` (renderiza todos os logos em todos os estados)
- `test_idle_agents_show_a_colored_dot_and_working_agents_show_their_animated_logo` (a animação liga e desliga sozinha)
- Especificação visual em [Interface](../reference/interface.md#ícones-e-logos-statusicon).
