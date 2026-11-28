"""approvals.py — S00-14 (UI): aprovacao humana presa ao HEAD exato. Sem Qt.

Quem decide e o modulo `approval` do agent-runtime (fixado no pyproject): `classify_risk` da o risco, `from_github_comment`
/ `from_github_review` reconhecem aprovacoes, `merge_decision` diz se o PR esta elegivel. A barra so junta os dados do
GitHub (arquivos, labels, CI, comentarios, reviews) e os fatos GITHUB_REVIEW_SUBMITTED que o runtime persistiu, lidos do
EventStore em mode=ro. A barra nunca grava no EventStore e nunca faz merge.

Canal da acao (S00-05 C9, fonte 2): um comentario no PR com UMA linha `APROVADO <RISCO> <sha40>`, postado pelo `gh` com o
login atual, que precisa estar em AGENT_BAR_APPROVERS. Antes de postar: confirmacao explicita e nova leitura do HEAD e do
CI; HEAD diferente ou CI nao verde = recusa com o motivo.

Risco aberto (S00-05, "Limite conhecido"): os agentes usam a mesma credencial do Felipe. Um comentario postado por esta
conta nao prova que foi o Felipe quem clicou.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from agent_runtime.approval import (
    ApprovalError,
    ApprovalRecord,
    MergeDecision,
    classify_risk,
    from_github_comment,
    from_github_review,
    merge_decision,
)

Gh = Callable[[list[str]], tuple[int, str]]
ENGINE_REPO = "Plan2Space/sketchup-mcp-next"
CREDENTIAL_WARNING = ("Atenção: os agentes usam a mesma conta do GitHub (fmodesto30) que posta esta aprovação. "
                      "O comentário não prova que foi o Felipe quem clicou. A separação real exige uma conta própria para os "
                      "agentes, fora da lista de aprovadores: decisão do Felipe (S00-05, risco aberto).")
_ROLLUP = {"SUCCESS": "success", "FAILURE": "failure", "ERROR": "failure", "PENDING": "pending", "EXPECTED": "pending"}
HEAD_QUERY = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){"
              "headRefOid commits(last:1){nodes{commit{committedDate statusCheckRollup{state}}}}}}}")
DEFAULT_REVIEWER = "fmmodesto"   # agente revisor: aprova LOW/MEDIUM; nos HIGH so recomenda (decisao do Felipe)


@dataclass
class PrApproval:
    repo: str
    number: int
    url: str = ""
    title: str = ""
    head_sha: str = ""
    risk: str = ""
    risk_reasons: list[str] = field(default_factory=list)
    ci_state: str = "none"                      # success | failure | pending | none
    evidence_ok: bool | None = None             # None = evidencia de execucao nao se aplica a este repo (vale o CI)
    evidence_note: str = ""
    approval: ApprovalRecord | None = None
    reasons: list[str] = field(default_factory=list)
    state: str = "erro"                          # auto_low | aguardando | aprovado | bloqueado | erro
    can_approve: bool = False
    blocked_reason: str = ""
    head_committed_at: str | None = None        # data do commit do HEAD (base do "ha quanto tempo espera"); None = desconhecida
    reviewer_verdict: str | None = None         # APPROVED | CHANGES_REQUESTED | COMMENTED | comentário; None = sem opiniao no HEAD
    reviewer_note: str = ""
    reviewer_url: str = ""

    @property
    def pr_ref(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class ApprovalBoard:
    rows: list[PrApproval] = field(default_factory=list)
    login: str | None = None
    approvers: dict[str, tuple[str, ...]] = field(default_factory=dict)   # por nivel: {'MEDIUM': (...), 'HIGH': (...)}
    credential_warning: str = CREDENTIAL_WARNING
    error: str | None = None
    reviewer: str = DEFAULT_REVIEWER
    collected_at: str | None = None

    @property
    def waiting(self) -> list[PrApproval]:
        return [r for r in self.rows if r.state in ("aguardando", "bloqueado", "erro") and r.risk != "LOW"]


LEVELS = ("MEDIUM", "HIGH")
Approvers = Mapping[str, Sequence[str]]


def _split(raw: str) -> tuple[str, ...]:
    return tuple(a.strip() for a in (raw or "").split(",") if a.strip())


def by_level(approvers: Approvers | Sequence[str], reviewer: str = DEFAULT_REVIEWER) -> dict[str, tuple[str, ...]]:
    """Mapa por nivel (runtime 0de08b9: sem heranca entre niveis). Lista plana = os dois niveis. A conta do revisor nunca
    aprova HIGH, mesmo se configurada: e tirada da lista HIGH aqui."""
    if isinstance(approvers, Mapping):
        m = {lvl: tuple(next((v for k, v in approvers.items() if str(k).upper() == lvl), ())) for lvl in LEVELS}
    else:
        flat = tuple(approvers)
        m = {lvl: flat for lvl in LEVELS}
    m["HIGH"] = tuple(a for a in m["HIGH"] if a.lower() != reviewer.lower())
    return m


def approvers_from_env(env: Mapping[str, str] | None = None) -> dict[str, tuple[str, ...]]:
    """AGENT_BAR_APPROVERS_MEDIUM / AGENT_BAR_APPROVERS_HIGH; a antiga AGENT_BAR_APPROVERS (plana) vale para os dois niveis
    quando o nivel nao tem variavel propria."""
    e = os.environ if env is None else env
    flat = _split(e.get("AGENT_BAR_APPROVERS", ""))
    raw = {lvl: _split(e[f"AGENT_BAR_APPROVERS_{lvl}"]) if f"AGENT_BAR_APPROVERS_{lvl}" in e else flat for lvl in LEVELS}
    return by_level(raw, e.get("AGENT_BAR_REVIEWER") or DEFAULT_REVIEWER)


def approvers_text(m: Mapping[str, Sequence[str]]) -> str:
    return " · ".join(f"{lvl}: {', '.join(m.get(lvl, ())) or 'ninguém'}" for lvl in LEVELS)


def approval_line(risk: str, sha: str) -> str:
    return f"APROVADO {risk} {sha}"


def _lines(out: str) -> list[dict]:
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            v = json.loads(line)
            rows += v if isinstance(v, list) else [v]
    return rows


def _rest(gh: Gh, path: str, jq: str | None = None) -> str:
    args = ["api", path] + (["--paginate", "--jq", jq] if jq else [])
    rc, out = gh(args)
    if rc != 0:
        raise RuntimeError(f"gh api {path.split('/', 3)[-1]} rc={rc}: {(out.splitlines() or ['sem saida'])[0][:160]}")
    return out


def read_head(gh: Gh, repo: str, number: int) -> tuple[str, str]:
    """(HEAD atual, estado do CI nesse HEAD) numa consulta GraphQL. Usado na tela e de novo no clique."""
    head, ci, _ = read_head_info(gh, repo, number)
    return head, ci


def read_head_info(gh: Gh, repo: str, number: int) -> tuple[str, str, str | None]:
    """(HEAD, CI, data do commit do HEAD ou None)."""
    owner, name = repo.split("/", 1)
    rc, out = gh(["api", "graphql", "-f", "query=" + HEAD_QUERY, "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}"])
    if rc != 0:
        raise RuntimeError(f"gh api graphql rc={rc}: {(out.splitlines() or ['sem saida'])[0][:160]}")
    pr = json.loads(out)["data"]["repository"]["pullRequest"]
    nodes = (pr.get("commits") or {}).get("nodes") or [{}]
    commit = nodes[-1].get("commit") or {}
    rollup = (commit.get("statusCheckRollup") or {}).get("state")
    committed = commit.get("committedDate") if isinstance(commit.get("committedDate"), str) else None
    return str(pr["headRefOid"]), _ROLLUP.get(str(rollup), "none") if rollup else "none", committed


def stored_approvals(store_path: Path | None, pr_ref: str) -> list[ApprovalRecord]:
    """Fatos GITHUB_REVIEW_SUBMITTED do runtime para este PR, lidos em mode=ro. Banco ausente/ilegivel = nenhum (nunca cria)."""
    if store_path is None or not Path(store_path).is_file():
        return []
    out = []
    try:
        uri = Path(store_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute("SELECT payload FROM events WHERE event_type='GITHUB_REVIEW_SUBMITTED' AND job_id=? ORDER BY id",
                              (pr_ref,)).fetchall()
    except sqlite3.Error:
        return []
    for (payload,) in rows:
        try:
            out.append(ApprovalRecord(**json.loads(payload)))
        except (ValueError, TypeError, ApprovalError):
            continue                                                         # registro invalido nao vira aprovacao
    return out


def evidence_for(repo: str, head_sha: str, runs) -> tuple[bool | None, str]:
    """Motor: precisa de uma execucao neste HEAD, PASS e sem problema. Outros repos: nao se aplica (vale o CI), escrito assim."""
    if repo != ENGINE_REPO:
        return None, "evidência de execução não se aplica a este repo (sem motor): vale o CI do HEAD"
    at_head = [r for r in (getattr(runs, "runs", None) or []) if r.engine_commit == head_sha]
    if not at_head:
        return False, f"sem evidência: nenhuma execução do motor no HEAD {head_sha[:12]}"
    good = [r for r in at_head if r.verdict == "PASS" and not r.problems]
    if not good:
        return False, f"evidência inválida no HEAD {head_sha[:12]}: " + "; ".join(p for r in at_head for p in (r.problems or [f'verdict {r.verdict}']))[:200]
    return True, f"execução {good[0].run_id} PASS no HEAD, sha256 conferido"


def _iso(text: object):
    import datetime as _dt
    if not isinstance(text, str) or not text:
        return None
    try:
        v = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)


def _first_line(text: object) -> str:
    return (str(text or "").strip().splitlines() or [""])[0][:160]


def reviewer_opinion(reviewer: str, head: str, committed_at: str | None, reviews: list[dict], comments: list[dict]
                     ) -> tuple[str, str, str] | None:
    """Ultima opiniao do revisor NESTE HEAD: review com commit_id = HEAD; senao comentario que cita o SHA (7+) ou e posterior
    ao commit do HEAD. (veredito, primeira linha, link) ou None. So leitura."""
    who = reviewer.lower()
    mine = [r for r in reviews if str((r.get("user") or {}).get("login", "")).lower() == who and r.get("commit_id") == head
            and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")]
    if mine:
        r = mine[-1]
        return str(r["state"]), _first_line(r.get("body")), str(r.get("html_url") or "")
    since = _iso(committed_at)
    notes = [c for c in comments if str((c.get("user") or {}).get("login", "")).lower() == who
             and (head[:7] in str(c.get("body") or "") or (since is not None and (_iso(c.get("created_at")) or since) > since))]
    if notes:
        c = notes[-1]
        return "comentário", _first_line(c.get("body")), str(c.get("html_url") or "")
    return None


def _one(gh: Gh, repo: str, number: int, approvers: Mapping[str, Sequence[str]], login: str | None, runs, store_path,
         reviewer: str = DEFAULT_REVIEWER) -> PrApproval:
    row = PrApproval(repo, number)
    try:
        pr = json.loads(_rest(gh, f"repos/{repo}/pulls/{number}"))
        row.url, row.title = str(pr.get("html_url") or ""), str(pr.get("title") or "")
        files = _lines(_rest(gh, f"repos/{repo}/pulls/{number}/files",
                             ".[] | {filename, status, patch, previous_filename}"))
        comments = _lines(_rest(gh, f"repos/{repo}/issues/{number}/comments",
                                ".[] | {user: {login: .user.login, type: .user.type}, body, created_at, updated_at, html_url}"))
        reviews = _lines(_rest(gh, f"repos/{repo}/pulls/{number}/reviews",
                               ".[] | {user: {login: .user.login, type: .user.type}, state, commit_id, submitted_at, html_url, body}"))
        row.head_sha, row.ci_state, row.head_committed_at = read_head_info(gh, repo, number)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        row.blocked_reason = f"leitura do GitHub falhou: {exc}"[:240]
        return row
    names = [f["filename"] for f in files]
    changes = {f["filename"]: {k: f.get(k) for k in ("status", "patch", "previous_filename")} for f in files}
    labels = [lbl.get("name", "") for lbl in pr.get("labels") or []]
    row.risk, row.risk_reasons = classify_risk(names, labels, file_changes=changes, repo=repo)   # mesmo perfil do merge_gate
    author = str((pr.get("user") or {}).get("login") or "")
    level_approvers = tuple(approvers.get(row.risk, ()))                    # comentario: so quem aprova ESTE nivel (sem heranca)
    records: list[ApprovalRecord] = []
    for c in comments:
        try:
            rec = from_github_comment(c, pr=row.pr_ref, approvers=level_approvers)
        except ApprovalError:
            rec = None
        records += [rec] if rec else []
    for r in reviews:
        try:
            rec = from_github_review(r, pr=row.pr_ref, risk=row.risk, approvers=approvers, author=author)
        except ApprovalError:
            rec = None
        records += [rec] if rec else []
    records += stored_approvals(store_path, row.pr_ref)
    opinion = reviewer_opinion(reviewer, row.head_sha, row.head_committed_at, reviews, comments)
    if opinion:
        row.reviewer_verdict, row.reviewer_note, row.reviewer_url = opinion
    row.evidence_ok, row.evidence_note = evidence_for(repo, row.head_sha, runs)
    decision: MergeDecision = merge_decision(pr=row.pr_ref, head_sha=row.head_sha, risk=row.risk, ci_state=row.ci_state,
                                             evidence_valid=row.evidence_ok is not False, approvals=records)
    row.reasons = list(decision.reasons)
    row.approval = decision.approval
    if row.ci_state != "success" or row.evidence_ok is False:
        row.state = "bloqueado"
        why = [f"CI {row.ci_state} no HEAD {row.head_sha[:12]}"] if row.ci_state != "success" else []
        row.blocked_reason = "; ".join(why + ([row.evidence_note] if row.evidence_ok is False else []))
    elif decision.eligible and decision.automatic:
        row.state, row.blocked_reason = "auto_low", "LOW com CI verde: merge automático pelo runtime, sem aprovação humana"
    elif decision.eligible:
        row.state, row.blocked_reason = "aprovado", f"aprovado por {decision.approval.approver} no HEAD {row.head_sha[:12]}"
    else:
        row.state = "aguardando"
        who = {a.lower() for a in level_approvers}
        if not level_approvers:
            row.blocked_reason = f"AGENT_BAR_APPROVERS_{row.risk} vazio: nenhum login configurado como aprovador {row.risk}"
        elif not login or login.lower() not in who:
            row.blocked_reason = (f"login atual ({login or 'desconhecido'}) fora da lista de aprovadores {row.risk} "
                                  f"({', '.join(level_approvers)})")
        else:
            row.can_approve = True
    return row


@dataclass
class ApprovalMemo:
    """C2 (cota REST): o que já foi lido, por PR, com a chave (HEAD, updatedAt, evidência); o login; e até quando o
    REST está suspenso por cota esgotada. Vive no Worker (um por processo)."""
    rows: dict = field(default_factory=dict)          # (repo, n) -> (chave, PrApproval)
    login: str | None = None
    rest_blocked_until: float | None = None


def _rate_limited(text: object) -> bool:
    low = str(text or "").lower()
    return "rate limit" in low


def _rest_reset(gh: Gh, fallback: float) -> float:
    """Quando a cota volta (gh api rate_limit não gasta cota). Ilegível = `fallback`."""
    rc, out = gh(["api", "rate_limit"])
    try:
        return float(json.loads(out)["resources"]["core"]["reset"]) if rc == 0 else fallback
    except (ValueError, TypeError, KeyError):
        return fallback


def collect_approvals(gh: Gh, prs: Iterable[tuple[str, int]], approvers: Approvers | Sequence[str] | None = None, runs=None,
                      store_path: Path | None = None, max_prs: int = 12, reviewer: str | None = None, now=None,
                      memo: ApprovalMemo | None = None, versions: Mapping[tuple[str, int], tuple[str, str, str]] | None = None,
                      clock: Callable[[], float] | None = None) -> ApprovalBoard:
    """Sem `memo`, como sempre: lê tudo. Com `memo` e `versions` (HEAD, updatedAt, estado do CI da fila), só relê o PR
    cuja chave (HEAD, updatedAt, CI, evidência do motor) mudou (o updatedAt não muda quando um check termina); o login é lido uma vez; no 403 de cota, o REST para até o reset e a
    linha diz isso (aprovação indisponível, nunca dado velho sem rótulo). A regra de aprovação é a mesma (`_one`)."""
    import datetime as _dt
    import time as _time
    reviewer = reviewer or os.environ.get("AGENT_BAR_REVIEWER") or DEFAULT_REVIEWER
    approvers = approvers_from_env() if approvers is None else by_level(approvers, reviewer)
    t = (clock or _time.time)()
    blocked = bool(memo and memo.rest_blocked_until and t < memo.rest_blocked_until)

    def blocked_text() -> str:
        until = _time.strftime("%H:%M", _time.gmtime(memo.rest_blocked_until or t))  # type: ignore[union-attr]
        return f"cota REST do GitHub esgotada até {until} UTC: leitura adiada"

    error = None
    if memo is not None and memo.login:
        login = memo.login
    elif blocked:
        login, error = None, blocked_text()
    else:
        rc, out = gh(["api", "user", "--jq", ".login"])
        login = out.strip() if rc == 0 else None
        error = None if rc == 0 else f"gh api user rc={rc}: {out[:120]}"
        if memo is not None and rc == 0:
            memo.login = login
        elif memo is not None and _rate_limited(out):
            memo.rest_blocked_until, blocked = _rest_reset(gh, t + 900), True
            error = blocked_text()
    at = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    board = ApprovalBoard(login=login, approvers=approvers, error=error, reviewer=reviewer, collected_at=at)
    for repo, n in list(prs)[:max_prs]:
        ref = (repo, n)
        key = None
        if versions and ref in versions:
            head, updated, checks = versions[ref]
            key = (head, updated, checks, evidence_for(repo, head, runs) if head else None)
        cached = memo.rows.get(ref) if memo is not None else None
        if cached is not None and key is not None and cached[0] == key:
            board.rows.append(cached[1])                                     # nada mudou: zero REST
            continue
        if blocked:
            board.rows.append(PrApproval(repo, n, blocked_reason=blocked_text()))
            continue
        row = _one(gh, repo, n, approvers, login, runs, store_path, reviewer)
        if memo is not None and _rate_limited(row.blocked_reason):
            memo.rest_blocked_until, blocked = _rest_reset(gh, t + 900), True
            row = PrApproval(repo, n, blocked_reason=blocked_text())
        elif memo is not None and key is not None and not row.blocked_reason.startswith("leitura do GitHub falhou"):
            memo.rows[ref] = (key, row)
        board.rows.append(row)
    return board


def confirmation_text(row: PrApproval) -> str:
    return (f"Aprovar {row.pr_ref} como {row.risk} no HEAD\n{row.head_sha}\n\nSerá postado no PR, pela conta do gh, exatamente:\n"
            f"{approval_line(row.risk, row.head_sha)}\n\n{CREDENTIAL_WARNING}\n\n"
            "Antes de postar, a barra confere de novo o HEAD e o CI. A barra não faz merge: quem decide o merge é o runtime.")


def approve(gh: Gh, row: PrApproval, confirm: Callable[[str], bool]) -> tuple[bool, str]:
    """Posta a aprovacao do HEAD mostrado. Nunca sem: linha aprovavel, confirmacao explicita, HEAD e CI conferidos de novo."""
    if not row.can_approve:
        return False, f"aprovação indisponível: {row.blocked_reason or row.state}"
    line = approval_line(row.risk, row.head_sha)
    if not confirm(confirmation_text(row)):
        return False, "aprovação cancelada: nada foi postado"
    try:
        head, ci = read_head(gh, row.repo, row.number)
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        return False, f"não foi possível conferir o HEAD agora: {exc}"[:240]
    if head != row.head_sha:
        return False, f"recusado: o HEAD mudou de {row.head_sha[:12]} para {head[:12]}; a tela volta a aguardar aprovação do HEAD novo"
    if ci != "success":
        return False, f"recusado: o CI do HEAD {head[:12]} agora está {ci!r}"
    rc, out = gh(["pr", "comment", str(row.number), "--repo", row.repo, "--body", line])
    if rc != 0:
        return False, f"gh pr comment rc={rc}: {(out.splitlines() or ['sem saida'])[0][:160]}"
    return True, f"aprovação postada: {out.strip()[:200]}"
