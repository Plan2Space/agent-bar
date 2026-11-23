"""permission_inbox.py — Permission Inbox persistente (SPEC agent-bar/P2S-STUDIO-WORKSPACE-UX.md, F4).

Todo pedido de permissao de agente/ferramenta vira uma linha em SQLite (`permissions.sqlite` no diretorio de dados do
cockpit) ANTES de qualquer decisao, com: id, provedor, sessao, ferramenta, operacao, recurso, motivo, risco, politica,
validade, decisao humana (ou de politica), quem decidiu, quando e o resultado da execucao. Nada some: pedido que nao
foi respondido termina EXPIRED/CANCELLED com o motivo, nunca e apagado.

Politicas distinguidas (`policy`): NORMAL (pergunta), PREVIOUSLY_GRANTED (regra persistente que o Felipe criou),
AUTO_MODE (o fluxo com auto_approve escolheu allow_once), BYPASS_MODE (o agente estava num modo que decide sozinho),
IRREVERSIBLE (sempre pergunta; nenhuma regra nem modo aprova sozinho) e FORBIDDEN (recusado por politica, sem pergunta).

Decisao e idempotente: decidir de novo com a mesma escolha nao muda nada; com outra escolha, `AlreadyDecided`.
Pedido repetido (mesma impressao digital na mesma sessao, ou em sessao anterior) recebe `repeat_of` e uma explicacao:
"permitir uma vez" nao vale para o proximo pedido; sessao reiniciada perde concessoes de sessao; etc.

Segredo nao entra no banco: a operacao gravada e truncada e passa pela redacao (tokens, chaves, senhas).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bar.cockpit.devmode import irreversible_request, permission_text

SCHEMA_VERSION = 1
DEFAULT_TTL_S = 15 * 60
OP_LIMIT = 600
POLICIES = ("NORMAL", "PREVIOUSLY_GRANTED", "AUTO_MODE", "BYPASS_MODE", "IRREVERSIBLE", "FORBIDDEN")
STATUSES = ("PENDING", "ALLOWED", "DENIED", "EXPIRED", "CANCELLED")
RISKS = ("LOW", "MEDIUM", "HIGH", "IRREVERSIBLE")
KIND_RISK = {"read": "LOW", "search": "LOW", "fetch": "MEDIUM", "think": "LOW", "edit": "MEDIUM", "move": "MEDIUM",
             "delete": "HIGH", "execute": "HIGH", "other": "MEDIUM"}
FORBIDDEN = (                                                                  # recusado sem perguntar, por politica
    re.compile(r"\bgit\s+push\b[^\n]*\s(--force|-f)\b", re.I),
    re.compile(r"\bgh\s+pr\s+merge\b[^\n]*--admin\b", re.I),
    re.compile(r"\bgh\s+api\b[^\n]*branches/[^\s]+/protection", re.I),
)
SECRET = re.compile(r"(?i)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|"
                    r"(?:token|password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+)")


class AlreadyDecided(Exception):
    """O pedido ja tem outra decisao; decisoes nao sao reescritas."""


@dataclass(frozen=True)
class Entry:
    id: str
    created_at: float
    expires_at: float
    provider: str
    session_id: str
    tool_call_id: str
    title: str
    kind: str
    operation: str
    resource: str
    reason: str
    risk: str
    policy: str
    status: str
    decision: str | None
    decided_by: str | None
    decided_at: float | None
    result: str | None
    fingerprint: str
    repeat_of: str | None
    repeat_note: str | None
    workspace: str

    @property
    def pending(self) -> bool:
        return self.status == "PENDING"


def redact(text: str) -> str:
    """Troca segredo por «redigido»; em `chave=valor` mantem o nome da chave."""
    def sub(m: re.Match) -> str:
        key = re.match(r"(?i)(token|password|passwd|secret|api[_-]?key)", m.group(0))
        return f"{key.group(0)}=«redigido»" if key else "«redigido»"
    return SECRET.sub(sub, text or "")


def describe(tool_call: dict) -> tuple[str, str, str]:
    """(operacao, recurso, motivo) legiveis do tool_call ACP, ja redigidos e truncados."""
    raw = tool_call.get("rawInput")
    if isinstance(raw, dict):
        op = raw.get("command") or raw.get("cmd") or json.dumps(raw, ensure_ascii=False)
    else:
        op = str(raw or tool_call.get("title", ""))
    locs = [str(loc.get("path")) for loc in tool_call.get("locations") or [] if isinstance(loc, dict) and loc.get("path")]
    if not locs and isinstance(raw, dict):
        locs = [str(raw[k]) for k in ("file_path", "path", "notebook_path", "url") if raw.get(k)]
    reason = str((raw or {}).get("description", "")) if isinstance(raw, dict) else ""
    return redact(str(op))[:OP_LIMIT], redact(", ".join(locs))[:OP_LIMIT], redact(reason)[:OP_LIMIT]


def fingerprint(tool_call: dict) -> str:
    """Impressao digital do QUE se pede (tipo + titulo + rawInput), sem a sessao: serve para achar repeticoes."""
    raw = json.dumps(tool_call.get("rawInput", ""), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{tool_call.get('kind', '')}\n{tool_call.get('title', '')}\n{raw}".encode()).hexdigest()[:24]


def classify(tool_call: dict, mode_decides_alone: bool = False) -> tuple[str, str]:
    """(risco, politica) de um pedido, sem olhar regras persistentes."""
    text = permission_text(tool_call)
    if any(p.search(text) for p in FORBIDDEN):
        return "IRREVERSIBLE", "FORBIDDEN"
    if irreversible_request(tool_call):
        return "IRREVERSIBLE", "IRREVERSIBLE"
    risk = KIND_RISK.get(str(tool_call.get("kind") or "other"), "MEDIUM")
    return risk, ("BYPASS_MODE" if mode_decides_alone else "NORMAL")


class PermissionInbox:
    def __init__(self, path: Path | str = ":memory:", clock: Callable[[], float] = time.time, ttl_s: float = DEFAULT_TTL_S):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.ttl_s = ttl_s
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._migrate()

    # ------------------------------------------------------------------------------------------------ schema
    def _migrate(self) -> None:
        with self._lock:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    provider TEXT NOT NULL, session_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
                    title TEXT NOT NULL, kind TEXT NOT NULL, operation TEXT NOT NULL, resource TEXT NOT NULL,
                    reason TEXT NOT NULL, risk TEXT NOT NULL, policy TEXT NOT NULL, status TEXT NOT NULL,
                    decision TEXT, decided_by TEXT, decided_at REAL, result TEXT,
                    fingerprint TEXT NOT NULL, repeat_of TEXT, repeat_note TEXT, workspace TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS requests_status ON requests(status, created_at);
                CREATE INDEX IF NOT EXISTS requests_fp ON requests(fingerprint, created_at);
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY, created_at REAL NOT NULL, kind TEXT NOT NULL, title_prefix TEXT NOT NULL,
                    workspace TEXT NOT NULL, created_by TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
            """)
            self._db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    # ------------------------------------------------------------------------------------------------ registro
    def record(self, tool_call: dict, *, provider: str, session_id: str, workspace: str = "",
               mode_decides_alone: bool = False) -> Entry:
        """Grava o pedido (PENDING) antes de qualquer decisao. Regra persistente aplicavel decide na hora
        (PREVIOUSLY_GRANTED), exceto para irreversivel; FORBIDDEN e negado na hora."""
        tc = tool_call or {}
        risk, policy = classify(tc, mode_decides_alone)
        op, resource, reason = describe(tc)
        fp = fingerprint(tc)
        now = self.clock()
        rid = uuid.uuid4().hex
        repeat_of, note = self._repeat(fp, session_id)
        rule = self._rule_for(tc, workspace) if policy in ("NORMAL", "BYPASS_MODE") else None
        if rule is not None:
            policy = "PREVIOUSLY_GRANTED"
        with self._lock:
            self._db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, now, now + self.ttl_s, provider, session_id, str(tc.get("toolCallId", "")),
                 redact(str(tc.get("title", "")))[:OP_LIMIT], str(tc.get("kind") or "other"), op, resource, reason,
                 risk, policy, "PENDING", None, None, None, None, fp, repeat_of, note, workspace))
        if policy == "FORBIDDEN":
            self.decide(rid, "deny", by="policy")
        elif rule is not None:
            self.decide(rid, "allow_always", by=f"rule:{rule}")
        return self.get(rid)

    def _repeat(self, fp: str, session_id: str) -> tuple[str | None, str | None]:
        row = self._db.execute("SELECT * FROM requests WHERE fingerprint=? ORDER BY created_at DESC LIMIT 1", (fp,)).fetchone()
        if row is None:
            return None, None
        when = time.strftime("%H:%M", time.localtime(row["created_at"]))
        if row["session_id"] != session_id:
            note = (f"o mesmo pedido apareceu às {when} em outra sessão ({row['session_id'][:12]}): concessões de uma "
                    "sessão não passam para a seguinte (sessão reiniciada ou nova)")
        elif row["decision"] == "allow_once":
            note = f"o mesmo pedido foi permitido UMA vez às {when}: 'permitir uma vez' não vale para o próximo"
        elif row["decision"] in ("deny", "reject_once", "reject_always"):
            note = f"o mesmo pedido foi negado às {when} e o agente pediu de novo"
        elif row["status"] in ("EXPIRED", "CANCELLED"):
            note = f"o pedido anterior (às {when}) terminou {row['status']} sem decisão"
        else:
            note = f"pedido repetido (anterior às {when}, {row['status']})"
        return row["id"], note

    # ------------------------------------------------------------------------------------------------ decisao
    def decide(self, rid: str, decision: str | None, by: str = "human") -> Entry:
        """`decision` = optionId/kind escolhido (allow_once, allow_always, deny...) ou None (recusado/fechado).
        Idempotente; nao reescreve uma decisao diferente."""
        status = "DENIED" if decision is None or str(decision).startswith(("deny", "reject")) else "ALLOWED"
        dec = decision or "deny"
        with self._lock:
            row = self._row(rid)
            if row["status"] != "PENDING":
                if row["decision"] == dec and row["status"] == status:
                    return self._entry(row)                                  # mesma decisao: nada muda
                raise AlreadyDecided(f"{rid}: ja {row['status']} ({row['decision']})")
            if row["policy"] == "IRREVERSIBLE" and status == "ALLOWED" and by != "human":
                raise PermissionError("irreversivel so com decisao humana")
            self._db.execute("UPDATE requests SET status=?, decision=?, decided_by=?, decided_at=? WHERE id=?",
                             (status, dec, by, self.clock(), rid))
            return self.get(rid)

    def finish(self, rid: str, result: str) -> None:
        """Resultado da execucao (status final do tool_call: completed/failed/cancelled)."""
        with self._lock:
            self._db.execute("UPDATE requests SET result=? WHERE id=?", (str(result)[:80], rid))

    def finish_tool_call(self, session_id: str, tool_call_id: str, result: str) -> None:
        with self._lock:
            self._db.execute("UPDATE requests SET result=? WHERE session_id=? AND tool_call_id=? AND result IS NULL",
                             (str(result)[:80], session_id, tool_call_id))

    def cancel_open(self, why: str, session_id: str | None = None) -> int:
        """Pedidos ainda PENDING viram CANCELLED com o motivo (agente encerrou, Studio reiniciou...). Nunca apaga."""
        with self._lock:
            q = "UPDATE requests SET status='CANCELLED', result=?, decided_at=? WHERE status='PENDING'"
            args: list[Any] = [why[:80], self.clock()]
            if session_id is not None:
                q += " AND session_id=?"
                args.append(session_id)
            return self._db.execute(q, args).rowcount

    def expire_due(self) -> int:
        with self._lock:
            return self._db.execute(
                "UPDATE requests SET status='EXPIRED', result='prazo vencido', decided_at=? "
                "WHERE status='PENDING' AND expires_at<=?", (self.clock(), self.clock())).rowcount

    # ------------------------------------------------------------------------------------------------ regras
    def add_rule(self, kind: str, title_prefix: str, workspace: str, created_by: str = "human") -> str:
        """Regra persistente (so por decisao humana explicita). Nunca cobre irreversivel nem FORBIDDEN."""
        if created_by != "human":
            raise PermissionError("regra persistente so por decisao humana")
        rid = uuid.uuid4().hex
        with self._lock:
            self._db.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,1)", (rid, self.clock(), kind, title_prefix, workspace,
                                                                          created_by))
        return rid

    def rules(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM rules WHERE active=1 ORDER BY created_at")]

    def disable_rule(self, rule_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE rules SET active=0 WHERE id=?", (rule_id,))

    def _rule_for(self, tc: dict, workspace: str) -> str | None:
        title = str(tc.get("title", ""))
        for r in self.rules():
            if r["kind"] == str(tc.get("kind") or "other") and r["workspace"] == workspace and title.startswith(r["title_prefix"]):
                return r["id"]
        return None

    # ------------------------------------------------------------------------------------------------ consulta
    def _row(self, rid: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return row

    @staticmethod
    def _entry(row: sqlite3.Row) -> Entry:
        return Entry(**{k: row[k] for k in row.keys()})

    def get(self, rid: str) -> Entry:
        with self._lock:
            return self._entry(self._row(rid))

    def pending(self) -> list[Entry]:
        self.expire_due()
        with self._lock:
            return [self._entry(r) for r in self._db.execute("SELECT * FROM requests WHERE status='PENDING' ORDER BY created_at")]

    def history(self, *, status: Iterable[str] = (), session_id: str | None = None, text: str = "",
                limit: int = 500) -> list[Entry]:
        q, args = "SELECT * FROM requests WHERE 1=1", []
        st = list(status)
        if st:
            q += f" AND status IN ({','.join('?' * len(st))})"
            args += st
        if session_id:
            q += " AND session_id=?"
            args.append(session_id)
        if text:
            q += " AND (title LIKE ? OR operation LIKE ? OR resource LIKE ?)"
            args += [f"%{text}%"] * 3
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [self._entry(r) for r in self._db.execute(q, args)]

    def close(self) -> None:
        with self._lock:
            self._db.close()
