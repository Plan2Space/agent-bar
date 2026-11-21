"""models.py — estado tipado da barra de agentes (FP-058, refs FP-057/#589). Sem Qt, sem I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_bar.approvals import ApprovalBoard
from agent_bar.observability import Observation
from agent_bar.runs import BenchmarkStatus, RunsView, benchmark_status


class Level(str, Enum):
    GREEN = "green"      # operacional / trabalhando normalmente
    BLUE = "blue"        # executando tarefa
    YELLOW = "yellow"    # aguardando, ocioso, dependencia pendente, estado ambiguo
    RED = "red"          # erro ou interrupcao inesperada
    GRAY = "gray"        # desligado, desconhecido, nao configurado ou nao integrado (NUNCA sucesso)


@dataclass(frozen=True)
class AgentStatus:
    name: str
    level: Level
    label: str                      # curto: aparece na barra/tooltip
    detail: str = ""                # uma linha de evidencia (de onde veio o dado)
    pid: int | None = None
    since: str | None = None        # ISO UTC do inicio do processo, quando conhecido
    command: str = ""               # linha de comando resumida do processo atribuido (sem segredos)

    def line(self) -> str:
        pid = f" pid {self.pid}" if self.pid else ""
        return f"{self.name}: {self.label}{pid}" + (f" - {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class Event:
    at: str
    action: str
    item: str
    reason: str = ""


@dataclass(frozen=True)
class GroupedEvent:
    """Eventos consecutivos identicos (mesma acao/item/motivo) viram UMA linha com contagem (sem spam de DRY_RUN)."""
    first_at: str
    last_at: str
    action: str
    item: str
    reason: str
    count: int
    error: bool

    def pr_label(self) -> str:
        parts = self.item.split(":")
        return f"PR #{parts[2]}" if len(parts) > 2 and parts[2].isdigit() else self.item


PR_STATE_ORDER = ("pronto", "corrigir", "conflito", "ci", "aguarda", "rascunho")      # o que pede acao vem primeiro


@dataclass(frozen=True)
class PrRow:
    number: int
    title: str
    url: str
    state: str                       # um de PR_STATE_ORDER
    repo: str = ""                    # owner/name quando a fila cobre a org inteira


@dataclass(frozen=True)
class BoardItem:
    task_id: str
    title: str
    status: str
    ref: str                         # repo#n, ou "rascunho" para DraftIssue


@dataclass
class Board:
    """Leitura do Project v2 NO MOMENTO da consulta. `error` preenchido => contagens None e itens vazios (nunca numero antigo)."""
    org: str = ""
    number: int = 0
    board: str = "rebuild"
    title: str | None = None
    url: str | None = None
    node_id: str | None = None
    total: int | None = None
    counts: dict[str, int | None] = field(default_factory=dict)
    items: list[BoardItem] = field(default_factory=list)
    error: str | None = "nao coletado"
    synced: str | None = None


@dataclass
class PrQueue:
    """Fila de PRs abertos do repositorio. `error` preenchido = leitura falhou: a lista vazia NAO significa "sem PRs"."""
    rows: list[PrRow] = field(default_factory=list)
    error: str | None = None
    synced: str | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.state] = out.get(r.state, 0) + 1
        return {k: out[k] for k in PR_STATE_ORDER if k in out}


@dataclass
class Snapshot:
    at: str
    claude: AgentStatus
    codex: AgentStatus
    listener: AgentStatus
    github: AgentStatus
    gpt: AgentStatus = field(default_factory=lambda: AgentStatus(
        "GPT", Level.GRAY, "Indisponível", "Nenhuma fonte de estado GPT contratada pelo Harness"))
    observation: Observation = field(default_factory=Observation)
    pr_queue: PrQueue = field(default_factory=lambda: PrQueue(error="nao coletada"))
    runs: RunsView = field(default_factory=lambda: RunsView("sem_fonte", "nao coletado"))
    benchmark: BenchmarkStatus = field(default_factory=benchmark_status)
    board: Board = field(default_factory=Board)
    approvals: ApprovalBoard = field(default_factory=lambda: ApprovalBoard(error='nao coletado'))
    pr: int | None = None
    pr_url: str | None = None
    mode: str = "indisponivel"                         # DRY_RUN | EXECUTE | indisponivel
    heartbeat_age_s: int | None = None
    kill_switch: bool | None = None                    # None = nao observavel
    concurrent_instances: int | None = None
    lock_holder: str | None = None
    lock_status: str = "indisponivel"                  # correlacao do lock com processos reais
    last_error: str | None = None
    events: list[GroupedEvent] = field(default_factory=list)
    backlog: dict[str, int | None] = field(default_factory=dict)
    backlog_error: str | None = None
    backlog_synced: str | None = None                  # ISO UTC da ultima leitura bem-sucedida
    github_checks: str = "indisponivel"
    executor_summary: str = "indisponivel"             # fila/tentativas/execucoes 24 h
    work: str = "indisponivel"                         # "monitorando" | "trabalhando" | ...
    review_ref: str = ""                               # review/SHA atual da fila
    log_path: str | None = None

    def agents(self) -> list[AgentStatus]:
        return [self.claude, self.codex, self.gpt, self.listener, self.github]

    def mode_banner(self) -> str:
        if self.mode == "DRY_RUN":
            return "DRY_RUN — execução automática desligada"
        if self.mode == "EXECUTE":
            return "EXECUTE — execução automática LIGADA"
        return "modo indisponível"

    def diagnostic(self) -> str:
        """Texto para a area de transferencia. Sem segredos: so estado observado."""
        def n(v, suffix=""):
            return "indisponivel" if v is None else f"{v}{suffix}"
        rows = [f"Agent bar snapshot {self.at}"]
        rows += [a.line() + (f" | cmd: {a.command}" if a.command else "") + (f" | inicio: {a.since}" if a.since else "") for a in self.agents()]
        rows += [f"modo: {self.mode_banner()}", f"trabalho: {self.work}", f"PR atual: {self.pr if self.pr else 'indisponivel'}",
                 f"review/SHA: {self.review_ref or 'indisponivel'}", f"checks: {self.github_checks}",
                 f"heartbeat: {n(self.heartbeat_age_s, 's')}",
                 f"kill switch: {'indisponivel' if self.kill_switch is None else ('ATIVO' if self.kill_switch else 'inativo')}",
                 f"instancias do executor: {n(self.concurrent_instances)}", f"lock: {self.lock_holder or 'indisponivel'} ({self.lock_status})",
                 f"executor: {self.executor_summary}",
                 "backlog: " + (", ".join(f"{k}={n(v)}" for k, v in self.backlog.items()) if self.backlog else "indisponivel")
                 + (f" (erro: {self.backlog_error})" if self.backlog_error else f" (sync {self.backlog_synced or 'nunca'})"),
                 f"Project: {self.board.title or '?'} · {self.board.url or '?'} · {self.board.node_id or '?'}"
                 + (" (LEGADO)" if self.board.board == "legacy" else "") + (f" · erro: {self.board.error}" if self.board.error else f" · {self.board.total} itens"),
                 *[f"  {i.task_id or '-'} {i.status} {i.ref}" for i in self.board.items],
                 "PRs abertos: " + (f"indisponivel (erro: {self.pr_queue.error})" if self.pr_queue.error and not self.pr_queue.rows else
                                    ", ".join(f"{r.repo or '?'}#{r.number}={r.state}" for r in self.pr_queue.rows) or "nenhum")
                 + (f" (sync {self.pr_queue.synced})" if self.pr_queue.synced else ""),
                 f"ultimo erro: {self.last_error or 'nenhum observado'}", f"log: {self.log_path or 'indisponivel'}"]
        rows += [f"  {e.last_at} {e.action} {e.pr_label()} x{e.count} {e.reason}".rstrip() for e in self.events[:10]]
        rows += [f"Event Store: {self.observation.source} ({self.observation.reason})",
                 f"fila: {self.observation.counts}",
                 f"mensagens: {self.observation.messages_status}",
                 f"execucoes do motor: {self.runs.state} ({self.runs.reason})"]
        rows += [f"  {r.run_id} {r.status} verdict={r.verdict or 'sem relatorio'} visual={r.visual_verdict or '?'} HEAD={r.engine_commit[:12] or '?'}"
                 + (f" problemas: {'; '.join(r.problems)}" if r.problems else " evidencia conferida (sha256)") for r in self.runs.runs[:5]]
        rows += [f"benchmark: {self.benchmark.state} ({self.benchmark.reason})"]
        return "\n".join(rows)
