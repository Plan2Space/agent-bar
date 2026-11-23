"""Read-only projection of the agent-runtime EventStore (`EVENT_STORE_DB_PATH`).

This module never constructs EventStore: its constructor creates tables and sets
WAL mode. Missing contracts are reported as unavailable, never inferred.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.store_reader import redact_secrets

# A janela de 8 KiB pode comecar no meio de uma chave PEM: o BEGIN fica de fora e o corpo apareceria cru.
_PEM_TAIL = re.compile(r"\A[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")


@dataclass(frozen=True)
class TimelineEntry:
    at: float
    event_type: str
    job_id: str
    transition: str
    detail: str


@dataclass(frozen=True)
class TaskEntry:
    event_id: int
    event_type: str
    job_id: str
    status: str
    attempt: int
    max_attempts: int
    parent_event_id: int | None
    lease_until: float
    error: str = ""


@dataclass(frozen=True)
class RunEntry:
    agent: str
    pid: int | None
    started_at: float
    ended_at: float | None
    outcome: str | None
    stdout_path: str | None
    stderr_path: str | None


@dataclass
class Observation:
    source: str = "indisponível"  # lido | demonstração | indisponível ("lido" = schema lido do arquivo; NAO prova que e producao)
    reason: str = "Event Store não encontrado"
    db_path: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    timeline: list[TimelineEntry] = field(default_factory=list)
    tasks: list[TaskEntry] = field(default_factory=list)
    runs: list[RunEntry] = field(default_factory=list)
    pipeline: list[TimelineEntry] = field(default_factory=list)
    terminal: list[str] = field(default_factory=list)
    terminal_status: str = "Saída de terminal indisponível"
    outbox_pending: int | None = None
    completed_24h: int | None = None
    failed_24h: int | None = None
    mean_run_seconds_24h: float | None = None
    messages_status: str = "Mensagens entre agentes indisponíveis: sem contrato persistido"
    decisions_status: str = "Decisões humanas indisponíveis: sem contrato persistido"
    schema_version: str | None = None
    last_event_age_s: int | None = None          # idade da ultima transicao; None = banco sem transicoes (nunca um 0 inventado)
    heartbeats_status: str = "Heartbeats individuais indisponíveis: o EventStore só registra lease"


def _db_path(explicit: Path | None) -> Path | None:
    """Only the configured store: no fallback to the legacy orchestrator folder (removed 2026-11-22)."""
    if explicit is not None:
        return explicit
    env = os.environ.get("EVENT_STORE_DB_PATH")
    return Path(env) if env else None


def _safe_tail(raw: str | None, root: Path, max_bytes: int = 8192) -> list[str]:
    """Read only a bounded suffix beneath the selected store's directory."""
    if not raw:
        return []
    try:
        target = Path(raw).resolve(strict=True)
        target.relative_to(root.resolve())
        if not target.is_file():
            return []
        with target.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - max_bytes))
            data = stream.read(max_bytes)
        # redige o bloco inteiro ANTES de quebrar em linhas: uma chave PEM ocupa varias linhas
        text = redact_secrets(_PEM_TAIL.sub("<segredo>", data.decode("utf-8", errors="replace")))
        lines = text.splitlines()
        return (lines[1:] if size > max_bytes else lines)[-30:]
    except (OSError, ValueError):
        return []


def collect_observation(*, db_path: Path | None = None, now: float | None = None) -> Observation:
    """Open an existing SQLite DB with mode=ro; never create a DB or schema."""
    path = _db_path(db_path)
    if path is None:
        return Observation(reason="EVENT_STORE_DB_PATH não configurado")
    result = Observation(db_path=str(path))
    if not path.is_file():
        return result
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"events", "transitions", "subprocess_runs", "outbox", "meta"}
            if not required <= tables:
                result.reason = "Esquema do EventStore incompleto"
                return result
            version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if not version or version[0] != "1":
                result.reason = "Versão do Event Store incompatível"
                return result
            result.schema_version = version[0]
            last = db.execute("SELECT MAX(at) FROM transitions").fetchone()[0]
            if last is not None:
                result.last_event_age_s = max(0, int((now if now is not None else time.time()) - float(last)))
            result.counts = {row["status"]: row["n"] for row in db.execute(
                "SELECT status, COUNT(*) n FROM events GROUP BY status")}
            result.outbox_pending = db.execute(
                "SELECT COUNT(*) FROM outbox WHERE status='PENDING'").fetchone()[0]
            result.timeline = [TimelineEntry(*row[:4], redact_secrets(row[4])) for row in db.execute(
                """SELECT t.at, e.event_type, e.job_id, t.to_status, COALESCE(t.detail, '')
                   FROM transitions t JOIN events e ON e.id=t.event_id
                   ORDER BY t.id DESC LIMIT 60""").fetchall()]
            result.tasks = [TaskEntry(*row[:8], redact_secrets(row[8])) for row in db.execute(
                """SELECT id, event_type, job_id, status, attempt, max_attempts,
                          parent_event_id, lease_until, COALESCE(error, '')
                   FROM events WHERE status IN ('PENDING','RUNNING','DLQ')
                   ORDER BY updated_at DESC, id DESC LIMIT 60""").fetchall()]
            result.runs = [RunEntry(*row) for row in db.execute(
                """SELECT agent,pid,started_at,ended_at,outcome,stdout_path,stderr_path
                   FROM subprocess_runs ORDER BY id DESC LIMIT 12""").fetchall()]
            result.pipeline = [TimelineEntry(*row[:4], redact_secrets(row[4])) for row in db.execute(
                """SELECT created_at,event_type,job_id,status,COALESCE(error, '')
                   FROM events WHERE event_type IN
                   ('PR_OPENED','PR_HEAD_UPDATED','CI_PASSED','CI_FAILED',
                    'REVIEW_REQUESTED','REVIEW_APPROVED','REVIEW_CHANGES_REQUESTED',
                    'PR_MERGED','WORKFLOW_COMPLETED')
                   ORDER BY id DESC LIMIT 20""").fetchall()]
            cutoff = (now if now is not None else time.time()) - 86400
            result.completed_24h = db.execute(
                "SELECT COUNT(*) FROM events WHERE status='DONE' AND updated_at>=?", (cutoff,)).fetchone()[0]
            result.failed_24h = db.execute(
                "SELECT COUNT(*) FROM events WHERE status='DLQ' AND updated_at>=?", (cutoff,)).fetchone()[0]
            result.mean_run_seconds_24h = db.execute(
                """SELECT AVG(ended_at-started_at) FROM subprocess_runs
                   WHERE ended_at IS NOT NULL AND ended_at>=?""", (cutoff,)).fetchone()[0]
        result.source = "demonstração" if os.environ.get("AGENT_BAR_DEMO") == "1" else "lido"
        result.reason = "Fixture isolada" if result.source == "demonstração" else f"arquivo {path.name} lido em modo somente leitura (schema {version[0]})"
        for run in result.runs:
            lines = _safe_tail(run.stdout_path, path.parent)
            errors = _safe_tail(run.stderr_path, path.parent)
            if lines or errors:
                result.terminal = ([f"{run.agent} · pid {run.pid or '?'} · saída observada"]
                                   + lines + (["stderr:"] + errors if errors else []))
                result.terminal_status = "Últimas linhas observadas; atualização por polling"
                break
        return result
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.reason = f"Event Store indisponível: {type(exc).__name__}: {exc}"
        return result
