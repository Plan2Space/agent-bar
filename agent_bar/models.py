"""models.py — estado tipado da barra de agentes (FP-058, refs FP-057/#589). Sem Qt, sem I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agent_bar.approvals import ApprovalBoard
from agent_bar.benchmark import BenchmarkView
from agent_bar.observability import Observation
from agent_bar.runs import RunsView


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
    github: AgentStatus
    observation: Observation = field(default_factory=Observation)
    pr_queue: PrQueue = field(default_factory=lambda: PrQueue(error="nao coletada"))
    runs: RunsView = field(default_factory=lambda: RunsView("sem_fonte", "nao coletado"))
    benchmark: BenchmarkView = field(default_factory=lambda: BenchmarkView('sem_fonte', 'nao coletado'))
    board: Board = field(default_factory=Board)
    approvals: ApprovalBoard = field(default_factory=lambda: ApprovalBoard(error='nao coletado'))
    backlog: dict[str, int | None] = field(default_factory=dict)
    backlog_error: str | None = None
    backlog_synced: str | None = None                  # ISO UTC da ultima leitura bem-sucedida

    def agents(self) -> list[AgentStatus]:
        return [self.claude, self.github]

    def diagnostic(self) -> str:
        """Texto para a area de transferencia. Sem segredos: so estado observado."""
        def n(v, suffix=""):
            return "indisponivel" if v is None else f"{v}{suffix}"
        rows = [f"P2S Studio snapshot {self.at}"]
        rows += [a.line() + (f" | cmd: {a.command}" if a.command else "") + (f" | inicio: {a.since}" if a.since else "") for a in self.agents()]
        rows += ["backlog: " + (", ".join(f"{k}={n(v)}" for k, v in self.backlog.items()) if self.backlog else "indisponivel")
                 + (f" (erro: {self.backlog_error})" if self.backlog_error else f" (sync {self.backlog_synced or 'nunca'})"),
                 f"Project: {self.board.title or '?'} · {self.board.url or '?'} · {self.board.node_id or '?'}"
                 + (f" · erro: {self.board.error}" if self.board.error else f" · {self.board.total} itens"),
                 *[f"  {i.task_id or '-'} {i.status} {i.ref}" for i in self.board.items],
                 "PRs abertos: " + (f"indisponivel (erro: {self.pr_queue.error})" if self.pr_queue.error and not self.pr_queue.rows else
                                    ", ".join(f"{r.repo or '?'}#{r.number}={r.state}" for r in self.pr_queue.rows) or "nenhum")
                 + (f" (sync {self.pr_queue.synced})" if self.pr_queue.synced else "")]
        rows += [f"Event Store: {self.observation.source} ({self.observation.reason})",
                 f"fila: {self.observation.counts}",
                 f"mensagens: {self.observation.messages_status}",
                 f"execucoes do motor: {self.runs.state} ({self.runs.reason})"]
        rows += [f"  {r.run_id} {r.status} verdict={r.verdict or 'sem relatorio'} visual={r.visual_verdict or '?'} HEAD={r.engine_commit[:12] or '?'}"
                 + (f" problemas: {'; '.join(r.problems)}" if r.problems else " evidencia conferida (sha256)") for r in self.runs.runs[:5]]
        rows += [f"benchmark: {self.benchmark.state} ({self.benchmark.reason}; commit de origem {self.benchmark.origin_commit or 'nao informado'})"]
        rows += [f"  {b.backend}: sucesso {b.success}/{b.runs} visiveis {b.visible_pass} ocultos {b.hidden_pass} recomendacao {b.recommendation}"
                 f" result.json lidos {len(b.results)}" + (f" problemas: {'; '.join(b.problems)}" if b.problems else "") for b in self.benchmark.backends]
        return "\n".join(rows)
