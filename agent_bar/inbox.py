"""inbox.py — "Precisa de você": PRs abertos da org em que a decisao e do Felipe. Sem Qt.

Fluxo combinado (2026-11-21): o agente revisor (AGENT_BAR_REVIEWER, padrao `fmmodesto`) aprova LOW/MEDIUM e o runtime mergeia;
nos HIGH ele so recomenda, e quem aprova e o Felipe. Entra na lista: risco HIGH (de `agent_runtime.approval.classify_risk`)
em AGUARDANDO no HEAD atual (`merge_decision` sem aprovacao valida nesse HEAD, CI verde, evidencia ok). Commit novo = HEAD novo =
volta para a lista. Falha da coleta = INDISPONIVEL, nunca lista velha.

`InboxNotifier` avisa uma vez por PR@HEAD; varios novos juntos viram um aviso so. Falha do aviso vai para o log e nao para a lista.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

LOG = logging.getLogger("agent_bar")


@dataclass(frozen=True)
class HumanItem:
    pr_ref: str
    url: str
    title: str
    risk: str
    reason: str
    head_sha: str
    ci_state: str
    waiting_s: int | None
    reviewer_verdict: str | None = None
    reviewer_note: str = ""
    reviewer_url: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.pr_ref, self.head_sha


@dataclass
class Inbox:
    state: str                              # lido | indisponivel
    reason: str
    items: list[HumanItem] = field(default_factory=list)
    unreadable: int = 0
    reviewer: str = ""
    collected_at: str | None = None


def _iso(text: object) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        v = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)


def waited(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    m = max(0, seconds) // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def human_inbox(board, now: dt.datetime | None = None) -> Inbox:
    """`board` = ApprovalBoard (approvals.py). So le; quem classificou foi o runtime."""
    n = now or dt.datetime.now(dt.timezone.utc)
    if board.error and not board.rows:
        return Inbox("indisponivel", board.error, reviewer=getattr(board, "reviewer", ""))
    items = []
    for r in board.rows:
        if r.risk != "HIGH" or r.state != "aguardando":
            continue
        at = _iso(r.head_committed_at)
        items.append(HumanItem(r.pr_ref, r.url, r.title, r.risk, "; ".join(r.risk_reasons[:2]), r.head_sha, r.ci_state,
                               int((n - at).total_seconds()) if at else None, r.reviewer_verdict, r.reviewer_note, r.reviewer_url))
    items.sort(key=lambda i: (-(i.waiting_s if i.waiting_s is not None else -1), i.pr_ref))     # mais antigo primeiro; sem data no fim
    return Inbox("lido", f"{len(items)} PR(s) esperando você", items, sum(1 for r in board.rows if r.state == "erro"),
                 getattr(board, "reviewer", ""), getattr(board, "collected_at", None))


class InboxNotifier:
    """Um aviso por PR@HEAD. `show(titulo, texto, url)` pode falhar: vai para o log, a lista segue."""

    def __init__(self, show: Callable[[str, str, str], None], enabled: Callable[[], bool] = lambda: True):
        self.show, self.enabled = show, enabled
        self._seen: set[tuple[str, str]] = set()

    def update(self, inbox: Inbox) -> list[HumanItem]:
        if inbox.state != "lido":                                            # indisponivel: nao avisa e nao esquece o que ja avisou
            return []
        new = [i for i in inbox.items if i.key not in self._seen]
        self._seen |= {i.key for i in new}
        if not new or not self.enabled():
            return new
        first = new[0]
        if len(new) == 1:
            title, text = "PR precisa de você", f"{first.pr_ref.split('/')[-1]} · {first.risk} · {first.title[:80]}"
        else:
            title, text = f"{len(new)} PRs precisam de você", ", ".join(i.pr_ref.split("/")[-1] for i in new[:5])
        try:
            self.show(title, text, first.url)
        except Exception as exc:                                              # noqa: BLE001 - aviso nunca derruba a lista
            LOG.warning("notificacao 'precisa de você' falhou (%s): %s", first.pr_ref, exc)
        return new
