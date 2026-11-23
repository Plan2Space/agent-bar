"""workflows.py — fluxos de 5 etapas sobre uma SPEC do engineering-specs (F5, R24–R27). Sem Qt.

Um cliente ACP por etapa. Etapas marcadas `parallel_with_next` consecutivas formam um grupo que roda ao mesmo tempo; os
grupos rodam em sequencia e um grupo com erro (ou Parar) impede os seguintes. A etapa `pr` prepara commits e o texto do
PR, mas o app NUNCA da push nem abre PR (D6). `auto_approve` e desligado por padrao e, ligado, so escolhe `allow_once`.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_bar.cockpit.acp import (
    AcpClient,
    AcpError,
    AsyncRuntime,
    PermissionRequest,
    Update,
    mode_is_safe,
)
from agent_bar.cockpit.devmode import irreversible_request
from agent_bar.cockpit.library import git_sha
from agent_bar.cockpit.permission_inbox import AlreadyDecided, InboxClosed, PermissionInbox
from agent_bar.cockpit.store import atomic_write_json, data_dir

SPEC_LIMIT = 60_000
CONTEXT_LIMIT = 20_000
SUMMARY_LIMIT = 400
LOG_KEEP = 200
DEFAULT_SPECS = Path(r"E:\Claude\apps\engineering-specs")
STATUSES = ("pending", "running", "done", "error", "cancelled")

DEFAULT_STAGES: tuple[tuple[str, str, str], ...] = (
    ("spec", "Analisar a SPEC", "Leia a SPEC de referência e o código do repositório. NÃO altere nenhum arquivo. Liste lacunas, "
     "ambiguidades e riscos, e o que precisa de decisão humana."),
    ("plan", "Planejar", "Proponha o plano: arquivos a tocar, passos pequenos, testes que provam cada requisito e rollback. "
     "NÃO altere nenhum arquivo."),
    ("implement", "Implementar", "Implemente o plano com TDD: teste vermelho primeiro, depois o código. Rode lint e testes. "
     "NÃO faça commit."),
    ("review", "Revisar", "Compare o diff atual com a SPEC. Liste bloqueadores P0/P1 com arquivo:linha e porquê, e o que falta "
     "de teste. NÃO altere arquivos."),
    ("pr", "Preparar o PR", "Proponha os commits semânticos (mensagens) e o texto do PR com risco e evidência. NÃO faça push, "
     "NÃO abra PR e NÃO rode git commit: só o texto."),
)


@dataclass
class Stage:
    name: str
    desc: str = ""
    instructions: str = ""
    agent: str = ""                     # chave do perfil (profiles.py); vazio = o do fluxo
    mode: str = ""                      # modo ACP; vazio = o padrao do agente (pede permissao)
    skill_keys: list[str] = field(default_factory=list)
    parallel_with_next: bool = False
    status: str = "pending"
    summary: str = ""
    log: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    def add_log(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > LOG_KEEP:
            del self.log[:-LOG_KEEP]


@dataclass
class Workflow:
    id: str
    title: str
    spec_path: str = ""                 # relativo ao checkout do engineering-specs
    spec_sha: str = ""
    workspace: str = ""
    branch: str = ""
    agent: str = ""
    auto_approve: bool = False
    stages: list[Stage] = field(default_factory=list)
    updated_at: float = 0.0

    @classmethod
    def new(cls, title: str, workspace: str = "", agent: str = "") -> Workflow:
        return cls(id=uuid.uuid4().hex[:12], title=title, workspace=workspace, agent=agent,
                   stages=[Stage(n, d, i) for n, d, i in DEFAULT_STAGES])

    @classmethod
    def from_dict(cls, d: dict) -> Workflow:
        stages = [Stage(**{k: v for k, v in s.items() if k in Stage.__dataclass_fields__})
                  for s in d.get("stages") or [] if isinstance(s, dict)]
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "stages"}
        return cls(**known, stages=stages)


def stage_groups(stages: Sequence[Stage]) -> list[list[int]]:
    """Indices agrupados: etapas com `parallel_with_next` consecutivas ficam no mesmo grupo que a seguinte."""
    groups: list[list[int]] = []
    current: list[int] = []
    for i, st in enumerate(stages):
        current.append(i)
        if not st.parallel_with_next or i == len(stages) - 1:
            groups.append(current)
            current = []
    return groups


def truncate(text: str, limit: int, what: str) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… [{what} truncado: {len(text) - limit} caracteres omitidos]"


def stage_prompt(wf: Workflow, stage: Stage, spec_text: str, context: str = "", previous: Sequence[Stage] = ()) -> str:
    """Prompt da etapa: cabecalho (repo, branch, etapa) + SPEC (caminho, SHA, truncada) + instrucoes + contexto + resumos."""
    prev = "\n".join(f"- {p.name}: {p.summary}" for p in previous if p.summary)
    parts = [
        f"Fluxo \"{wf.title}\" · etapa {stage.name} ({stage.desc}) · workspace {wf.workspace} · branch {wf.branch or '(atual)'}",
        f"SPEC de referência: engineering-specs/{wf.spec_path} @ {wf.spec_sha or '(sha indisponível)'}",
        "```markdown\n" + truncate(spec_text, SPEC_LIMIT, "SPEC") + "\n```",
        "Instruções desta etapa:\n" + stage.instructions,
    ]
    if prev:
        parts.append("Resumo das etapas anteriores:\n" + prev)
    if context:
        parts.append(truncate(context, CONTEXT_LIMIT, "contexto"))
    return "\n\n".join(parts)


def spec_files(specs_root: Path | None = None) -> list[str]:
    root = specs_root or Path(os.environ.get("AGENT_BAR_COCKPIT_SPECS") or DEFAULT_SPECS)
    if not root.is_dir():
        return []
    out = [p.relative_to(root).as_posix() for p in root.rglob("*.md")
           if ".git" not in p.parts and p.name not in ("README.md", "CLAUDE.md", "AGENTS.md")]
    return sorted(out)


def load_spec(rel: str, specs_root: Path | None = None) -> tuple[str, str]:
    """(texto, sha40 do checkout) da SPEC `rel`; recusa caminho fora do checkout."""
    root = (specs_root or Path(os.environ.get("AGENT_BAR_COCKPIT_SPECS") or DEFAULT_SPECS)).resolve()
    path = (root / rel).resolve()
    if root not in path.parents or path.suffix.lower() != ".md":
        raise ValueError(f"SPEC fora do engineering-specs: {rel!r}")
    return path.read_text(encoding="utf-8"), git_sha(root)


class WorkflowStore:
    def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = (root or data_dir()) / "workflows.json"
        self.clock = clock

    def load_all(self) -> list[Workflow]:
        try:
            import json
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [Workflow.from_dict(d) for d in data.get("workflows", []) if isinstance(d, dict)]

    def save(self, wf: Workflow) -> None:
        wf.updated_at = self.clock()
        others = [w for w in self.load_all() if w.id != wf.id]
        atomic_write_json(self.path, {"version": 1, "workflows": [asdict(w) for w in [wf, *others]]})


# --------------------------------------------------------------------------------------------------------- runner

Event = dict[str, Any]
ClientFactory = Callable[..., AcpClient]


class WorkflowRunner:
    """Executa um fluxo no runtime asyncio dado. `on_event` recebe dicts {type, stage, ...} (chamado na thread asyncio).
    `on_permission(stage_index, req, respond)` so e usado com auto_approve desligado."""

    def __init__(self, wf: Workflow, *, runtime: AsyncRuntime, argv_for: Callable[[Stage], Sequence[str]],
                 spec_text: str, context_for: Callable[[Stage], str] = lambda s: "",
                 on_event: Callable[[Event], None] = lambda e: None,
                 on_permission: Callable[[int, PermissionRequest, Callable[[str | None], None]], None] | None = None,
                 client_factory: ClientFactory = AcpClient, stderr_dir: Path | None = None,
                 confirmed_modes: Collection[tuple[str, str]] = (), inbox: PermissionInbox | None = None) -> None:
        self.wf = wf
        self.confirmed_modes = {(str(n), str(m)) for n, m in confirmed_modes}   # (etapa, modo) que o Felipe aceitou
        self.inbox = inbox                                                   # todo pedido dos Fluxos tambem vai ao Inbox
        self.rt = runtime
        self.argv_for = argv_for
        self.spec_text = spec_text
        self.context_for = context_for
        self.on_event = on_event
        self.on_permission = on_permission
        self.client_factory = client_factory
        self.stderr_dir = stderr_dir
        self.clients: dict[int, AcpClient] = {}
        self.cancelled = False

    def start(self):
        return self.rt.submit(self._run())

    def stop(self):
        """Parar: cancela todas as etapas em andamento e encerra os processos (R26)."""
        return self.rt.submit(self._stop())

    def _emit(self, **event: Any) -> None:
        try:
            self.on_event(event)
        except Exception:                                                        # noqa: BLE001 - UI nao derruba o fluxo
            pass

    def _set(self, i: int, status: str, **extra: Any) -> None:
        st = self.wf.stages[i]
        st.status = status
        st.updated_at = time.time()
        self._emit(type="stage", stage=i, status=status, t=time.monotonic(), **extra)

    async def _stop(self) -> None:
        self.cancelled = True
        clients = list(self.clients.values())
        for c in clients:
            try:
                await asyncio.wrap_future(c.cancel())
            except AcpError:
                pass
        await asyncio.gather(*(asyncio.wrap_future(c.close(2.0)) for c in clients), return_exceptions=True)

    async def _run(self) -> str:
        for st in self.wf.stages:
            if st.status != "done":
                st.status, st.summary, st.log = "pending", "", []
        result = "done"
        for group in stage_groups(self.wf.stages):
            if self.cancelled:
                result = "cancelled"
                break
            outcomes = await asyncio.gather(*(self._stage(i) for i in group if self.wf.stages[i].status != "done"))
            if self.cancelled:
                result = "cancelled"
                break
            if any(o == "error" for o in outcomes):
                result = "error"
                break
        for st_i, st in enumerate(self.wf.stages):
            if st.status in ("pending", "running"):
                self._set(st_i, "cancelled" if result == "cancelled" else "pending")
        self._emit(type="workflow", status=result)
        return result

    def _check_stage_mode(self, st: Stage, modes: dict) -> None:
        """H-011: modo de etapa so vale se o agente o anuncia e, se decide sozinho (auto, acceptEdits, bypass...),
        se o Felipe o confirmou para ESTA etapa ao iniciar o fluxo. Bypass anularia R27/R31 (aprovacao automatica so
        allow_once; irreversivel nunca sozinho), entao sem confirmacao a etapa falha em vez de rodar."""
        announced = {str(m.get("id", "")) for m in modes.get("availableModes") or [] if isinstance(m, dict)}
        if st.mode not in announced:
            raise AcpError("RPC", f"modo {st.mode!r} da etapa nao e anunciado pelo agente")
        if not mode_is_safe(st.mode, modes) and (st.name, st.mode) not in self.confirmed_modes:
            raise AcpError("RPC", f"modo {st.mode!r} decide sem pedir permissao: recusado sem confirmacao explicita")

    def permission_handler(self, i: int, st: Stage) -> Callable[[PermissionRequest, Callable[[str | None], None]], None]:
        """Decide cada pedido de permissao da etapa: irreversivel nunca e aprovado sozinho (R31); auto_approve so
        escolhe allow_once (R27); sem auto_approve, vai ao callback da UI (ou e recusado)."""

        def on_perm(req: PermissionRequest, raw_respond: Callable[[str | None], None]) -> None:
            title = (req.tool_call or {}).get("title", "ferramenta")
            danger = irreversible_request(req.tool_call or {})
            entry = self._record(st, req)
            if entry is False:                                                # Inbox fechado: Studio encerrando
                raw_respond(None)
                return
            if entry is not None and entry.status != "PENDING":               # politica ja decidiu (proibido/regra)
                choice = None if entry.status == "DENIED" else req.strongest_once()
                st.add_log(f"permissão {entry.status.lower()} por {entry.decided_by}: {title}")
                self._emit(type="log", stage=i, line=st.log[-1])
                raw_respond(choice)
                return

            def respond(choice: str | None, by: str = "human") -> None:      # grava a decisao antes de responder
                self._decide(entry, req, choice, by)
                raw_respond(choice)
            if self.wf.auto_approve and danger:
                st.add_log(f"permissão automática RECUSADA (irreversível: {danger}): {title}")
                self._emit(type="log", stage=i, line=st.log[-1])
                if self.on_permission is not None:
                    self.on_permission(i, req, respond)
                else:
                    respond(None)
            elif self.wf.auto_approve:
                choice = req.strongest_once()
                st.add_log(f"permissão automática ({'allow_once' if choice else 'sem allow_once: recusada'}): {title}")
                self._emit(type="log", stage=i, line=st.log[-1])
                respond(choice, "fluxo:auto_approve")
            elif self.on_permission is not None:
                self.on_permission(i, req, respond)
            else:
                respond(None, "fluxo: ninguem para responder")

        return on_perm

    def _record(self, st: Stage, req: PermissionRequest):
        """Entrada do Inbox (None = sem Inbox; False = Inbox fechado)."""
        if self.inbox is None:
            return None
        try:
            return self.inbox.record(req.tool_call or {}, provider=f"fluxo:{self.wf.title}:{st.name}",
                                     session_id=req.session_id, workspace=self.wf.workspace,
                                     mode_decides_alone=bool(st.mode) and st.mode not in ("default", "plan"),
                                     auto_mode=self.wf.auto_approve)
        except InboxClosed:
            return False

    def _decide(self, entry, req: PermissionRequest, choice: str | None, by: str) -> None:
        if not entry:
            return
        kind = next((str(o.get("kind")) for o in req.options if str(o.get("optionId")) == str(choice)), None)
        try:
            self.inbox.decide(entry.id, (kind or choice) if choice else None, by=by)
        except (InboxClosed, AlreadyDecided, PermissionError):
            pass                                                             # a resposta ao agente vem primeiro

    async def _stage(self, i: int) -> str:
        st = self.wf.stages[i]
        previous = [s for s in self.wf.stages[:i] if s.status == "done"]
        prompt = stage_prompt(self.wf, st, self.spec_text, self.context_for(st), previous)
        text: list[str] = []
        titles: dict[str, str] = {}

        def on_update(u: Update) -> None:
            if u.kind == "agent_message_chunk":
                text.append(u.text)
            elif u.kind in ("tool_call", "tool_call_update"):
                tid = str(u.data.get("toolCallId", ""))
                if u.data.get("title"):
                    titles[tid] = str(u.data["title"])
                line = f"[{u.data.get('status', '')}] {titles.get(tid, tid)}"
                if self.inbox is not None and u.data.get("status") in ("completed", "failed"):
                    try:
                        self.inbox.finish_tool_call(u.session_id, tid, str(u.data["status"]))
                    except InboxClosed:
                        pass
                st.add_log(line)
                self._emit(type="log", stage=i, line=line)

        client = self.client_factory(list(self.argv_for(st)), self.wf.workspace, runtime=self.rt, on_update=on_update,
                                     on_permission=self.permission_handler(i, st),
                                     stderr_path=(self.stderr_dir / f"wf-{self.wf.id}-{st.name}.stderr.log") if self.stderr_dir else None)
        self.clients[i] = client
        self._set(i, "running")
        try:
            await asyncio.wrap_future(client.connect())
            if st.mode:
                self._check_stage_mode(st, client.modes)
                await asyncio.wrap_future(client.set_mode(st.mode))
            reason = await asyncio.wrap_future(client.prompt(prompt))
            body = "".join(text).strip()
            st.summary = body[-SUMMARY_LIMIT:]
            status = "cancelled" if reason == "cancelled" or self.cancelled else "done"
            st.add_log(f"fim: {reason}")
        except AcpError as exc:
            st.summary = f"erro: {exc.code}: {exc.message}"[:SUMMARY_LIMIT]
            st.add_log(st.summary)
            status = "cancelled" if self.cancelled else "error"
        finally:
            try:
                await asyncio.wrap_future(client.close(2.0))
            except AcpError:
                pass
            self.clients.pop(i, None)
        self._set(i, status, summary=st.summary)
        return status


SAFE_STAGE_MODES = ("", "default", "plan")                                    # pedem permissao ou so planejam


def risky_stage_modes(wf: Workflow) -> list[tuple[str, str]]:
    """(etapa, modo) que vao precisar de confirmacao explicita antes de o fluxo rodar (H-011)."""
    return [(st.name, st.mode) for st in wf.stages if st.mode not in SAFE_STAGE_MODES]
