"""collectors.py — leitura SOMENTE-LEITURA do estado real. Sem Qt. Todo I/O externo entra por funcao injetavel (testavel).

Fontes vivas: sessoes Claude locais (tabela de processos + `claude auth status`), `gh` (login e fila de PRs da org), o
Project #2, o EventStore do agent-runtime (`EVENT_STORE_DB_PATH`), execucoes do motor, benchmark e aprovacoes. O
orquestrador legado (listener/event bridge, laco de revisao do Codex, passo FP-060) foi removido em 2026-11-22
(docs/audit/legacy-cleanup-2026-11-22.md). Ausencia de dado => GRAY/`indisponivel`, nunca verde.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_bar.models import (
    PR_STATE_ORDER,
    AgentStatus,
    Board,
    BoardItem,
    Level,
    PrQueue,
    PrRow,
    Snapshot,
)
from agent_bar.targets import DEFAULT_COLUMNS, Targets, load_targets

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows: subprocesso da barra nunca abre janela de console
@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str
    cmdline: str = ""
    created: str | None = None          # ISO UTC


def _now(now: dt.datetime | None) -> dt.datetime:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)


def parse_iso(text: object) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        v = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------------------------------------ I/O injetavel (real)
def list_processes(timeout: float = 15.0) -> list[Proc] | None:
    """Tabela de processos via CIM (Windows). None = indisponivel (nao e 'lista vazia')."""
    if os.name != "nt":
        return None
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,"
          "@{n='C';e={if($_.CreationDate){$_.CreationDate.ToUniversalTime().ToString('o')}}} | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        data = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if data is None:
        return None
    if isinstance(data, dict):
        data = [data]
    return [Proc(int(d["ProcessId"]), int(d["ParentProcessId"] or 0), str(d["Name"] or ""), str(d.get("CommandLine") or ""), d.get("C"))
            for d in data if isinstance(d, dict) and d.get("ProcessId") is not None]


def run_gh(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
    exe = shutil.which("gh") or "gh"
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stdout + r.stderr)).strip()


_LOGIN_CACHE: dict[str, tuple[float, bool | None]] = {}


def _run_cli(cmd: list[str]) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "GH_TOKEN")}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, creationflags=NO_WINDOW, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return r.returncode, (r.stdout + r.stderr).strip()


def _cached_login(name: str, clock: Callable[[], float], ttl: float, compute: Callable[[], bool | None]) -> bool | None:
    """Resultado do login com TTL (o CLI custa ~1 s): 5 min para um valor conhecido, 30 s para desconhecido (recupera logo depois de uma falha)."""
    hit = _LOGIN_CACHE.get(name)
    if hit and clock() - hit[0] < (ttl if hit[1] is not None else 30.0):
        return hit[1]
    value = compute()
    _LOGIN_CACHE[name] = (clock(), value)
    return value


def claude_login_state(run: Callable[[list[str]], tuple[int, str]] = _run_cli, clock: Callable[[], float] = time.monotonic,
                       which: Callable[[str], str | None] = shutil.which, ttl: float = 300.0) -> bool | None:
    """O Claude Code esta logado? `claude auth status` (JSON). `None` = nao deu para saber (sem CLI/falha): nunca vira "logado"."""
    exe = which("claude")
    if not exe:
        return None

    def compute() -> bool | None:
        rc, out = run([exe, "auth", "status"])
        if rc != 0:
            return None
        try:
            return bool(json.loads(out).get("loggedIn"))
        except (ValueError, AttributeError):
            return None
    return _cached_login("claude", clock, ttl, compute)


DESKTOP_RE = re.compile(r"--type=|AnthropicClaude|--startup", re.I)           # app Claude Desktop (Electron) e seus auxiliares
def short_cmd(cmd: str, n: int = 120) -> str:
    """Linha de comando resumida e sem segredos (tokens conhecidos redatados)."""
    c = re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+\S+)", "[REDACTED]", " ".join(cmd.split()))
    return c if len(c) <= n else c[: n - 1] + "…"


def is_claude(p: Proc) -> bool:
    return p.name.lower() in ("claude.exe", "claude")


def collect_claude(procs: list[Proc] | None, installed: Callable[[], bool] = lambda: bool(shutil.which("claude")),
                   login: bool | None = None) -> AgentStatus:
    """Claude Code nesta maquina. COR = conexao: verde logado, vermelho sem login, cinza sem CLI ou sem leitura.
    Sessoes interativas vivas e auxiliares do app Desktop aparecem no detalhe; nenhuma delas vira "trabalhando" por
    suposicao (nao ha fonte que diga o que uma sessao esta fazendo)."""
    if procs is None:
        return AgentStatus("Claude", Level.GRAY, "Indisponivel", "tabela de processos nao observavel")
    claudes = [p for p in procs if is_claude(p)]
    desktop = [p for p in claudes if DESKTOP_RE.search(p.cmdline)]
    sessions = [p for p in claudes if not DESKTOP_RE.search(p.cmdline)]
    extra = ""
    if sessions:
        extra += f"; {len(sessions)} sessao(oes) interativa(s) viva(s) (pid {', '.join(str(p.pid) for p in sessions[:3])})"
    if desktop:
        extra += f"; app Desktop: {len(desktop)} processo(s) auxiliar(es)"
    if not installed():
        return AgentStatus("Claude", Level.GRAY, "Nao instalado", "CLI nao encontrada" + extra)
    if login is False:
        return AgentStatus("Claude", Level.RED, "Sem login", "CLI sem credencial valida: rode `claude auth login`" + extra)
    if login is True:
        return AgentStatus("Claude", Level.GREEN, "Conectado", "logado (claude.ai)" + extra)
    if sessions:                                                              # vivo, mas o login nao foi confirmado: atencao, nunca verde
        return AgentStatus("Claude", Level.YELLOW, "Sessao interativa", "Claude Code vivo; login nao confirmado" + extra)
    return AgentStatus("Claude", Level.GRAY, "Login nao verificado", "CLI instalada; `claude auth status` sem resposta" + extra)


def collect_github_status(gh: Callable[[list[str]], tuple[int, str]] = run_gh, queue: PrQueue | None = None,
                          board: Board | None = None) -> AgentStatus:
    """GitHub alcançável = uma leitura REAL desta coleta deu certo (fila de PRs da org ou Project). `gh auth status` sozinho
    não prova acesso (com limite de API ele chega a declarar o token inválido) e só explica a falha quando nada foi lido."""
    q_ok = queue is not None and queue.error is None
    b_ok = board is not None and board.error is None
    if q_ok or b_ok:
        parts = [f"{len(queue.rows)} PR(s) abertos na org" if q_ok else f"fila de PRs indisponivel: {queue.error if queue else 'nao coletada'}"]
        parts.append("Project lido" if b_ok else f"Project indisponivel: {board.error if board else 'nao coletado'}")
        return AgentStatus("GitHub", Level.GREEN, "Conectado", "; ".join(parts))
    rc, out = gh(["auth", "status"])
    if rc == 127:
        return AgentStatus("GitHub", Level.GRAY, "Nao instalado", "gh nao encontrado")
    first = next((ln.strip(" -X") for ln in out.splitlines() if ln.strip() and "github.com" != ln.strip()), "")
    if rc != 0:
        return AgentStatus("GitHub", Level.RED, "Sem acesso", (first or "gh sem credencial") + "; nenhuma leitura desta coleta funcionou")
    return AgentStatus("GitHub", Level.YELLOW, "Logado, leituras falhando",
                       (queue.error if queue and queue.error else "sem leitura") + " (gh auth status ok)")


class BackgroundCache:
    """Como GithubCache, mas a coleta roda numa thread propria: `get` nunca espera. Sem valor ainda => `placeholder`;
    vencido => serve o anterior e atualiza em fundo. Uma coleta por vez. Falha => `on_error(exc)` vira o valor (nunca some calada)."""

    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic, placeholder: object = None,
                 spawn: Callable[[Callable[[], None]], object] | None = None, on_done: Callable[[], None] | None = None,
                 on_error: Callable[[BaseException], object] | None = None):
        self.ttl, self.clock, self.placeholder = ttl_s, clock, placeholder
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, name="agent-bar-background", daemon=True).start())
        self.on_done, self.on_error = on_done, on_error
        self._lock = threading.Lock()
        self._key, self._val, self._at, self._running = None, None, None, False

    def invalidate(self) -> None:
        with self._lock:
            self._at = None

    def get(self, key: str, fetch: Callable[[], object]) -> object:
        with self._lock:
            stale = self._val is None or self._key != key or self._at is None or self.clock() - self._at >= self.ttl
            start = stale and not self._running
            if start:
                self._running = True
            current = self._val
        if start:
            def run() -> None:
                try:
                    value = fetch()
                except Exception as exc:                                      # noqa: BLE001 - erro vira valor exibido
                    value = self.on_error(exc) if self.on_error else None
                with self._lock:
                    self._key, self._val, self._at, self._running = key, value, self.clock(), False
                if self.on_done:
                    self.on_done()
            self.spawn(run)
        return current if current is not None else self.placeholder


class GithubCache:
    """Cache com TTL para nao consultar o GitHub a cada ciclo local."""

    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.clock, self._at, self._val = ttl_s, clock, None, None
        self.last_good = None

    def invalidate(self) -> None:
        self._at, self._val = None, None

    def get(self, key: str, fetch: Callable[[], object]) -> object:
        now = self.clock()
        if self._at is not None and self._val is not None and self._val[0] == key and now - self._at < self.ttl:
            return self._val[1]
        v = fetch()
        self._at, self._val = now, (key, v)
        return v


BACKLOG_COLUMNS = DEFAULT_COLUMNS                                                # colunas do Plan2Space Project #2 (configuravel: targets.py)


BOARD_QUERY = ("query($after:String){organization(login:\"%s\"){projectV2(number:%d){id title url items(first:100,after:$after){"
               "totalCount pageInfo{hasNextPage endCursor} nodes{"
               "status:fieldValueByName(name:\"Status\"){... on ProjectV2ItemFieldSingleSelectValue{name}} "
               "task:fieldValueByName(name:\"Task ID\"){... on ProjectV2ItemFieldTextValue{text}} "
               "content{__typename ... on Issue{number title repository{nameWithOwner}} "
               "... on PullRequest{number title repository{nameWithOwner}} ... on DraftIssue{title}}}}}}}")


def _board_item(node: dict) -> BoardItem:
    status = ((node.get("status") or {}).get("name")) or "(sem status)"
    task = ((node.get("task") or {}).get("text")) or ""
    c = node.get("content") or {}
    if c.get("__typename") == "DraftIssue" or "number" not in c:
        ref = "rascunho"
    else:
        ref = f"{str((c.get('repository') or {}).get('nameWithOwner') or '?').split('/')[-1]}#{c.get('number')}"
    return BoardItem(task, str(c.get("title") or ""), status, ref)


def collect_board(gh: Callable[[list[str]], tuple[int, str]] = run_gh, targets: Targets | None = None,
                  now: dt.datetime | None = None, max_pages: int = 10) -> Board:
    """O Project v2 selecionado NO MOMENTO da consulta: nome, URL, id, Task ID e Status de cada item, 100 por pagina ate o fim.
    Qualquer falha (auth, sem acesso, GraphQL, resposta ilegivel, itens faltando) => `error` e contagens None. Nunca numero velho."""
    t = targets or load_targets()
    failed = Board(org=t.org, number=t.project, board=t.board, counts={k: None for k in t.columns})
    meta: dict = {}
    items: list[BoardItem] = []
    total = None
    after = None
    for _ in range(max_pages):
        args = ["api", "graphql", "-f", "query=" + BOARD_QUERY % (t.org, t.project)] + (["-f", f"after={after}"] if after else [])
        rc, out = gh(args)
        if rc != 0:
            first = (out.splitlines() or ["sem saida"])[0]
            hint = " (token sem escopo 'read:project'? rode: gh auth refresh -s read:project)" if "scope" in out.lower() else ""
            failed.error = f"gh api graphql rc={rc}: {first[:160]}{hint}"
            return failed
        try:
            data = json.loads(out)
            if data.get("errors"):
                failed.error = "GraphQL: " + str(data["errors"][0].get("message", "erro"))[:160]
                return failed
            proj = data["data"]["organization"]["projectV2"]
            if proj is None:
                failed.error = f"Project {t.org}/{t.project} nao encontrado ou sem acesso para este login"
                return failed
            meta = {"title": proj.get("title"), "url": proj.get("url"), "node_id": proj.get("id")}
            page = proj["items"]
            total = page.get("totalCount")
            items += [_board_item(n) for n in page["nodes"] if isinstance(n, dict)]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            failed.error = f"resposta do GraphQL ilegivel: {type(exc).__name__}: {exc}"[:200]
            return failed
    else:
        failed.error = f"Project com mais de {max_pages * 100} itens: contagem incompleta recusada"
        return failed
    if isinstance(total, int) and total != len(items):
        failed.error = f"resposta incompleta: {len(items)} de {total} itens; contagem recusada"
        return failed
    counts: dict[str, int | None] = {k: 0 for k in t.columns}
    for i in items:
        counts[i.status] = (counts.get(i.status) or 0) + 1                  # status fora das colunas: aparece, nao some
    return Board(org=t.org, number=t.project, board=t.board, total=len(items), counts=counts, items=items, error=None,
                 synced=_now(now).strftime("%Y-%m-%dT%H:%M:%SZ"), **meta)


def collect_backlog(gh: Callable[[list[str]], tuple[int, str]] = run_gh, owner: str = "Plan2Space", number: int = 2,
                    now: dt.datetime | None = None, max_pages: int = 10, columns: tuple[str, ...] = BACKLOG_COLUMNS
                    ) -> tuple[dict[str, int | None], str | None, str | None]:
    """(contagens por coluna, erro, sync ISO): visao resumida de `collect_board`. Itens sem status nao entram nas contagens."""
    b = collect_board(gh, Targets(org=owner, project=number, columns=columns), now=now, max_pages=max_pages)
    return {k: v for k, v in b.counts.items() if k != "(sem status)"}, b.error, b.synced


def _rollup_state(node: dict) -> str:
    """Estado do statusCheckRollup do HEAD na fila GraphQL ('' sem CI). Entra na chave do memo de aprovações."""
    commits = ((node.get("commits") or {}).get("nodes") or [{}])
    return str(((commits[-1].get("commit") or {}).get("statusCheckRollup") or {}).get("state") or "")


def classify_pr(node: dict) -> str:
    """O que precisa acontecer com este PR (so fatos do GitHub; nunca inventa): rascunho | conflito | ci | pronto | corrigir | aguarda.
    `pronto` exige APPROVED no HEAD ATUAL e checks SUCCESS: aprovacao de outro commit ou CI ausente/pendente nunca conta."""
    if node.get("isDraft"):
        return "rascunho"
    if node.get("mergeable") == "CONFLICTING":
        return "conflito"
    rollup = _rollup_state(node)
    if rollup in ("FAILURE", "ERROR"):
        return "ci"
    reviews = (node.get("reviews") or {}).get("nodes") or []
    last = reviews[-1] if reviews else None
    at_head = bool(last) and ((last.get("commit") or {}).get("oid") or "").lower() == str(node.get("headRefOid") or "x").lower()
    if at_head and last.get("state") == "APPROVED" and rollup == "SUCCESS":
        return "pronto"
    if at_head and last.get("state") == "CHANGES_REQUESTED":
        return "corrigir"
    return "aguarda"


ORG_PR_QUERY = ("query($q:String!,$after:String){search(query:$q,type:ISSUE,first:50,after:$after){pageInfo{hasNextPage endCursor} "
                "nodes{... on PullRequest{number title url isDraft mergeable headRefOid updatedAt repository{nameWithOwner} "
                "reviews(last:1){nodes{state commit{oid}}} commits(last:1){nodes{commit{statusCheckRollup{state}}}}}}}}")


def collect_org_pr_queue(gh: Callable[[list[str]], tuple[int, str]] = run_gh, owner: str = "Plan2Space",
                         now: dt.datetime | None = None, max_pages: int = 4) -> PrQueue:
    """PRs abertos de TODOS os repos da org numa busca GraphQL (50/pagina). Falha => erro, nunca lista vazia."""
    nodes: list[dict] = []
    after = None
    for _ in range(max_pages):
        args = ["api", "graphql", "-f", "query=" + ORG_PR_QUERY, "-f", f"q=org:{owner} is:pr is:open archived:false"] + (["-f", f"after={after}"] if after else [])
        rc, out = gh(args)
        if rc != 0:
            return PrQueue(error=f"gh api graphql rc={rc}: {(out.splitlines() or ['sem saida'])[0][:160]}")
        try:
            data = json.loads(out)
            if data.get("errors"):
                return PrQueue(error="GraphQL: " + str(data["errors"][0].get("message", "erro"))[:160])
            page = data["data"]["search"]
            nodes += [n for n in page["nodes"] if isinstance(n, dict) and isinstance(n.get("number"), int)]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return PrQueue(error=f"resposta do GraphQL ilegivel: {type(exc).__name__}: {exc}"[:200])
    else:
        return PrQueue(error=f"mais de {max_pages * 50} PRs abertos: contagem incompleta recusada")
    rows = [PrRow(int(n["number"]), str(n.get("title") or ""), str(n.get("url") or ""), classify_pr(n),
                  str((n.get("repository") or {}).get("nameWithOwner") or ""), head=str(n.get("headRefOid") or ""),
                  updated_at=str(n.get("updatedAt") or ""), checks=_rollup_state(n)) for n in nodes]
    rows.sort(key=lambda r: (PR_STATE_ORDER.index(r.state), r.repo, r.number))
    return PrQueue(rows=rows, synced=_now(now).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _approvals_for(pr_queue: PrQueue, runs, store_path, memo=None, gh=run_gh):
    """C2: aprovações da fila. Com o memo, o PR só é relido quando HEAD, updatedAt, estado do CI ou evidência mudam
    (a fila GraphQL já traz os três primeiros, sem gastar REST)."""
    from agent_bar import approvals as ap
    rows = [r for r in pr_queue.rows if r.repo and r.state != "rascunho"]
    refs = [(r.repo, r.number) for r in rows]
    versions = {(r.repo, r.number): (r.head, r.updated_at, r.checks) for r in rows if r.head}
    return ap.collect_approvals(gh, refs, runs=runs, store_path=store_path, memo=memo, versions=versions or None)


def collect_snapshot(procs_fn=list_processes, gh=run_gh, cache: GithubCache | None = None, backlog_cache: GithubCache | None = None,
                     now: dt.datetime | None = None, queue_cache: GithubCache | None = None,
                     claude_login: Callable[[], bool | None] | None = None, targets: Targets | None = None,
                     approval_cache: GithubCache | None = None, approval_memo=None) -> Snapshot:
    """Uma coleta completa. Chamar FORA da thread de UI."""
    n = _now(now)
    t = targets or load_targets()
    claude = collect_claude(procs_fn(), login=claude_login() if claude_login else None)
    bfetch = lambda: collect_board(gh, t, now=n)  # noqa: E731
    board = backlog_cache.get(f"board:{t.org}/{t.project}", bfetch) if backlog_cache else bfetch()     # falha = erro na tela, sem numero antigo
    counts = {k: v for k, v in board.counts.items() if k != "(sem status)"}
    qfetch = lambda: collect_org_pr_queue(gh, owner=t.org, now=n)  # noqa: E731
    pr_queue = queue_cache.get("pr_queue", qfetch) if queue_cache else qfetch()
    if queue_cache is not None:
        if pr_queue.error is None:
            queue_cache.last_good = pr_queue
        elif getattr(queue_cache, "last_good", None):                            # erro agora, mas ha leitura anterior: mostra a antiga E sinaliza
            pr_queue = PrQueue(rows=queue_cache.last_good.rows, error=pr_queue.error, synced=queue_cache.last_good.synced)
    gfetch = lambda: collect_github_status(gh, pr_queue, board)  # noqa: E731
    github = cache.get("gh_auth", gfetch) if cache else gfetch()
    from agent_bar.observability import collect_observation

    observation = collect_observation(now=n.timestamp())
    from agent_bar.runs import collect_runs, runs_dir_from_env

    runs = collect_runs(runs_dir_from_env())
    from agent_bar.benchmark import benchmark_from_env

    benchmark = benchmark_from_env()
    refs = [(r.repo, r.number) for r in pr_queue.rows if r.repo and r.state != "rascunho"]
    store = Path(observation.db_path) if observation.db_path else None
    afetch = lambda: _approvals_for(pr_queue, runs, store, memo=approval_memo, gh=gh)  # noqa: E731
    approvals = approval_cache.get("approvals:" + ",".join(f"{a}#{b}" for a, b in refs), afetch) if approval_cache else afetch()
    return Snapshot(at=n.isoformat(), claude=claude, github=github, backlog=counts, backlog_error=board.error,
                    backlog_synced=board.synced, observation=observation, pr_queue=pr_queue, runs=runs, board=board,
                    approvals=approvals, benchmark=benchmark)
