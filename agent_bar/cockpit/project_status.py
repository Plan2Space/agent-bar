"""project_status.py — coletores da Central de acompanhamento do projeto (§7 do pedido de 2026-11-27, P0). Sem Qt.

Somente leitura, das fontes autoritativas: o GitHub via `gh` (PRs dos repos da org) e o Project #1 (tarefas). Regra
travada por teste: sem fonte o valor é ``None`` / ``UNKNOWN`` ("desconhecido"), nunca zero nem sucesso inventado
(ADR 0008). Nada de percentual: só contagens, porque o board não dá um denominador confiável por frente.

Estados de entrega (§7.3), derivados do card do Project (coluna ``status`` + campo ``estado da prova``):

- Backlog/Ready → Planejado · In Progress → Em implementação (Implementado com UNIT_PASS) · GPT Review → Em revisão
- Done → Integrado. Só vira Comprovado com prova de integração (INTEGRATION_PASS / SKETCHUP_PASS): PR mergeado não é
  funcionalidade comprovada.
- Blocked, ou prova BLOCKED → Bloqueado. Coluna desconhecida → desconhecido.
- Instalado e Concluído existem no vocabulário, mas nenhuma fonte deste módulo os comprova ainda; não são emitidos.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agent_bar.cockpit.github import DEFAULT_REPOS, GhClient, GhError

UNKNOWN = "desconhecido"
MONITORED = DEFAULT_REPOS

PLANEJADO, EM_IMPLEMENTACAO, IMPLEMENTADO = "Planejado", "Em implementação", "Implementado"
EM_REVISAO, INTEGRADO, INSTALADO = "Em revisão", "Integrado", "Instalado"
COMPROVADO, BLOQUEADO, CONCLUIDO = "Comprovado", "Bloqueado", "Concluído"
DELIVERY_STATES = (PLANEJADO, EM_IMPLEMENTACAO, IMPLEMENTADO, EM_REVISAO, INTEGRADO, INSTALADO, COMPROVADO,
                   BLOQUEADO, CONCLUIDO)

T_PLANNED, T_IN_PROGRESS, T_DONE, T_BLOCKED = "planejadas", "em andamento", "concluídas", "bloqueadas"

_FAILED = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
_PROVEN = frozenset({"INTEGRATION_PASS", "SKETCHUP_PASS"})
_STATUS_BUCKET = {"backlog": T_PLANNED, "ready": T_PLANNED, "in progress": T_IN_PROGRESS, "gpt review": T_IN_PROGRESS,
                  "done": T_DONE, "blocked": T_BLOCKED}


def ci_failed(rollup: object) -> bool:
    for c in rollup if isinstance(rollup, list) else []:
        if isinstance(c, dict) and str(c.get("conclusion") or c.get("state") or "").upper() in _FAILED:
            return True
    return False


def _field(item: dict, name: str) -> Any:
    """Campos do Project chegam com caixa variável (``sPEC vinculada``): casa sem diferenciar maiúsculas."""
    for k, v in item.items():
        if k.lower() == name:
            return v
    return None


def item_state(item: dict) -> str:
    status = str(_field(item, "status") or "").strip().lower()
    proof = str(_field(item, "estado da prova") or "").strip().upper()
    if status == "blocked" or proof == "BLOCKED":
        return BLOQUEADO
    if status in ("backlog", "ready"):
        return PLANEJADO
    if status == "in progress":
        return IMPLEMENTADO if proof == "UNIT_PASS" else EM_IMPLEMENTACAO
    if status == "gpt review":
        return EM_REVISAO
    if status == "done":
        return COMPROVADO if proof in _PROVEN else INTEGRADO
    return UNKNOWN


# ------------------------------------------------------------------------------------------------------- GitHub


@dataclass(frozen=True)
class RepoSnapshot:
    repo: str
    state: str = "ok"                       # "ok" ou UNKNOWN
    open_prs: int | None = None
    awaiting_review: int | None = None
    ci_failing: int | None = None
    merged_recent: int | None = None
    last_activity: str | None = None        # ISO-8601 do GitHub, o mais recente entre os PRs lidos
    error: str = ""


@dataclass(frozen=True)
class GithubOverview:
    repos: list[RepoSnapshot]
    new_repos: list[str]
    read_at: float

    def repo(self, name: str) -> RepoSnapshot:
        return next(r for r in self.repos if r.repo == name)

    @property
    def totals_partial(self) -> bool:
        return any(r.state == UNKNOWN for r in self.repos)

    def totals(self) -> dict[str, int | None]:
        """Soma dos repos que responderam. ``totals_partial`` diz se algum ficou de fora (a tela mostra "parcial")."""
        ok = [r for r in self.repos if r.state != UNKNOWN]
        keys = ("open_prs", "awaiting_review", "ci_failing", "merged_recent")
        if not ok:
            return {k: None for k in keys}
        return {k: sum(getattr(r, k) or 0 for r in ok) for k in keys}


def _snapshot(client: GhClient, repo: str, merged_limit: int) -> RepoSnapshot:
    try:
        open_prs = client.prs(repo, limit=100)
        merged = list(client._json(("pr", "list"), repo, {"--json": "number,title,headRefName,mergedAt,updatedAt,url",
                                                           "--limit": str(merged_limit), "--state": "merged"}) or [])
    except GhError as exc:
        return RepoSnapshot(repo, UNKNOWN, error=exc.message)
    stamps = [str(p.get("updatedAt")) for p in open_prs + merged if p.get("updatedAt")]
    return RepoSnapshot(
        repo,
        open_prs=len(open_prs),
        awaiting_review=sum(1 for p in open_prs if not p.get("isDraft") and p.get("reviewDecision") != "APPROVED"),
        ci_failing=sum(1 for p in open_prs if ci_failed(p.get("statusCheckRollup"))),
        merged_recent=len(merged),
        last_activity=max(stamps) if stamps else None,
    )


def collect_github(client: GhClient, org: str, now: float, merged_limit: int = 20) -> GithubOverview:
    """Os 8 repos monitorados sempre entram; os que a org listar a mais aparecem também e em ``new_repos``."""
    base = [f"{org}/{n}" for n in MONITORED]
    listed = client.repos(org)
    extra = sorted(r for r in listed if r not in base)
    return GithubOverview([_snapshot(client, r, merged_limit) for r in base + extra], extra, now)


# ------------------------------------------------------------------------------------------------------ Project


@dataclass(frozen=True)
class SpecProgress:
    spec: str
    total: int
    states: dict[str, int]                  # estado de entrega -> contagem (só contagens, sem percentual)


@dataclass(frozen=True)
class ProjectSnapshot:
    state: str
    read_at: float
    total: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    specs: list[SpecProgress] = field(default_factory=list)
    unlinked: int | None = None
    error: str = ""


def collect_project(client: GhClient, org: str, number: int, now: float, limit: int = 1000) -> ProjectSnapshot:
    try:
        data = client._json(("project", "item-list"), None, {"--owner": org, "--format": "json", "--limit": str(limit)},
                            positional=[str(int(number))]) or {}
    except GhError as exc:
        return ProjectSnapshot(UNKNOWN, now, error=f"{exc.kind}: {exc.message}")
    items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    counts = Counter(_STATUS_BUCKET.get(str(_field(i, "status") or "").strip().lower(), UNKNOWN) for i in items)
    for k in (T_PLANNED, T_IN_PROGRESS, T_DONE, T_BLOCKED):
        counts.setdefault(k, 0)
    by_spec: dict[str, Counter] = {}
    unlinked = 0
    for i in items:
        spec = str(_field(i, "spec vinculada") or "").strip()
        if not spec:
            unlinked += 1
            continue
        by_spec.setdefault(spec, Counter())[item_state(i)] += 1
    specs = [SpecProgress(s, sum(c.values()), dict(c)) for s, c in sorted(by_spec.items())]
    return ProjectSnapshot("ok", now, len(items), dict(counts), specs, unlinked)


# ----------------------------------------------------------------------------------------------------- Visão geral


@dataclass(frozen=True)
class Overview:
    github: GithubOverview
    project: ProjectSnapshot
    read_at: float
    active_agents: int | None = None        # fonte: Runtime/EventStore (próximo PR). Sem ela: desconhecido.
    health: tuple[tuple[str, str], ...] = ()


def _github_health(gh: GithubOverview) -> str:
    bad = [r.repo for r in gh.repos if r.state == UNKNOWN]
    if not bad:
        return "ok"
    if len(bad) == len(gh.repos):
        return "indisponível"
    return f"parcial ({len(bad)} sem resposta: {', '.join(b.split('/')[-1] for b in bad)})"


def collect_overview(client: GhClient, org: str, now: float, project_number: int = 1) -> Overview:
    gh = collect_github(client, org, now)
    proj = collect_project(client, org, project_number, now)
    health = (("github", _github_health(gh)),
              ("project", "ok" if proj.state == "ok" else f"indisponível ({proj.error})"),
              ("runtime", UNKNOWN))
    return Overview(gh, proj, now, None, health)
