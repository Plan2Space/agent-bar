"""O leitor só consome o que está no contrato documentado (docs/contracts/READER_CONTRACT.md).

Detecta deriva nos dois sentidos: o leitor passar a ler coluna fora do contrato, e o contrato
listar coluna que o leitor não usa. Também prova que schema incompatível vira `indisponivel`.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from agent_bar import store_reader as sr

REPO = Path(__file__).resolve().parents[1]
CONTRACT = (REPO / "docs" / "contracts" / "READER_CONTRACT.md").read_text(encoding="utf-8")
SOURCE = (REPO / "agent_bar" / "store_reader.py").read_text(encoding="utf-8")


def documented_columns() -> dict[str, set[str]]:
    table_rows = re.findall(r"^\| `(\w+)` \| (.+) \|$", CONTRACT, flags=re.MULTILINE)
    return {table: {c.strip() for c in cols.split(",")} for table, cols in table_rows}


def selected_columns() -> dict[str, set[str]]:
    """Colunas que o SQL do leitor pede, por tabela (leitura estática do código-fonte).

    O SELECT agregado de `transitions` (MAX(id) por evento) não é uma lista simples de colunas:
    o conjunto usado é declarado aqui, e o teste confere que o SQL realmente o usa.
    """
    found: dict[str, set[str]] = {}
    for cols, table in re.findall(r'"SELECT ([^"]+?) FROM (\w+)', SOURCE):
        if "MAX(" in cols:
            continue
        found.setdefault(table, set()).update(c.strip() for c in cols.split(","))
    if "FROM transitions WHERE from_status='RUNNING'" in SOURCE:
        found.setdefault("transitions", set()).update({"event_id", "id", "from_status", "to_status"})
    return found


def test_the_reader_reads_only_documented_columns():
    documented = documented_columns()
    read = selected_columns()
    assert set(read) == set(documented), (sorted(read), sorted(documented))
    for table, columns in read.items():
        extra = columns - documented[table]
        assert not extra, f"{table}: o leitor pede colunas fora do contrato: {sorted(extra)}"


def test_the_contract_does_not_document_columns_the_reader_never_reads():
    documented = documented_columns()
    read = selected_columns()
    for table, columns in documented.items():
        unused = columns - read.get(table, set())
        assert not unused, f"{table}: o contrato lista colunas que o leitor não usa: {sorted(unused)}"


def test_the_contract_declares_schema_version_1():
    assert "Schema **1**" in CONTRACT


@pytest.mark.parametrize("drop", ["lease_until", "idempotency_key"])
def test_schema_drift_in_a_required_column_is_unavailable_not_healthy(tmp_path, drop):
    path = tmp_path / "events.db"
    columns = {
        "id": "INTEGER PRIMARY KEY", "event_type": "TEXT", "idempotency_key": "TEXT", "status": "TEXT",
        "attempt": "INTEGER", "available_at": "REAL", "lease_owner": "TEXT", "lease_until": "REAL",
        "error": "TEXT", "updated_at": "REAL",
    }
    del columns[drop]
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events (" + ", ".join(f"{k} {v}" for k, v in columns.items()) + ")")
    db.execute("CREATE TABLE transitions (id INTEGER PRIMARY KEY, event_id INTEGER, at REAL, from_status TEXT, to_status TEXT, actor TEXT, detail TEXT)")
    db.execute("CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_id INTEGER, target TEXT, operation TEXT, payload TEXT, idempotency_key TEXT, status TEXT, attempts INTEGER, max_attempts INTEGER, available_at REAL, lease_owner TEXT, lease_token TEXT, lease_until REAL, error TEXT, created_at REAL, updated_at REAL)")
    db.commit()
    db.close()
    out = sr.collect_store(path)
    assert out["state"] == "indisponivel"
    assert "counts" not in out
