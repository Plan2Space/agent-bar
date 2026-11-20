# Contrato de leitura do EventStore (consumidor: agent-bar)

Este documento é o lado do consumidor. A definição do schema é do agent-runtime (`docs/contracts/CONTRACT.md`, schema versão 1). O agent-bar **não importa código do runtime**: lê o banco por SQL, somente leitura (`mode=ro`).

## Versão exigida

- Schema **1**. Qualquer outra versão de banco é tratada como incompatível e não é lida como saudável.

## Colunas lidas

| Tabela | Colunas |
|---|---|
| `events` | id, event_type, idempotency_key, status, attempt, available_at, lease_owner, lease_until, error, updated_at |
| `transitions` | id, event_id, from_status, to_status |
| `outbox` | id, event_id, target, operation, idempotency_key, status, attempts, available_at, lease_until, error |

Qualquer coluna fora desta tabela não é lida. Uma coluna listada aqui que sumir do schema é mudança MAJOR no runtime.

## Estados lidos

- `events.status`: PENDING, RUNNING, DONE, DLQ, SUPERSEDED, RECORDED.
- `transitions`: falha = `from_status='RUNNING'` com `to_status` em PENDING ou DLQ.
- `outbox.status`: PENDING, RUNNING, DONE, DLQ.

## Comportamento em incompatibilidade

| Situação | Estado reportado |
|---|---|
| Arquivo ausente | `ausente` |
| Arquivo não é SQLite, tabela ausente ou coluna ausente | `indisponivel` |
| Leitura OK | `ok` |

Nunca `ok` com dados parciais.
