"""notifications.py — Notification Center persistente (SPEC agent-bar/P2S-STUDIO-WORKSPACE-UX.md, F3).

Mesmo arquivo SQLite do Permission Inbox (`permissions.sqlite`, tabela `notifications`), conexao propria (WAL).
Categorias: PERMISSION, AGENT, TASK, BUILD, RENDER, PR_CI, ERROR, REVIEW, HUMAN_APPROVAL.
Estados: NEW, READ, ACTION_REQUIRED, RESOLVED, EXPIRED, FAILED.

Regras:
- **idempotente por `dedupe_key`**: publicar de novo o mesmo evento atualiza a linha (estado/texto), nao duplica;
- **acao pendente nunca vira "lida" sozinha**: marcar como lida nao resolve ACTION_REQUIRED (so a decisao resolve);
- **nada some**: retencao so apaga o que esta RESOLVED/READ/EXPIRED/FAILED e mais velho que o prazo; ACTION_REQUIRED
  nunca e apagado;
- notificacao nativa do Windows e opcional e nunca a unica representacao (a linha aqui sempre existe antes).

Permissoes entram por `sync_permissions(inbox)`: cada pedido vira (ou atualiza) uma notificacao `permission:<id>`:
PENDING -> ACTION_REQUIRED, ALLOWED/DENIED -> RESOLVED, EXPIRED -> EXPIRED, CANCELLED -> EXPIRED (com o motivo).
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = ("PERMISSION", "AGENT", "TASK", "BUILD", "RENDER", "PR_CI", "ERROR", "REVIEW", "HUMAN_APPROVAL", "IMAGE")
IMAGE_STATE = {"RECORDED": "NEW", "SENT": "NEW", "NOT_SENT": "FAILED"}
IMAGE_VERB = {"RECORDED": "Imagem registrada para envio", "SENT": "Imagem entregue", "NOT_SENT": "Imagem não enviada"}
PURPOSE_TEXT = {"REFERENCE": "referência", "EVALUATION": "avaliação", "CORRECTION_PACKAGE": "pacote de correção",
                "CHAT": "Chat", "CALIBRATION": "calibração de tokens"}


def image_notification(rows) -> dict:
    """Argumentos de `NotificationCenter.publish` para um lote do registro de imagem (uma notificacao por lote)."""
    first = rows[0]
    names = ", ".join(f"{Path(r.path).name} (sha256 {r.sha256[:12]}…)" for r in rows[:4])
    more = f" e mais {len(rows) - 4}" if len(rows) > 4 else ""
    body = (f"{first.provider_id} · {first.model_id} · para {PURPOSE_TEXT.get(first.purpose, first.purpose)} · "
            f"pedido por {first.requested_by} · {names}{more}")
    if first.ref_id:
        body += f" · referência {first.ref_id}{' ' + first.ref_version if first.ref_version else ''}"
    if first.error:
        body += f" · {first.error}"
    return {"category": "IMAGE", "title": f"{IMAGE_VERB.get(first.status, 'Imagem')}: {len(rows)} arquivo(s)",
            "body": body, "state": IMAGE_STATE.get(first.status, "NEW"), "session_id": first.session_id,
            "source": f"image:{first.batch_id}", "link": "chat", "dedupe_key": f"image:{first.batch_id}"}
STATES = ("NEW", "READ", "ACTION_REQUIRED", "RESOLVED", "EXPIRED", "FAILED")
OPEN_STATES = ("NEW", "ACTION_REQUIRED")
PERMISSION_STATE = {"PENDING": "ACTION_REQUIRED", "ALLOWED": "RESOLVED", "DENIED": "RESOLVED", "EXPIRED": "EXPIRED",
                    "CANCELLED": "EXPIRED"}
DEFAULT_RETENTION_DAYS = 90


@dataclass(frozen=True)
class Notification:
    id: str
    created_at: float
    updated_at: float
    category: str
    state: str
    title: str
    body: str
    session_id: str
    source: str
    link: str
    dedupe_key: str


class NotificationCenter:
    def __init__(self, path: Path | str = ":memory:", clock: Callable[[], float] = time.time,
                 on_change: Callable[[], None] | None = None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.on_change = on_change
        self._lock = threading.RLock()
        self.closed = False
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL, category TEXT NOT NULL,
                state TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, session_id TEXT NOT NULL,
                source TEXT NOT NULL, link TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS notifications_state ON notifications(state, updated_at);
        """)

    # ------------------------------------------------------------------------------------------------ publicar
    def publish(self, category: str, title: str, *, body: str = "", state: str = "NEW", session_id: str = "",
                source: str = "", link: str = "", dedupe_key: str | None = None) -> Notification:
        if category not in CATEGORIES:
            raise ValueError(f"categoria desconhecida: {category}")
        if state not in STATES:
            raise ValueError(f"estado desconhecido: {state}")
        key = dedupe_key or uuid.uuid4().hex
        now = self.clock()
        with self._lock:
            row = self._db.execute("SELECT * FROM notifications WHERE dedupe_key=?", (key,)).fetchone()
            if row is None:
                nid = uuid.uuid4().hex
                self._db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (nid, now, now, category, state, title[:300], body[:2000], session_id, source, link, key))
            else:
                nid = row["id"]
                if (row["state"], row["title"], row["body"]) != (state, title[:300], body[:2000]):
                    self._db.execute("UPDATE notifications SET state=?, title=?, body=?, updated_at=? WHERE id=?",
                                     (state, title[:300], body[:2000], now, nid))
        self._changed()
        return self.get(nid)

    def sync_permissions(self, inbox) -> int:
        """Espelha o Permission Inbox: uma notificacao por pedido, estado acompanhando a decisao. Idempotente."""
        n = 0
        try:
            entries = inbox.history(limit=1000)
        except RuntimeError:                                                 # InboxClosed: Studio encerrando
            return 0
        for e in entries:
            state = PERMISSION_STATE.get(e.status, "NEW")
            verb = {"ACTION_REQUIRED": "Permissão pedida", "RESOLVED": "Permissão decidida", "EXPIRED": "Permissão sem resposta"}
            body = f"{e.provider} · {e.kind} · risco {e.risk}"
            if e.status in ("ALLOWED", "DENIED"):
                body += f" · {e.status.lower()} ({e.decision}) por {e.decided_by}"
            elif e.status in ("EXPIRED", "CANCELLED") and e.result:
                body += f" · {e.result}"
            if e.repeat_note:
                body += f" · repetido: {e.repeat_note}"
            before = self._state_of(f"permission:{e.id}")
            self.publish("PERMISSION", f"{verb.get(state, 'Permissão')}: {e.title}", body=body, state=state,
                         session_id=e.session_id, source=f"permission:{e.id}", link=f"permissions:{e.id}",
                         dedupe_key=f"permission:{e.id}")
            n += before != state
        return n

    def _state_of(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT state FROM notifications WHERE dedupe_key=?", (key,)).fetchone()
        return row["state"] if row else None

    # ------------------------------------------------------------------------------------------------ estado
    def mark_read(self, nid: str) -> Notification:
        """NEW -> READ. ACTION_REQUIRED continua pedindo acao (ler nao e decidir)."""
        with self._lock:
            self._db.execute("UPDATE notifications SET state='READ', updated_at=? WHERE id=? AND state='NEW'",
                             (self.clock(), nid))
        self._changed()
        return self.get(nid)

    def mark_all_read(self) -> int:
        with self._lock:
            n = self._db.execute("UPDATE notifications SET state='READ', updated_at=? WHERE state='NEW'",
                                 (self.clock(),)).rowcount
        self._changed()
        return n

    def resolve(self, nid: str, state: str = "RESOLVED") -> Notification:
        if state not in ("RESOLVED", "EXPIRED", "FAILED"):
            raise ValueError(state)
        with self._lock:
            self._db.execute("UPDATE notifications SET state=?, updated_at=? WHERE id=?", (state, self.clock(), nid))
        self._changed()
        return self.get(nid)

    def apply_retention(self, days: float = DEFAULT_RETENTION_DAYS) -> int:
        cutoff = self.clock() - days * 86400
        with self._lock:
            return self._db.execute("DELETE FROM notifications WHERE state IN ('READ','RESOLVED','EXPIRED','FAILED') "
                                    "AND updated_at < ?", (cutoff,)).rowcount

    # ------------------------------------------------------------------------------------------------ consulta
    def get(self, nid: str) -> Notification:
        with self._lock:
            row = self._db.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone()
        if row is None:
            raise KeyError(nid)
        return Notification(**{k: row[k] for k in row.keys()})

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT state, COUNT(*) AS n FROM notifications GROUP BY state").fetchall()
        out = {s: 0 for s in STATES}
        out.update({r["state"]: r["n"] for r in rows})
        return out

    def badge(self) -> int:
        """O numero do sino: o que ainda nao foi visto (NEW) mais o que pede acao (ACTION_REQUIRED)."""
        c = self.counts()
        return c["NEW"] + c["ACTION_REQUIRED"]

    def list(self, *, categories: Iterable[str] = (), states: Iterable[str] = (), text: str = "",
             session_id: str | None = None, since: float | None = None, limit: int = 500) -> list[Notification]:
        q, args = "SELECT * FROM notifications WHERE 1=1", []
        for col, values in (("category", list(categories)), ("state", list(states))):
            if values:
                q += f" AND {col} IN ({','.join('?' * len(values))})"
                args += values
        if text:
            q += " AND (title LIKE ? OR body LIKE ?)"
            args += [f"%{text}%"] * 2
        if session_id:
            q += " AND session_id=?"
            args.append(session_id)
        if since is not None:
            q += " AND updated_at >= ?"
            args.append(since)
        q += " ORDER BY CASE state WHEN 'ACTION_REQUIRED' THEN 0 WHEN 'NEW' THEN 1 ELSE 2 END, updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [Notification(**{k: r[k] for k in r.keys()}) for r in self._db.execute(q, args)]

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:                                                 # noqa: BLE001 - UI nao derruba o registro
                pass

    def close(self) -> None:
        with self._lock:
            if not self.closed:
                self.closed = True
                self._db.close()


# ------------------------------------------------------------------------------------- uso depois do close (#75)
def _quiet_after_close(default):
    """Depois do close (Studio encerrando), timers e sinais atrasados nao derrubam nada: a chamada vira no-op."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **kw):
            if self.closed:
                return default() if callable(default) else default
            return fn(self, *a, **kw)
        return wrapper
    return deco


for _name, _default in (("publish", None), ("sync_permissions", 0), ("mark_read", None), ("mark_all_read", 0),
                        ("resolve", None), ("apply_retention", 0), ("counts", lambda: {s: 0 for s in STATES}),
                        ("badge", 0), ("list", list)):
    setattr(NotificationCenter, _name, _quiet_after_close(_default)(getattr(NotificationCenter, _name)))
