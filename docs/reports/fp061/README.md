# FP-061 — evidência do incremento isolado

Branch `feat/fp061-agent-bar-2` do sketchup-mcp (worktree isolado), base `origin/develop` em `7ea99dae`.

## Capturas visuais

As imagens foram geradas por `tests/fixtures/fp061_visual.py` com SQLite em diretório temporário e `AGENT_BAR_DEMO=1`. A coleta de processos/rede foi desativada nesse script; não há dados ou mensagens de produção nas capturas.

- [Recolhido](fp061_recolhido_demo.png)
- [Expandido](fp061_expandido_demo.png)
- [Completo](fp061_completo_demo.png)
- [Cartões e terminal completos](fp061_operacoes_demo.png)

O modo completo tem rolagem interna; a captura dos cartões mostra também a cauda do terminal. Fontes Segoe UI e Consolas foram registradas no processo Qt offscreen porque esse plugin não enumerava fontes de sistema neste ambiente.

## Verificação

- `uv run --no-sync -- python -m pytest tests/test_agent_bar.py tests/test_agent_bar_panel.py tests/test_agent_bar_observability.py -q`: 99 testes aprovados após o último ajuste de apresentação.
- `ruff check agent_bar tests/test_agent_bar_observability.py tests/fixtures/fp061_visual.py`: sem problemas após ordenar imports.
- `git diff --check`: sem erros.

Limites: evidência offscreen não substitui validação visual em desktop Windows real. Mensagens, decisões humanas, status GPT e heartbeat individual ficam indisponíveis até existir fonte autoritativa. O Event Store do `develop` usado ainda opera em shadow mode.
