"""Read-only FP-061 projection of the FP-060 Event Store.

This module never constructs EventStore: its constructor creates tables and sets
WAL mode. Missing contracts are reported as unavailable, never inferred.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path


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
    heartbeats_status: str = "Heartbeats individuais indisponíveis: apenas lease e heartbeat do listener"


def _db_path(gov: Path, explicit: Path | None) -> Path:
    return explicit or Path(os.environ.get("EVENT_STORE_DB_PATH") or gov / "event_store.db")


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
        lines = data.decode("utf-8", errors="replace").splitlines()
        return (lines[1:] if size > max_bytes else lines)[-30:]
    except (OSError, ValueError):
        return []


def _coordinator_line(line: str) -> str:
    """A bounded, factual summary of one JSON line from the active coordinator."""
    try:
        row = json.loads(line)
        if not isinstance(row, dict):
            return line[:240]
        at = str(row.get("at") or "")[:19]
        if isinstance(row.get("review"), dict):
            review = row["review"]
            return f"{at} review {review.get('action', '?')} · PR #{review.get('pr', '?')} · {review.get('verdict', '')}"[:240]
        if isinstance(row.get("prepare"), list):
            prepared = row["prepare"]
            human = sum(isinstance(p, dict) and p.get("code") == "HUMAN" for p in prepared)
            blocked = sum(isinstance(p, dict) and p.get("code") == "BLOCKED_DEPENDENCY" for p in prepared)
            enqueued = row.get("enqueue")
            count = len(enqueued) if isinstance(enqueued, list) else "?"
            return f"{at} preparo {len(prepared)} PRs · decisão humana {human} · dependência {blocked} · enfileirados {count}"
        return f"{at} {str(row)[:200]}"[:240]
    except (ValueError, TypeError):
        return line[:240]


def collect_observation(gov: Path, *, db_path: Path | None = None, now: float | None = None) -> Observation:
    """Open an existing SQLite DB with mode=ro; never create a DB or schema."""
    path = _db_path(gov, db_path)
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
                result.reason = "Esquema Event Store FP-060 incompleto"
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
            result.timeline = [TimelineEntry(*row) for row in db.execute(
                """SELECT t.at, e.event_type, e.job_id, t.to_status, COALESCE(t.detail, '')
                   FROM transitions t JOIN events e ON e.id=t.event_id
                   ORDER BY t.id DESC LIMIT 60""").fetchall()]
            result.tasks = [TaskEntry(*row) for row in db.execute(
                """SELECT id, event_type, job_id, status, attempt, max_attempts,
                          parent_event_id, lease_until, COALESCE(error, '')
                   FROM events WHERE status IN ('PENDING','RUNNING','DLQ')
                   ORDER BY updated_at DESC, id DESC LIMIT 60""").fetchall()]
            result.runs = [RunEntry(*row) for row in db.execute(
                """SELECT agent,pid,started_at,ended_at,outcome,stdout_path,stderr_path
                   FROM subprocess_runs ORDER BY id DESC LIMIT 12""").fetchall()]
            result.pipeline = [TimelineEntry(*row) for row in db.execute(
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
        if not result.terminal:
            coordinator_log = os.environ.get("AGENT_BAR_HARNESS_LOG")
            if coordinator_log:
                lines = _safe_tail(coordinator_log, path.parent.parent, max_bytes=65536)
                if lines:
                    result.terminal = ["Log do coordenador · resumo das linhas JSON observadas"]
                    result.terminal += [_coordinator_line(line) for line in lines[-8:]]
                    result.terminal_status = "Log do coordenador (não é stdout de subprocesso); polling"
        return result
    except (OSError, sqlite3.Error, ValueError) as exc:
        result.reason = f"Event Store indisponível: {type(exc).__name__}: {exc}"
        return result
