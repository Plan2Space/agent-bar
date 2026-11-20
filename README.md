# agent-bar

> **Política de SPECs e skills:** as SPECs e skills do ecossistema Plan2Space são mantidas exclusivamente em
> `Plan2Space/claude-engineering-skills`. Nenhuma SPEC nova é criada neste repositório. Ver `CLAUDE.md`.

Painel operacional somente leitura do agent-runtime. Mostra estado; não é fonte autoritativa.

## Escopo atual
- `src/agent_bar/store_reader.py`: leitura do EventStore em modo `mode=ro`, com redação de credenciais. Portado do sketchup-mcp (PR #663, FP-060 MT-09).
- Não inclui ainda: resolução do caminho do banco por variável de ambiente, janela de UI, controles (pause, resume, kill switch).

## Fronteiras
- Não importa código do agent-runtime. Lê o contrato do schema (`events`, `transitions`, `outbox`).
- Não cria, não altera e não trava o banco.
- Estado ausente ou ilegível aparece como `ausente` ou `indisponivel`, nunca como saudável.

## Desenvolvimento
```powershell
python -m pip install -e ".[dev]"
python -m pytest
```
