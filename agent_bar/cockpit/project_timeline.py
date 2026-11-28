"""project_timeline.py — timeline consolidada do projeto (§7.5). Sem Qt.

Só eventos com fonte autoritativa, lidos sem escrever nada:

- GitHub (`gh pr list --state all`, por repo): PR criado (`createdAt`), PR mergeado (`mergedAt`) e o CI do HEAD do PR,
  aprovado ou falhou, quando TODOS os checks terminaram, na hora da última conclusão. Check pendente não vira evento.
- evidence (`index.json`): evidência publicada. O índice não traz hora; a data vem do prefixo AAAAMMDD do id do run.
  Sem prefixo, a data é desconhecida (``at=None``): o evento aparece em "tudo" e nunca entra num filtro de período.

Eventos de agente e de construção (tarefa iniciada, build, render, bloqueio, instalação) chegam com o EventStore do
agent-runtime; até lá não existem aqui, em vez de serem inferidos de branch ou de PR.

Sem duplicata: cada evento tem uma chave estável (o CI inclui o SHA do HEAD, então um HEAD novo é um evento novo) e
``merge`` une leituras pela chave.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from agent_bar.cockpit.github import GhClient, GhError

PR_CRIADO, PR_MERGEADO = "PR criado", "PR mergeado"
CI_APROVADO, CI_FALHOU = "CI aprovado", "CI falhou"
EVIDENCIA_PUBLICADA = "Evidência publicada"
KINDS = (PR_CRIADO, PR_MERGEADO, CI_APROVADO, CI_FALHOU, EVIDENCIA_PUBLICADA)
PERIODS = {"24h": dt.timedelta(hours=24), "7d": dt.timedelta(days=7), "30d": dt.timedelta(days=30), "tudo": None}

TIMELINE_PR_FIELDS = "number,title,url,createdAt,mergedAt,state,headRefOid,statusCheckRollup"
_FAILED = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
_OK = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
_RUN_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:\D|$)")


@dataclass(frozen=True)
class TimelineEvent:
    at: str | None                 # ISO-8601 (ou só a data, AAAA-MM-DD); None = data desconhecida
    kind: str
    repo: str
    ref: str                       # "#123" ou o id do run
    title: str = ""
    url: str = ""
    key: str = ""


@dataclass(frozen=True)
class GithubEvents:
    events: list[TimelineEvent]
    failed: dict[str, str] = field(default_factory=dict)   # repo -> motivo (não lido ≠ sem eventos)


def _parse(at: str | None) -> dt.datetime | None:
    if not at:
        return None
    try:
        if len(at) == 10:
            return dt.datetime.fromisoformat(at).replace(tzinfo=dt.timezone.utc)
        return dt.datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _ci_event(repo: str, pr: dict) -> TimelineEvent | None:
    checks = [c for c in pr.get("statusCheckRollup") or [] if isinstance(c, dict)]
    if not checks:
        return None
    states, stamps = [], []
    for c in checks:
        state = str(c.get("conclusion") or c.get("state") or "").upper()
        if c.get("__typename") == "CheckRun" and str(c.get("status") or "").upper() != "COMPLETED":
            return None                                  # ainda rodando: sem evento
        if state not in _FAILED | _OK:
            return None                                  # pendente / esperado
        states.append(state)
        stamp = c.get("completedAt") or c.get("createdAt")
        if stamp and not str(stamp).startswith("0001"):
            stamps.append(str(stamp))
    kind = CI_FALHOU if any(s in _FAILED for s in states) else CI_APROVADO
    head = str(pr.get("headRefOid") or "")
    at = max(stamps, key=lambda s: _parse(s) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) if stamps else None
    n = pr.get("number")
    return TimelineEvent(at, kind, repo, f"#{n}", str(pr.get("title") or ""), str(pr.get("url") or ""),
                         f"ci|{repo}|{n}|{head}")


def pr_events(repo: str, prs: Iterable[dict]) -> list[TimelineEvent]:
    out = []
    for pr in prs:
        if not isinstance(pr, dict) or pr.get("number") is None:
            continue
        n, title, url = pr["number"], str(pr.get("title") or ""), str(pr.get("url") or "")
        if pr.get("createdAt"):
            out.append(TimelineEvent(str(pr["createdAt"]), PR_CRIADO, repo, f"#{n}", title, url, f"created|{repo}|{n}"))
        if pr.get("mergedAt"):
            out.append(TimelineEvent(str(pr["mergedAt"]), PR_MERGEADO, repo, f"#{n}", title, url, f"merged|{repo}|{n}"))
        ci = _ci_event(repo, pr)
        if ci is not None:
            out.append(ci)
    return out


def collect_github_events(client: GhClient, repos: Iterable[str], limit: int = 50) -> GithubEvents:
    events: list[TimelineEvent] = []
    failed: dict[str, str] = {}
    for repo in repos:
        try:
            prs = client._json(("pr", "list"), repo, {"--json": TIMELINE_PR_FIELDS, "--limit": str(limit),
                                                      "--state": "all"}) or []
        except GhError as exc:
            failed[repo] = f"{exc.kind}: {exc.message}"
            continue
        events += pr_events(repo, prs)
    return GithubEvents(merge(events), failed)


def evidence_events(run_ids: Iterable[str], repo: str = "Plan2Space/evidence") -> list[TimelineEvent]:
    out = []
    for rid in run_ids:
        m = _RUN_DATE.match(rid)
        at = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        out.append(TimelineEvent(at, EVIDENCIA_PUBLICADA, repo, rid, rid, "", f"evidence|{repo}|{rid}"))
    return out


def _sort_key(e: TimelineEvent) -> tuple:
    p = _parse(e.at)
    return (p is None, -(p.timestamp()) if p else 0.0, e.key)


def merge(*groups: Iterable[TimelineEvent]) -> list[TimelineEvent]:
    """Une leituras sem duplicar (pela chave) e ordena do mais novo ao mais velho; sem data vai para o fim."""
    seen: dict[str, TimelineEvent] = {}
    for group in groups:
        for e in group:
            seen.setdefault(e.key, e)
    return sorted(seen.values(), key=_sort_key)


def filter_events(events: Iterable[TimelineEvent], now: str, period: str = "tudo", repo: str = "",
                  kinds: Iterable[str] = ()) -> list[TimelineEvent]:
    span = PERIODS[period]
    since = (_parse(now) - span) if span is not None else None
    wanted = set(kinds)
    out = []
    for e in events:
        if repo and e.repo != repo:
            continue
        if wanted and e.kind not in wanted:
            continue
        if since is not None:
            p = _parse(e.at)
            if p is None or p < since:
                continue
        out.append(e)
    return sorted(out, key=_sort_key)
