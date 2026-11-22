# Registros de decisão de arquitetura (ADRs)

Cada ADR explica **uma** decisão: o contexto, o que foi decidido, o que foi descartado e as consequências. Eles existem para que ninguém precise adivinhar **por que** o Agent Bar é assim (e para que mudar de ideia seja uma decisão consciente, não um acidente).

Status possíveis: **Aceito** (vale hoje), **Substituído** (há um ADR mais novo) e **Proposto**.

| ADR | Decisão | Status |
| --- | --- | --- |
| [0001](0001-somente-observacao-com-uma-unica-acao.md) | Observar, não orquestrar: só observação, com uma única ação viva (o passo) | Aceito |
| [0002](0002-cor-igual-conexao-logo-igual-trabalho.md) | Cor da bolinha = conexão; logo animado = trabalhando | Aceito |
| [0003](0003-coleta-fora-da-thread-de-ui-com-cache-ttl.md) | Coleta em thread própria, com caches por TTL | Aceito |
| [0004](0004-eventstore-somente-leitura-sem-criar-nada.md) | EventStore aberto só para leitura, sem criar nada | Aceito |
| [0005](0005-passo-sem-tela-com-terminal-embutido-somente-leitura.md) | Passo do Claude sem tela, com terminal embutido somente leitura | Aceito — **substituída em 2026-11-22** |
| [0006](0006-barra-sobre-a-barra-de-tarefas.md) | A barra pode ficar sobre a barra de tarefas (monitor inteiro + topo reafirmado) | Aceito |
| [0007](0007-login-via-status-do-cli-com-cache.md) | Login do Claude/Codex lido do `status` do CLI, em cache | Aceito |
| [0008](0008-ausencia-de-dado-nunca-e-sucesso.md) | Ausência de dado nunca é sucesso nem zero | Aceito |
| [0009](0009-fila-de-prs-por-uma-consulta-graphql.md) | Fila de PRs por uma consulta GraphQL e classificação local | Aceito |
| [0010](0010-laco-de-revisao-como-automacao-viva.md) | O laço de revisão é a automação viva exibida no chip Listener | Aceito — **substituída em 2026-11-22** |
| [0011](0011-logos-desenhados-com-substituicao-por-png.md) | Logos desenhados em código, substituíveis por PNG | Aceito |
| [0012](0012-pacote-independente-extraido-do-sketchup-mcp.md) | Pacote independente extraído do `sketchup-mcp` | Aceito |
| [0013](0013-limite-de-uso-do-revisor-e-atencao-nao-erro.md) | Limite de uso do revisor é atenção (âmbar), não erro | Aceito — **substituída em 2026-11-22** |

## Modelo para novos ADRs

```markdown
# ADR NNNN: <decisão em uma frase>

- **Status:** Proposto | Aceito | Substituído por NNNN
- **Data:** AAAA-MM-DD

## Contexto
O problema, as forças em jogo e o que aconteceu que motivou a decisão.

## Decisão
O que decidimos, em frases afirmativas.

## Alternativas consideradas
O que foi descartado e por quê.

## Consequências
O que fica melhor, o que fica pior, o que passa a ser obrigação.

## Evidência
Código e testes que sustentam a decisão.
```

Convenções: numeração sequencial sem reaproveitar números; um ADR substituído **não** é apagado (ganha "Substituído por NNNN").
