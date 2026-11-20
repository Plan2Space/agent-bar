# ADR 0004: EventStore aberto só para leitura, sem criar nada

- **Status:** Aceito
- **Data:** 2026-11-18 (FP-061)

## Contexto
O EventStore da orquestração é um SQLite em modo WAL, escrito por outros processos. A classe `EventStore` do projeto de origem **cria tabelas e liga o WAL no construtor**. Se a barra a instanciasse para "olhar", poderia criar um banco vazio onde ele não existe (escondendo o problema) ou interferir no banco de produção.

## Decisão
1. A barra **nunca** instancia `EventStore` nem `store_factory.get_store()`.
2. Abre o arquivo por conta própria com URI `file:<caminho>?mode=ro`, `timeout=0.25 s`, e executa `PRAGMA query_only=ON`; fecha a conexão ao fim de cada coleta.
3. Valida o contrato antes de ler: as cinco tabelas esperadas (`events`, `transitions`, `subprocess_runs`, `outbox`, `meta`) e `meta.schema_version == "1"`. Qualquer divergência vira "indisponível" com o motivo; **não** interpreta versões desconhecidas.
4. Banco ausente, erro ou esquema incompatível: **nada é criado, reparado ou migrado**.
5. A leitura de logs de saída é **limitada** (8 KiB, 30 linhas) e **contida** na pasta do banco.

## Alternativas consideradas
- **Reutilizar `EventStore(path)`:** descartada pelo efeito colateral de criar o esquema/WAL.
- **Abrir em leitura/escrita e "só não escrever":** descartada: confia em disciplina; `mode=ro` + `query_only` é imposto pelo SQLite.
- **Copiar o banco para ler a cópia:** descartada: custo e risco de ler uma cópia inconsistente no meio de uma escrita.

## Consequências
- ✅ Impossível a barra corromper ou criar estado de produção.
- ✅ Banco ausente é visível ("Event Store não encontrado"), não mascarado.
- ⚠️ Em modo WAL, um leitor `mode=ro` ainda depende dos arquivos `-wal`/`-shm` existirem; se o banco estiver em local somente leitura sem eles, a leitura pode falhar (e vira "indisponível" com o erro).
- ⚠️ Acoplamento ao esquema versão 1: uma mudança de esquema na origem exige atualizar a barra.

## Evidência
- `test_read_only_projection_uses_existing_store_and_bounded_log` (confere que o banco não foi modificado)
- `test_missing_and_incompatible_store_never_create_or_claim_healthy`
- `test_terminal_path_outside_fixture_root_is_rejected`
- Detalhes em [Observabilidade do EventStore](../reference/observabilidade-eventstore.md).
