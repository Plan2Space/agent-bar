"""github.py — PRs e Actions da org via `gh`, SOMENTE LEITURA (F3, R20/D6). Sem Qt.

Todo comando passa por `read_only_argv`, que so aceita uma lista fechada de subcomandos de leitura e sempre exige
`--repo OWNER/NAME`. Nao existe caminho para comentar, aprovar, mergear, fechar, editar ou re-executar: o Felipe faz
isso no GitHub (botao "Abrir no GitHub"). O `gh` roda sem GH_TOKEN/GITHUB_TOKEN no ambiente (usa o login do keyring).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_bar.cockpit.acp import CREATE_NO_WINDOW

# subcomando -> flags permitidas (alem de --repo). Qualquer outra coisa e recusada antes de virar processo.
READ_ONLY: dict[tuple[str, str], frozenset[str]] = {
    ("pr", "list"): frozenset({"--json", "--limit", "--state"}),
    ("pr", "view"): frozenset({"--json"}),
    ("pr", "diff"): frozenset(),
    ("run", "list"): frozenset({"--json", "--limit", "--branch"}),
    ("run", "view"): frozenset({"--json", "--log", "--log-failed", "--job"}),
    ("repo", "list"): frozenset({"--json", "--limit", "--no-archived"}),
    ("auth", "status"): frozenset(),
    ("project", "item-list"): frozenset({"--owner", "--format", "--limit"}),   # Central de acompanhamento (§7)
}
WRITE_VERBS = frozenset({"merge", "comment", "review", "rerun", "close", "reopen", "edit", "create", "delete", "ready",
                         "cancel", "lock", "unlock", "checkout", "api", "workflow", "secret", "variable", "release"})
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_ARG_RE = re.compile(r"^[A-Za-z0-9._/,:-]{1,200}$")

PR_FIELDS = ("number,title,author,isDraft,state,baseRefName,headRefName,headRefOid,mergeable,reviewDecision,"
             "statusCheckRollup,url,updatedAt")
PR_DETAIL_FIELDS = PR_FIELDS + ",body,comments"
RUN_FIELDS = "databaseId,displayTitle,workflowName,headBranch,headSha,status,conclusion,event,createdAt,url"
DIFF_LIMIT = 60_000
LOG_LIMIT = 200_000
# Os 8 repos do Plan2Space (D-VS-7 / VS-I2, UX-09): a lista padrão quando o `gh` não lista a org.
DEFAULT_REPOS = ("agent-bar", "agent-runtime", "claude-engineering-skills", "design-library", "engineering-specs",
                 "evidence", "sketchup-mcp", "sketchup-mcp-next")


class GhError(Exception):
    """Falha tipada: kind in {not_found, auth, refused, failed, timeout, bad_output}."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


def read_only_argv(sub: Sequence[str], repo: str | None, flags: Mapping[str, str | None] | None = None,
                   positional: Sequence[str] = ()) -> list[str]:
    """Monta o argv (sem o executavel). Recusa subcomando/flag fora da lista, verbo de escrita e repo invalido."""
    key = tuple(sub)
    if len(key) != 2 or any(v in WRITE_VERBS for v in key) or key not in READ_ONLY:
        raise GhError("refused", f"subcomando nao permitido (somente leitura): {' '.join(key)}")
    argv = [*key]
    for p in positional:
        if not _ARG_RE.match(str(p)) or str(p).startswith("-"):
            raise GhError("refused", f"argumento invalido: {p!r}")
        argv.append(str(p))
    if key not in (("auth", "status"), ("repo", "list"), ("project", "item-list")):
        if not repo or not _REPO_RE.match(repo):
            raise GhError("refused", f"--repo obrigatorio e valido (recebido {repo!r})")
        argv += ["--repo", repo]
    for flag, value in (flags or {}).items():
        if flag not in READ_ONLY[key]:
            raise GhError("refused", f"flag nao permitida em {' '.join(key)}: {flag}")
        argv.append(flag)
        if value is not None:
            if not _ARG_RE.match(str(value)):
                raise GhError("refused", f"valor invalido para {flag}: {value!r}")
            argv.append(str(value))
    return argv


def gh_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    src = os.environ if base is None else base
    return {k: v for k, v in src.items() if k.upper() not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}


Runner = Callable[[list[str], float], tuple[int, str, str]]


def subprocess_runner(argv: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, env=gh_env(), check=False,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise GhError("timeout", f"gh sem resposta em {timeout:.0f} s") from exc
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


@dataclass
class GhClient:
    exe: Sequence[str] = field(default_factory=lambda: _default_exe())
    runner: Runner = subprocess_runner
    timeout: float = 30.0
    calls: list[list[str]] = field(default_factory=list)                     # argvs efetivamente executados (auditoria)

    def _run(self, sub: Sequence[str], repo: str | None, flags: Mapping[str, str | None] | None = None,
             positional: Sequence[str] = ()) -> str:
        if not self.exe or not (shutil.which(self.exe[0]) or os.path.isfile(self.exe[0])):
            raise GhError("not_found", "gh nao encontrado no PATH")
        argv = [*self.exe, *read_only_argv(sub, repo, flags, positional)]
        self.calls.append(argv)
        rc, out, err = self.runner(argv, self.timeout)
        if rc != 0:
            low = err.lower()
            if "auth login" in low or "not logged" in low or "authentication" in low:
                raise GhError("auth", "gh sem login: rode `gh auth login` num terminal")
            raise GhError("failed", (err.strip() or f"gh saiu com {rc}")[:400])
        return out

    def _json(self, *a, **kw) -> Any:
        out = self._run(*a, **kw)
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise GhError("bad_output", "gh devolveu JSON invalido") from exc

    # ------------------------------------------------------------------------------------------------- leitura
    def auth_ok(self) -> bool:
        try:
            self._run(("auth", "status"), None)
            return True
        except GhError:
            return False

    def repos(self, org: str) -> list[str]:
        env = os.environ.get("AGENT_BAR_COCKPIT_REPOS", "").strip()
        if env:
            return configured_repos(org)
        try:
            data = self._json(("repo", "list"), None, {"--json": "name", "--limit": "50", "--no-archived": None},
                              positional=[org])
            names = sorted(str(d.get("name")) for d in data or [] if isinstance(d, dict) and d.get("name"))
            if names:
                return [f"{org}/{n}" for n in names]
        except GhError:
            pass
        return [f"{org}/{n}" for n in DEFAULT_REPOS]

    def prs(self, repo: str, limit: int = 30) -> list[dict]:
        return list(self._json(("pr", "list"), repo, {"--json": PR_FIELDS, "--limit": str(limit), "--state": "open"}) or [])

    def pr(self, repo: str, number: int) -> dict:
        return dict(self._json(("pr", "view"), repo, {"--json": PR_DETAIL_FIELDS}, positional=[str(int(number))]) or {})

    def pr_diff(self, repo: str, number: int, limit: int = DIFF_LIMIT) -> str:
        return truncate(self._run(("pr", "diff"), repo, positional=[str(int(number))]), limit)

    def runs(self, repo: str, branch: str = "", limit: int = 30) -> list[dict]:
        flags: dict[str, str | None] = {"--json": RUN_FIELDS, "--limit": str(limit)}
        if branch:
            flags["--branch"] = branch
        return list(self._json(("run", "list"), repo, flags) or [])

    def run_jobs(self, repo: str, run_id: int) -> list[dict]:
        data = self._json(("run", "view"), repo, {"--json": "jobs"}, positional=[str(int(run_id))]) or {}
        return list(data.get("jobs") or [])

    def run_log(self, repo: str, run_id: int, failed_only: bool = False, limit: int = LOG_LIMIT) -> str:
        flag = "--log-failed" if failed_only else "--log"
        return truncate(self._run(("run", "view"), repo, {flag: None}, positional=[str(int(run_id))]), limit)


def configured_repos(org: str) -> list[str]:
    """Repos sem rede: `AGENT_BAR_COCKPIT_REPOS` (separados por virgula, com ou sem a org) ou a lista padrao."""
    env = os.environ.get("AGENT_BAR_COCKPIT_REPOS", "").strip()
    names = [x.strip() for x in env.split(",") if x.strip()] if env else list(DEFAULT_REPOS)
    return [n if "/" in n else f"{org}/{n}" for n in names]


def _default_exe() -> list[str]:
    custom = os.environ.get("AGENT_BAR_GH", "").strip()
    if custom:
        return [custom]
    for cand in ("gh", r"C:\Program Files\GitHub CLI\gh.exe"):
        if shutil.which(cand) or os.path.isfile(cand):
            return [cand]
    return ["gh"]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncado: {len(text) - limit} caracteres omitidos]"


# ---------------------------------------------------------------------------------------------------- resumos


def checks_summary(rollup: object) -> str:
    """'3 ok · 1 falhou · 2 pendentes' a partir do statusCheckRollup."""
    ok = fail = pend = 0
    for c in rollup if isinstance(rollup, list) else []:
        if not isinstance(c, dict):
            continue
        state = str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        if state in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            ok += 1
        elif state in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"):
            fail += 1
        else:
            pend += 1
    parts = [f"{ok} ok"] if ok else []
    if fail:
        parts.append(f"{fail} falhou")
    if pend:
        parts.append(f"{pend} pendente{'s' if pend > 1 else ''}")
    return " · ".join(parts) or "sem checks"


def review_prompt(repo: str, pr: Mapping[str, Any], diff: str) -> str:
    """Prompt pronto da revisao assistida (R21): numero, HEAD sha40, base/head e o diff (ja truncado)."""
    return (f"Revise o PR {repo}#{pr.get('number')} — \"{pr.get('title', '')}\".\n"
            f"HEAD exato: {pr.get('headRefOid', '?')} · base {pr.get('baseRefName', '?')} ← {pr.get('headRefName', '?')}.\n"
            "Aponte bugs de correção primeiro (arquivo:linha e porquê), depois riscos e testes faltando. "
            "Não altere arquivos, não comente no GitHub e não faça commit: só a análise aqui.\n\n"
            f"```diff\n{diff}\n```")
