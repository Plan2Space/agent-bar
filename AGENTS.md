# agent-bar — instruções para Codex CLI

## Conhecimento de engenharia Plan2Space

Consulte `Plan2Space/claude-engineering-skills` na release `v0.9.3`, commit `3f93ccb001d4609527587ddfa1a716ca5a09b38c`. O catálogo autoritativo é `catalog/skills.json`; use `skills/spec-author/SKILL.md` para SPECs, `skills/spec-progress-keeper/SKILL.md` para marcos e `skills/backlog-governance/SKILL.md` para Issues/Board. Registre a skill e o SHA na evidência. A instalação automática por lockfile depende de [hub #29](https://github.com/Plan2Space/claude-engineering-skills/issues/29) e da integração de [agent-bar#4](https://github.com/Plan2Space/agent-bar/issues/4); não declare uma skill carregada sem verificá-la.

O [contrato de ownership](https://github.com/Plan2Space/claude-engineering-skills/blob/v0.9.3/contracts/ownership.v1.md) mantém skills reutilizáveis e contratos compartilhados no hub. SPECs, testes e interface operacional de `agent-bar` ficam aqui; o schema do EventStore pertence a `agent-runtime`. Cite referências externas por commit imutável, sem copiar decisões normativas. Issues e [Project #1](https://github.com/orgs/Plan2Space/projects/1) guardam o estado operacional.

Próximo passo: expor a versão efetiva da skill e falhas de carga na UI, em modo somente leitura, após [agent-runtime#50](https://github.com/Plan2Space/agent-runtime/issues/50). Não altere permissões, proteção de branch ou políticas de aprovação sem autorização.
