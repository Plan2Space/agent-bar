"""Leitura somente leitura do EventStore (agent-bar).

O schema usado aqui e um subconjunto do contrato do agent-runtime (tabelas `events`, `transitions` e `outbox`).
Os bancos de teste sao criados com SQL direto para que o agent-bar nao dependa do agent-runtime nem em CI.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from agent_bar import store_reader as sr

NOW = dt.datetime(2026, 11, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
TS = NOW.timestamp()

SCHEMA = """
CREATE TABLE events (
  id INTEGER PRIMARY KEY, event_type TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0, error TEXT, updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE transitions (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, at REAL NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, actor TEXT, detail TEXT
);
CREATE TABLE outbox (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, target TEXT NOT NULL, operation TEXT NOT NULL,
  payload TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'PENDING',
  attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0, error TEXT, updated_at REAL NOT NULL DEFAULT 0
);
"""


def make_db(path, events=(), transitions=(), outbox=()):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for e in events:
        db.execute(
            "INSERT INTO events(id,event_type,idempotency_key,status,attempt,available_at,lease_owner,lease_until,error,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)", e)
    for t in transitions:
        db.execute("INSERT INTO transitions(id,event_id,at,from_status,to_status,actor,detail) VALUES(?,?,?,?,?,?,?)", t)
    for o in outbox:
        db.execute(
            "INSERT INTO outbox(id,event_id,target,operation,payload,idempotency_key,status,attempts,available_at,lease_owner,lease_until,error,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", o)
    db.commit()
    db.close()
    return path


def test_missing_database_is_reported_as_absent_and_nothing_is_created(tmp_path):
    target = tmp_path / "event_store.db"
    out = sr.collect_store(target, NOW)
    assert out["state"] == "ausente"
    assert not target.exists()


def test_counts_backoff_live_leases_and_zombies(tmp_path):
    db = make_db(tmp_path / "s.db", events=[
        (1, "IMPLEMENT_REQUESTED", "k1", "PENDING", 0, TS + 60, None, 0, None, TS),   # em backoff
        (2, "IMPLEMENT_REQUESTED", "k2", "PENDING", 0, TS - 1, None, 0, None, TS),    # elegivel
        (3, "REVIEW_REQUESTED", "k3", "RUNNING", 1, 0, "worker-a", TS + 30, None, TS),  # lease vivo
        (4, "REVIEW_REQUESTED", "k4", "RUNNING", 1, 0, "worker-b", TS - 5, None, TS),   # zumbi
        (5, "PUBLISH_REQUESTED", "k5", "DONE", 1, 0, None, 0, None, TS),
    ])
    out = sr.collect_store(db, NOW)
    assert out["state"] == "ok"
    assert out["counts"] == {"PENDING": 2, "RUNNING": 2, "DONE": 1}
    assert out["backoff"] == 1
    assert out["zombies"] == 1
    assert [lease["key"] for lease in out["live_leases"]] == ["k3"]
    assert out["live_leases"][0]["seconds_left"] == 30


def test_last_error_is_the_latest_failure_by_transition_order_not_by_clock(tmp_path):
    db = make_db(
        tmp_path / "s.db",
        events=[
            (1, "IMPLEMENT_REQUESTED", "k-clock-newer", "PENDING", 1, TS, None, 0, "falha A", TS + 999),
            (2, "IMPLEMENT_REQUESTED", "k-clock-older", "PENDING", 1, TS, None, 0, "falha B", TS),
        ],
        transitions=[
            (1, 2, TS, "RUNNING", "PENDING", "w", "failed: B primeiro na ordem persistida"),
            (2, 1, TS, "RUNNING", "PENDING", "w", "failed: A depois, por id"),
        ],
    )
    out = sr.collect_store(db, NOW)
    # a ordem persistida (id da transicao) decide, nao o relogio (updated_at) de cada evento
    assert out["last_error"]["key"] == "k-clock-newer"


def test_dlq_and_outbox_dlq_mark_unknown_remote_outcomes(tmp_path):
    db = make_db(
        tmp_path / "s.db",
        events=[(1, "PUBLISH_REQUESTED", "k1", "DLQ", 3, 0, None, 0, "limite", TS)],
        outbox=[(1, 1, "github", "post_review", "{}", "o1", "DLQ", 5, TS, None, 0,
                 "lease expired after final attempt; remote outcome requires reconciliation", TS)],
    )
    out = sr.collect_store(db, NOW)
    assert [d["id"] for d in out["dlq"]] == [1]
    assert out["outbox"]["dlq"][0]["unknown_outcome"] is True
    assert out["outbox"]["counts"] == {"DLQ": 1}


def test_credentials_are_redacted_from_keys_owners_and_errors(tmp_path):
    fake_token = "synthetic-secret-value-0001"  # valor sintetico, sem formato de credencial real
    db = make_db(tmp_path / "s.db", events=[
        (1, "IMPLEMENT_REQUESTED", f"key-{fake_token}", "RUNNING", 1, 0, f"owner {fake_token}", TS + 10, f"token={fake_token}", TS),
    ])
    out = sr.collect_store(db, NOW)
    text = repr(out)
    assert fake_token not in text
    assert "<segredo>" in text


def test_reading_does_not_change_the_database_file(tmp_path):
    path = make_db(tmp_path / "s.db", events=[(1, "IMPLEMENT_REQUESTED", "k1", "PENDING", 0, 0, None, 0, None, TS)])
    before = path.read_bytes()
    sr.collect_store(path, NOW)
    assert path.read_bytes() == before


def test_corrupt_file_is_reported_as_unavailable_not_healthy(tmp_path):
    bad = tmp_path / "s.db"
    bad.write_bytes(b"isto nao e um banco sqlite" * 50)
    out = sr.collect_store(bad, NOW)
    assert out["state"] == "indisponivel"
    assert "counts" not in out


def test_database_without_the_contract_tables_is_unavailable(tmp_path):
    empty = tmp_path / "s.db"
    sqlite3.connect(empty).close()
    out = sr.collect_store(empty, NOW)
    assert out["state"] == "indisponivel"
