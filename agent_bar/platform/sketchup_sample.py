"""sketchup_sample.py — a UNICA ferramenta do executor local: `build_room_sample` (P2S-LOCAL-EXECUTOR-SKETCHUP-POC).

Constroi uma amostra de comodo pelo ponto de entrada ja testado do motor `sketchup-mcp-next`
(`python -m p2s_engine.run <fixture> --out-dir --run-id` = a mesma funcao `run.build_room` que o MCP `p2s-engine`
expoe como `build_room`): SketchUp 2026 constroi, salva, fecha o proprio PID, reabre e o inspetor independente mede.

Fronteiras (SPEC §3-§5):
- o modelo so escolhe `fixture_ref` numa allowlist; caminho, URL, workspace e run_id NUNCA vem do modelo;
- `request_id` e gerado aqui (sessao + turno + digest da chamada) e grava num registro duravel ANTES do motor:
  o mesmo pedido nunca dispara um segundo run; pedido `running`/`unknown` e reconciliado pelo run_dir, nao re-executado;
- permissao antes de efeito, pelo mesmo canal do ACP (`on_permission(PermissionRequest, respond)`), so com
  `allow_once`/`reject_once`; recusar, fechar ou vencer o prazo = zero chamada ao motor;
- a versao (sha256) da fixture mostrada na permissao e conferida de novo antes do motor: divergiu, nao roda;
- saida so sob `out_root` (area isolada, nunca `artifacts/` do motor), uma execucao em voo por vez, e nao abre o
  SketchUp se outro SketchUp ja estiver rodando (o slot e compartilhado com outras sessoes);
- `completed` so com run_manifest OK + .skp presente, nao vazio e com o sha256 do manifest + veredito PASS do inspetor
  no arquivo REABERTO. Qualquer outra coisa e `failed` (ou `unknown` se o motor nao terminou) com erro tipado.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_bar.platform.local_tools import ToolCall, ToolContext

TOOL_NAME = "build_room_sample"
ALLOWED_FIXTURES = {                                                         # ref -> caminho versionado no motor
    "room_001": "tests/fixtures/rooms/room_001.json",
    "room_alt_5x3": "tests/fixtures/rooms/room_alt_5x3.json",
    "plan_alt_5x3_e3a": "tests/fixtures/furnishing/plan_alt_5x3.e3a.furnishing.json",   # LAB E3a: + cama (render)
}
CORE_FIXTURES = ("room_001", "room_alt_5x3")                                 # sem elas o executor nao esta pronto
OPTIONS = ({"optionId": "allow_once", "name": "Permitir uma vez", "kind": "allow_once"},
           {"optionId": "reject_once", "name": "Recusar", "kind": "reject_once"})
STATUSES = ("accepted", "running", "completed", "failed", "unknown", "refused")


_ENGINE_LOCK = threading.Lock()                                              # uma amostra em voo por processo (todas as abas)


@dataclass(frozen=True)
class EngineExit:
    returncode: int | None                                                   # None = nao terminou no prazo
    pid: int | None


Runner = Callable[..., EngineExit]                                           # (argv, cwd, env, timeout_s, log, on_start)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def filtered_env(base: Mapping[str, str]) -> dict[str, str]:
    """Mesma lista permitida do ACP (`isolated_env`, falha fechado: nada com TOKEN/KEY/SECRET passa) + o que o motor
    usa: P2S_SKETCHUP_EXE (SketchUp fora do caminho padrao)."""
    from agent_bar.cockpit.acp import isolated_env
    out = isolated_env(base, allow=("COMPUTERNAME", "PROGRAMW6432", "P2S_SKETCHUP_EXE"))
    out["PYTHONUTF8"] = "1"
    return out


def run_engine(argv: list[str], cwd: Path, env: dict, timeout_s: float, log_path: Path,
               on_start: Callable[[int, float | None], None] | None = None) -> EngineExit:
    """Sobe o motor como processo filho (sem shell), saida inteira no log. Nao mata no prazo: o motor fecha o proprio
    SketchUp pelo PID; matar o Python deixaria o SketchUp orfao. Prazo vencido = `unknown`, reconciliado depois."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=flags)
        if on_start is not None:                                             # identidade gravada antes de esperar
            from agent_bar.platform.proc import creation_time
            on_start(proc.pid, creation_time(proc.pid))
        try:
            return EngineExit(proc.wait(timeout=timeout_s), proc.pid)
        except subprocess.TimeoutExpired:
            return EngineExit(None, proc.pid)


def sketchup_running() -> bool:
    """Ha algum SketchUp.exe vivo nesta maquina (de outra sessao)? Saida do tasklist em bytes: sem decode de codepage."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SketchUp.exe", "/FO", "CSV", "/NH"], capture_output=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return True                                                          # sem como conferir: falha fechado
    return b'"sketchup.exe"' in out.lower()


def engine_alive(pid: int, created: float | None) -> bool:
    """O processo do motor daquele pedido ainda vive? PID + instante de criacao (o Windows reaproveita PID)."""
    from agent_bar.platform.proc import same_process
    return same_process(pid, created)


def git_head(root: Path) -> dict | None:
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    sha = head.stdout.decode("ascii", "replace").strip()
    if head.returncode != 0 or len(sha) != 40:
        return None
    return {"commit": sha, "dirty": bool(dirty.stdout.strip())}


# ------------------------------------------------------------------------------------------------ registro duravel
class RequestRegistry:
    """request_id -> run. SQLite em `out_root/_studio/requests.sqlite`: sobrevive a crash e a reinicio do Studio."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL, session_id TEXT NOT NULL,
            turn INTEGER NOT NULL, call_digest TEXT NOT NULL, fixture_ref TEXT NOT NULL, fixture_hash TEXT NOT NULL,
            run_id TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL)""")

    def get(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def insert(self, rec: dict, now: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (rec["request_id"], now, now, rec["session_id"], rec["turn"], rec["call_digest"],
                              rec["fixture_ref"], rec["fixture_hash"], rec["run_id"], rec["status"],
                              json.dumps(rec.get("result") or {}, ensure_ascii=False)))

    def update(self, request_id: str, status: str, result: dict, now: float) -> None:
        with self._lock:
            self._db.execute("UPDATE requests SET status=?, result=?, updated_at=? WHERE request_id=?",
                             (status, json.dumps(result, ensure_ascii=False), now, request_id))

    def by_run_id(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM requests WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                                   (run_id,)).fetchone()
        return dict(row) if row else None

    def in_flight(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM requests WHERE status IN ('accepted','running','unknown') ORDER BY created_at")]

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ------------------------------------------------------------------------------------------------ ferramenta
class SampleConfigError(ValueError):
    """Configuracao do executor fora das fronteiras (raiz, saida, motor ausente). A mensagem vai para a UI."""


def _under(path: Path, roots: tuple[Path, ...]) -> bool:
    for r in roots:
        try:
            path.relative_to(r)
            return True
        except ValueError:
            continue
    return False


class SketchUpSampleTool:
    def __init__(self, engine_root: Path | str, out_root: Path | str, *, allowed_roots: tuple[Path | str, ...],
                 registry: RequestRegistry | None = None, runner: Runner = run_engine,
                 sketchup_busy: Callable[[], bool] = sketchup_running, head: Callable[[Path], dict | None] = git_head,
                 alive: Callable[[int, float | None], bool] = engine_alive,
                 studio_head: Callable[[], dict | None] | None = None,
                 clock: Callable[[], float] = time.time, permission_timeout_s: float = 16 * 60,
                 engine_timeout_s: float = 15 * 60, base_env: Mapping[str, str] | None = None,
                 python: Path | str | None = None):
        roots = tuple(Path(r).resolve() for r in allowed_roots)
        raw_engine = Path(engine_root)
        if raw_engine.is_symlink():
            raise SampleConfigError(f"workspace do motor e um link simbolico: {raw_engine}")
        self.engine_root = raw_engine.resolve()
        if not _under(self.engine_root, roots):
            raise SampleConfigError(f"workspace do motor fora das raizes permitidas: {self.engine_root}")
        self.out_root = Path(out_root).resolve()
        if not _under(self.out_root, roots):
            raise SampleConfigError(f"saida fora das raizes permitidas: {self.out_root}")
        if _under(self.out_root, (self.engine_root,)) and "artifacts" in self.out_root.relative_to(self.engine_root).parts:
            raise SampleConfigError("saida dentro de artifacts/ do motor (evidencia publicada): recusada")
        self._registry = registry                                            # aberto no 1o uso (preflight nao grava)
        self.runner = runner
        self.sketchup_busy = sketchup_busy
        self.head = head
        self.alive = alive
        self.studio_head = studio_head or (lambda: git_head(Path(__file__).resolve().parents[2]))
        self.mode = "real" if runner is run_engine else "test-runner"     # runner injetado nunca se diz "real"
        self.clock = clock
        self.permission_timeout_s = permission_timeout_s
        self.engine_timeout_s = engine_timeout_s
        self.base_env = dict(os.environ if base_env is None else base_env)
        venv = self.engine_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.python = Path(python) if python is not None else venv
        # tipo de run -> (manifest do motor, leitor do resultado, papeis observados); o render se registra aqui
        self.replay_kinds: dict[str, tuple] = {
            "build": ("run_manifest.json", lambda rd, pr: self.read_run(rd, pr["fixture_hash"]), observe_roles)}

    @property
    def registry(self) -> RequestRegistry:
        if self._registry is None:
            self._registry = RequestRegistry(self.out_root / "_studio" / "requests.sqlite")
        return self._registry

    # -------------------------------------------------------------------------------------------- publicacao
    def specs(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": TOOL_NAME,
            "description": ("Constroi no SketchUp 2026 uma AMOSTRA de comodo a partir de uma fixture versionada e "
                            "permitida do motor; gera um run novo com .skp salvo, reaberto e inspecionado. "
                            "Pede permissao ao usuario antes de abrir o SketchUp."),
            "parameters": {"type": "object", "properties": {"fixture_ref": {
                "type": "string", "enum": self.available_fixtures(),
                "description": ("fixture permitida (room_001 = sala 4x4 m com porta e janela; plan_alt_5x3_e3a = "
                                "sala 5x3 com cama, renderizavel)")}},
                "required": ["fixture_ref"]}}}]

    def names(self) -> set[str]:
        return {TOOL_NAME}

    def available_fixtures(self) -> list[str]:
        """Fixtures da allowlist presentes neste checkout do motor (as opcionais so aparecem quando existem)."""
        return sorted(r for r, rel in ALLOWED_FIXTURES.items() if (self.engine_root / rel).is_file())

    def system_prompt(self) -> str:
        return ("Voce e o Executor local (amostra) do P2S Studio. Voce tem UMA ferramenta, build_room_sample: o Studio a "
                "executa de verdade no SketchUp 2026, depois da permissao do usuario, e devolve o resultado do motor. "
                "Para pedido de amostra de comodo, chame a ferramenta (fixture_ref room_001, salvo pedido de outra da "
                "lista). O desfecho de cada execucao e escrito pelo Studio a partir do motor.")

    @staticmethod
    def model_text(result: dict) -> str:
        """O que volta ao modelo (papel `tool`): o resumo factual do motor em frases curtas, com o desfecho na 1a linha
        (o llama3.1:8b inverteu o sentido de um JSON `completed` no ensaio real 5)."""
        st = result.get("status")
        head = {"completed": "SUCESSO: a amostra foi criada no SketchUp 2026; o .skp foi salvo, reaberto e inspecionado.",
                "unknown": "RESULTADO AINDA DESCONHECIDO: o motor nao terminou; nao ha arquivo confirmado.",
                "refused": "NAO EXECUTADO: o pedido foi recusado antes de abrir o SketchUp."}.get(
            str(st), "FALHOU: a amostra nao ficou pronta.")
        insp = result.get("inspection_result") or {}
        lines = [head]
        if result.get("run_id"):
            lines.append(f"run_id: {result['run_id']}")
        if result.get("skp_abs"):
            lines.append(f"arquivo: {result['skp_abs']} (sha256 {result.get('skp_sha256')})")
        if insp:
            lines.append(f"inspecao do arquivo reaberto: {insp.get('verdict')}; veredito visual: pendente (humano)")
        if result.get("error"):
            lines.append(f"erro: {result['error'].get('code')} - {result['error'].get('message')}")
        return "\n".join(lines)

    def model_view(self, result: dict) -> str:
        """O que fica no historico como papel `tool` (o modelo le no proximo turno): o mesmo resumo factual."""
        return self.model_text(result)

    def final_text(self, results: list[dict]) -> str:
        """A frase final do turno vem do Studio, a partir do que o motor gravou: no ensaio real o llama3.1:8b inverteu
        o sentido de resultados (falha resumida como sucesso), e a SPEC §5 proibe o modelo converter falha em sucesso."""
        parts = []
        for r in results:
            if r.get("request_id") or r.get("run_id"):
                parts.append(self.model_text(r))
            else:                                                            # recusa antes do executor (inventada etc.)
                err = r.get("error") or {}
                parts.append(f"NAO EXECUTADO: {err.get('code')} - {err.get('message')}")
        return "Resultado do motor (escrito pelo Studio, nao pelo modelo):\n" + "\n\n".join(parts)

    def preflight(self) -> tuple[bool, str]:
        """Pronto para a amostra? (motor com venv, pacote do motor, fixtures permitidas). Sem abrir o SketchUp."""
        if not (self.engine_root / "pyproject.toml").is_file() or not (self.engine_root / "src" / "p2s_engine").is_dir():
            return False, f"{self.engine_root} nao e um checkout do sketchup-mcp-next"
        if not self.python.is_file():
            return False, f"motor sem ambiente Python ({self.python}); rode `uv sync` no checkout do motor"
        missing = [r for r in CORE_FIXTURES if not (self.engine_root / ALLOWED_FIXTURES[r]).is_file()]
        if missing:
            return False, f"fixtures ausentes no motor: {missing}"
        return True, ""

    # -------------------------------------------------------------------------------------------- execucao
    def request_id(self, call: ToolCall, ctx: ToolContext) -> str:
        """Unico por chamada de cada envio (o turno entra pelo uuid do Studio: o id de sessao do Ollama recomeca em
        `ollama-1` a cada Studio). Ele LIGA pedido, permissao, run e registro; nao e dedupe entre turnos. O que impede um
        2o motor e o lock do processo + os pedidos em voo no registro duravel + o SketchUp ja aberto."""
        key = f"{ctx.session_id}|{ctx.turn_id}|{call.digest}"
        return "rq-" + hashlib.sha256(key.encode()).hexdigest()[:20]

    def _fixture(self, call: ToolCall) -> tuple[str, Path] | dict:
        args = dict(call.arguments)
        extra = sorted(set(args) - {"fixture_ref"})
        if extra:
            return _error("ARGUMENT_NOT_ALLOWED", f"argumentos nao publicados: {extra} (so fixture_ref)")
        ref = args.get("fixture_ref")
        if not isinstance(ref, str) or ref not in self.available_fixtures():
            return _error("FIXTURE_NOT_ALLOWED", f"fixture_ref fora da allowlist {self.available_fixtures()}")
        raw = self.engine_root / ALLOWED_FIXTURES[ref]
        if raw.is_symlink():
            return _error("FIXTURE_NOT_ALLOWED", "fixture e um link simbolico")
        path = raw.resolve()
        if not _under(path, (self.engine_root,)) or not path.is_file():
            return _error("FIXTURE_NOT_ALLOWED", "fixture fora do workspace do motor ou ausente")
        return ref, path

    def run(self, call: ToolCall, ctx: ToolContext) -> dict:
        rid = self.request_id(call, ctx)
        prior = self.registry.get(rid)
        if prior is not None:                                                # retry do mesmo pedido: nunca 2o run
            return self._replay(prior)
        fx = self._fixture(call)
        if isinstance(fx, dict):
            return self._refuse(call, ctx, rid, fx)
        ref, fixture = fx
        fixture_hash = sha256_file(fixture)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        run_id = f"lx-{stamp}-{rid[3:15]}-{ref}"
        run_dir = self.out_root / run_id
        head = self.head(self.engine_root)
        studio = self.studio_head() or {"commit": "desconhecido (fora de um checkout git)", "dirty": None}
        tool_call = {
            "toolCallId": call.call_id, "kind": "execute", "status": "pending",
            # o titulo aparece inteiro no cartao: modelo, acao e destino; o cartao corta o rawInput em 300 caracteres,
            # entao a ordem poe primeiro o que a permissao precisa mostrar (ferramenta, fixture+versao, workspace, impacto)
            "title": f"Executor local (amostra) · {ctx.model_id}: construir {ref} no SketchUp 2026 -> {run_dir}",
            "rawInput": {
                "tool": TOOL_NAME, "fixture_ref": ref, "expected_fixture_version": fixture_hash,
                "workspace_id": str(self.engine_root),
                "impacto": "abre o SketchUp 2026; cria so a pasta do run; nao toca Suite 01 nem artifacts/",
                # o Inbox guarda 600 caracteres: modelo, digest e request_id entram antes do que pode ser cortado
                "model": ctx.model_id, "model_digest": ctx.model_digest, "request_id": rid,
                "run_id": run_id, "fixture_path": ALLOWED_FIXTURES[ref], "engine_commit": (head or {}).get("commit"),
                "studio_commit": studio.get("commit"), "out_dir": str(run_dir),
                "call_digest": call.digest, "model_tool_call": dict(call.raw),
            },
            "locations": [{"path": str(run_dir)}],
        }
        ctx.emit("tool_call", tool_call)
        choice = self._ask(ctx, tool_call)
        if choice != "allow_once":
            if ctx.cancel.is_set():
                err = _error("CANCELLED", "turno cancelado enquanto esperava a permissao")
            else:
                err = _error("PERMISSION_DENIED", "recusado" if choice else "permissao fechada, vencida ou indisponivel")
            return self._refuse(call, ctx, rid, err, fixture_ref=ref, fixture_hash=fixture_hash, run_id=run_id)
        if sha256_file(fixture) != fixture_hash:                             # a versao aprovada e a que roda
            return self._refuse(call, ctx, rid, _error("FIXTURE_CHANGED", "a fixture mudou depois da permissao"),
                                fixture_ref=ref, fixture_hash=fixture_hash, run_id=run_id)
        if ctx.cancel.is_set():
            return self._refuse(call, ctx, rid, _error("CANCELLED", "turno cancelado antes do motor"), fixture_ref=ref,
                                fixture_hash=fixture_hash, run_id=run_id)
        ok, why = self.preflight()
        if not ok:
            return self._refuse(call, ctx, rid, _error("ENGINE_UNAVAILABLE", why), fixture_ref=ref,
                                fixture_hash=fixture_hash, run_id=run_id)
        if not _ENGINE_LOCK.acquire(blocking=False):
            return self._refuse(call, ctx, rid, _error("BUSY", "ja ha uma amostra em execucao"), fixture_ref=ref,
                                fixture_hash=fixture_hash, run_id=run_id)
        try:
            for other in self.registry.in_flight():                         # outro Studio/crash: reconciliar antes
                self._replay(other)
            if self.registry.in_flight():
                return self._refuse(call, ctx, rid, _error("BUSY", "ha um pedido anterior sem resultado"),
                                    fixture_ref=ref, fixture_hash=fixture_hash, run_id=run_id)
            if self.sketchup_busy():
                return self._refuse(call, ctx, rid, _error("SKETCHUP_BUSY", "outro SketchUp esta aberto nesta "
                                                           "maquina (slot compartilhado); tente depois"),
                                    fixture_ref=ref, fixture_hash=fixture_hash, run_id=run_id)
            if run_dir.exists():
                return self._refuse(call, ctx, rid, _error("RUN_EXISTS", f"{run_id} ja existe"), fixture_ref=ref,
                                    fixture_hash=fixture_hash, run_id=run_id)
            return self._execute(call, ctx, rid, ref, fixture, fixture_hash, run_id, head, studio)
        finally:
            _ENGINE_LOCK.release()

    def _ask(self, ctx: ToolContext, tool_call: dict) -> str | None:
        if ctx.on_permission is None:
            return None                                                      # sem canal de permissao: recusa
        from agent_bar.cockpit.acp import PermissionRequest
        got: list[str | None] = []
        done = threading.Event()

        def respond(choice: str | None) -> None:
            if not done.is_set():
                got.append(choice)
                done.set()
        ctx.on_permission(PermissionRequest(ctx.session_id, dict(tool_call), OPTIONS), respond)
        deadline = time.monotonic() + self.permission_timeout_s
        while not done.wait(0.05):
            if ctx.cancel.is_set() or time.monotonic() >= deadline:        # cancelado ou vencido = recusado
                respond(None)
                break
        return got[0] if got else None

    def _execute(self, call: ToolCall, ctx: ToolContext, rid: str, ref: str, fixture: Path, fixture_hash: str,
                 run_id: str, head: dict | None, studio: dict) -> dict:
        base = {"tool": TOOL_NAME, "request_id": rid, "run_id": run_id, "fixture_ref": ref,
                "fixture_hash": fixture_hash,
                "engine_commit": (head or {}).get("commit"), "engine_dirty": (head or {}).get("dirty"),
                "workspace_id": str(self.engine_root), "mode": self.mode, "studio_commit": studio.get("commit"),
                "studio_dirty": studio.get("dirty"), "model": ctx.model_id,
                "model_digest": ctx.model_digest, "provider_version": ctx.provider_version,
                "session_id": ctx.session_id, "turn": ctx.turn, "call_digest": call.digest,
                "provenance": provenance(ctx, head, studio)}
        self.registry.insert({**base, "session_id": ctx.session_id, "turn": ctx.turn, "call_digest": call.digest,
                              "status": "running", "result": {**base, "status": "running"}}, self.clock())
        ctx.emit("tool_update", {"toolCallId": call.call_id, "status": "in_progress",
                                 "content": _text(f"permitido uma vez · request_id {rid} · run {run_id}\n"
                                                  "SketchUp 2026 em execucao pelo motor...")})
        argv = [str(self.python), "-m", "p2s_engine.run", str(fixture), "--out-dir", str(self.out_root),
                "--run-id", run_id]
        log = self.out_root / "_studio" / f"{rid}.engine.log"
        started = self.clock()
        ident: dict[str, Any] = {}

        def on_start(pid: int, created: float | None) -> None:               # crash do Studio: o registro ja sabe o PID
            ident.update(engine_pid=pid, engine_created=created)
            self.registry.update(rid, "running", {**base, "status": "running", **ident}, self.clock())
        try:
            exit_ = self.runner(argv, self.engine_root, filtered_env(self.base_env), self.engine_timeout_s, log,
                                on_start=on_start)
        except (OSError, subprocess.SubprocessError) as exc:
            result = {**base, "status": "failed", "engine_log": str(log), "started_at": started,
                      "finished_at": self.clock(), "error": {"code": "ENGINE_LAUNCH_FAILED",
                                                             "message": f"{type(exc).__name__}: {str(exc)[:200]}"}}
            self._finish(call, ctx, rid, result)
            return result
        result = {**base, **ident, "engine_pid": exit_.pid, "engine_returncode": exit_.returncode,
                  "engine_log": str(log), "started_at": started, "finished_at": self.clock()}
        if exit_.returncode is None:
            result.update(status="unknown", error={"code": "ENGINE_TIMEOUT", "message":
                          "o motor nao terminou no prazo; o resultado sera reconciliado pelo run_id (sem novo run)"})
        else:
            result.update(self.read_run(self.out_root / run_id, fixture_hash))
        observe_roles(result["provenance"], self.out_root / run_id)          # papeis efetivos: so o que o motor registrou
        self._finish(call, ctx, rid, result)
        return result

    def _finish(self, call: ToolCall | None, ctx: ToolContext | None, rid: str, result: dict) -> None:
        self.registry.update(rid, result["status"], result, self.clock())
        prov = self.out_root / "_studio" / f"{rid}.json"
        prov.parent.mkdir(parents=True, exist_ok=True)
        prov.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        if call is not None and ctx is not None:
            ctx.emit("tool_update", {"toolCallId": call.call_id,
                                     "status": {"completed": "completed", "unknown": "in_progress"}.get(
                                         result["status"], "failed"),
                                     "content": _text(summary(result))})

    def read_run(self, run_dir: Path, fixture_hash: str) -> dict:
        """Resultado SO pelo que o motor gravou e pelo arquivo no disco. Nunca pela frase do modelo."""
        mpath = run_dir / "run_manifest.json"
        if not mpath.is_file():
            return {"status": "failed", "error": {"code": "NO_MANIFEST", "message": "o motor nao gravou run_manifest.json"}}
        try:
            man = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "failed", "error": {"code": "BAD_MANIFEST", "message": "run_manifest.json ilegivel"}}
        out: dict[str, Any] = {"run_dir": str(run_dir), "manifest_path": str(mpath),
                               "engine_commit": (man.get("engine") or {}).get("commit"),
                               "sketchup": man.get("sketchup"), "engine_status": man.get("status"),
                               "verdict": man.get("verdict")}
        if man.get("status") != "OK":
            return {**out, "status": "failed", "error": {"code": "ENGINE_FAILED", "message":
                    f"motor terminou {man.get('status')}: {json.dumps(man.get('error'), ensure_ascii=False)[:300]}"}}
        if (man.get("fixture") or {}).get("sha256") != fixture_hash:
            return {**out, "status": "failed", "error": {"code": "FIXTURE_HASH_MISMATCH", "message":
                    "o motor construiu outra versao da fixture"}}
        skp = next((a for a in man.get("artifacts") or [] if a.get("role") == "skp"), None)
        spath = (run_dir / str((skp or {}).get("path") or "")).resolve()
        inside = spath.is_relative_to(run_dir.resolve())
        if skp is None or not inside or not spath.is_file() or spath.stat().st_size == 0:
            return {**out, "status": "failed", "error": {"code": "SKP_MISSING", "message": ".skp ausente ou vazio"}}
        digest = sha256_file(spath)
        out.update(skp_path=f"{run_dir.name}/{spath.name}", skp_abs=str(spath), skp_sha256=digest,
                   skp_bytes=spath.stat().st_size)
        if digest != skp.get("sha256"):
            return {**out, "status": "failed", "error": {"code": "SKP_HASH_MISMATCH", "message":
                    "o .skp no disco nao e o que o motor registrou"}}
        vpath = run_dir / "validation_report.json"
        try:
            val = json.loads(vpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            val = {}
        reopened = next((c.get("pass") for c in val.get("checks") or [] if c.get("id") == "reopened"), None)
        out["inspection_result"] = {"verdict": val.get("verdict"), "report": str(vpath), "reopened": reopened,
                                    "failed": val.get("failed"), "visual_verdict": "PENDING_HUMAN"}
        if val.get("verdict") != "PASS" or man.get("verdict") != "PASS" or reopened is not True:
            return {**out, "status": "failed", "error": {"code": "INSPECTION_FAILED", "message":
                    f"inspecao do .skp reaberto: {val.get('verdict')} (reaberto={reopened})"}}
        return {**out, "status": "completed", "error": None}

    def _replay(self, prior: dict) -> dict:
        """Pedido ja registrado: devolve o resultado gravado; `running`/`unknown` reconcilia pelo run_dir, com o
        manifest e o leitor do tipo do run (build ou render)."""
        result = json.loads(prior["result"] or "{}")
        if prior["status"] in ("running", "unknown", "accepted"):
            kind = "render" if str(prior.get("fixture_ref") or "").startswith("render:") else "build"
            manifest_name, reader, observe = self.replay_kinds.get(kind, self.replay_kinds["build"])
            run_dir = self.out_root / prior["run_id"]
            man = run_dir / manifest_name
            if man.is_file():
                try:
                    finished = bool(json.loads(man.read_text(encoding="utf-8")).get("finished_at"))
                except (OSError, ValueError):
                    finished = False
                if finished:
                    result.update(reader(run_dir, prior))
                    if result.get("provenance"):
                        observe(result["provenance"], run_dir)
                    self._finish(None, None, prior["request_id"], result)
                    return {**result, "replayed": True}
            pid = result.get("engine_pid")
            young = not pid and self.clock() - float(prior.get("created_at") or 0) < self.engine_timeout_s
            if young or (pid and self.alive(int(pid), result.get("engine_created"))):
                result.update(status="unknown", error={"code": "RESULT_UNKNOWN", "message":
                              "o motor deste pedido ainda roda sem resultado final; nenhum novo run foi disparado"})
                self.registry.update(prior["request_id"], "unknown", result, self.clock())
            else:                                                            # motor morto sem manifest final
                code = "ENGINE_GONE" if pid else "ENGINE_NOT_STARTED"
                result.update(status="failed", error={"code": code, "message":
                              "o motor deste pedido terminou sem manifest final; nenhum novo run foi disparado"})
                self._finish(None, None, prior["request_id"], result)
        return {**result, "replayed": True}

    def _refuse(self, call: ToolCall, ctx: ToolContext, rid: str, err: dict, **extra: Any) -> dict:
        result = {"tool": TOOL_NAME, "status": "refused", "request_id": rid, "mode": self.mode,
                  "error": err["error"], **extra}
        ctx.emit("tool_update", {"toolCallId": call.call_id, "title": f"Executor local (amostra): {TOOL_NAME}",
                                 "kind": "execute", "status": "failed", "content": _text(summary(result))})
        return result


def provenance(ctx: ToolContext, engine_head: dict | None, studio: dict) -> dict:
    """Quem fez o que nesta execucao (P2S-STUDIO-LLM-LAB-001 §10). Na hora do pedido so dois papeis ja ocorreram: o
    modelo local PEDIU a ferramenta (e escolheu a fixture_ref) e o Studio escreve o desfecho. Planejamento, construcao e
    inspecao ficam em `roles_expected` e so passam a `roles` quando o run_manifest do motor registra o passo
    (`observe_roles`): timeout, falha antes do passo ou recusa nunca creditam ao motor algo que nao aconteceu. O plano
    de construcao e do motor, derivado da fixture versionada, nunca do modelo. Sem modelo de reserva."""
    engine = f"sketchup-mcp-next@{(engine_head or {}).get('commit') or 'desconhecido'}"
    model = f"{ctx.model_id}@{ctx.model_digest or 'sem-digest'}"
    return {
        "schema": "p2s.lab.provenance.v1", "provider": ctx.provider, "adapter": ctx.adapter,
        "provider_version": ctx.provider_version, "model_id": ctx.model_id, "model_digest": ctx.model_digest,
        "model_version": None,                                               # o Ollama so da a tag (no model_id) e o digest
        "experiment_id": None, "parent_run_id": None,
        "prompt_sha256": ctx.prompt_sha256, "inference": dict(ctx.inference),
        "roles": {"tool_requester": {"kind": "model", "id": model}, "planner": None, "builder": None,
                  "renderer": None, "evaluator": None, "refiner": None,
                  "outcome_writer": {"kind": "studio", "id": f"agent-bar@{studio.get('commit')}"}},
        "roles_expected": {
            "planner": {"kind": "engine", "id": engine,
                        "note": "plano de construcao derivado da fixture versionada; o modelo so escolheu a ferramenta "
                                "e a fixture_ref"},
            "builder": {"kind": "engine", "id": engine},
            "evaluator": {"kind": "engine_harness", "id": engine,
                          "note": "inspecao do .skp reaberto (validation_report.json); veredito visual: humano"},
        },
        "fallback_model": None,
    }


def observe_roles(prov: dict, run_dir: Path) -> dict:
    """Passa de `roles_expected` a `roles` so o que o run_manifest do motor registrou como passo OK; o avaliador exige
    tambem o validation_report.json com veredito. Ilegivel ou ausente = nada creditado."""
    try:
        man = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prov
    ok = {str(st.get("name")) for st in man.get("steps") or [] if st.get("status") == "OK"}
    exp, roles = prov.get("roles_expected") or {}, prov["roles"]
    commit = (man.get("engine") or {}).get("commit")
    engine = f"sketchup-mcp-next@{commit}" if commit else None

    def credit(role: str, **extra) -> None:
        if role in exp:
            roles[role] = {**exp[role], **({"id": engine} if engine else {}), **extra}
    if "validate_fixture" in ok:
        credit("planner")
    if "build_and_save" in ok:
        credit("builder", sketchup_version=(man.get("sketchup") or {}).get("version"))
    try:
        verdict = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8")).get("verdict")
    except (OSError, ValueError):
        verdict = None
    if "reopen_and_inspect" in ok and verdict:
        credit("evaluator", verdict=verdict)
    return prov


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _text(s: str) -> list[dict]:
    return [{"type": "content", "content": {"type": "text", "text": s}}]


def summary(result: dict) -> str:
    """Linhas que o Chat mostra no cartao da ferramenta: o que o MOTOR gravou, nunca a frase do modelo."""
    st = result.get("status")
    lines = [f"estado: {st}" + (" (pronto: .skp salvo, reaberto e inspecionado)" if st == "completed" else "")]
    for k in ("request_id", "run_id", "build_run_id", "fixture_ref", "fixture_hash", "engine_commit", "skp_abs",
              "skp_sha256", "skp_bytes"):
        if result.get(k) is not None:
            lines.append(f"{k}: {result[k]}")
    for img in result.get("images") or []:
        lines.append(f"imagem ({img.get('kind')}): {img.get('png_abs')} sha256 {img.get('sha256')}")
    if result.get("images"):
        lines.append("veredito visual: PENDENTE (humano)")
    insp = result.get("inspection_result") or {}
    if insp:
        lines.append(f"inspecao: {insp.get('verdict')} · reaberto={insp.get('reopened')} · relatorio {insp.get('report')}")
        lines.append("veredito visual: PENDENTE (humano)")
    su = result.get("sketchup") or {}
    if su:
        lines.append(f"SketchUp {su.get('version')} · PID build {su.get('build_pid')} · PID reabertura {su.get('reopen_pid')}")
    if result.get("error"):
        e = result["error"]
        lines.append(f"erro {e.get('code')}: {e.get('message')}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ montagem
def default_roots() -> tuple[Path, ...]:
    env = os.environ.get("P2S_LOCAL_EXECUTOR_ROOTS", "").strip()
    if env:
        return tuple(Path(p) for p in env.split(";") if p.strip())
    from agent_bar.cockpit.profiles import DEFAULT_APPS
    return (DEFAULT_APPS, DEFAULT_APPS.parent / "worktrees", DEFAULT_APPS.parent / "data" / "runs")


def preflight_from_env() -> tuple[bool, str]:
    """Para o seletor de perfil: o executor da amostra esta pronto nesta maquina? (sem gravar nada)."""
    try:
        return sample_tool_from_env().preflight()
    except SampleConfigError as exc:
        return False, str(exc)


def sample_tool_from_env() -> SketchUpSampleTool:
    """Executor da amostra com a configuracao desta maquina: P2S_LOCAL_EXECUTOR_ENGINE (checkout do motor; padrao
    E:\\Claude\\apps\\sketchup-mcp-next) e P2S_LOCAL_EXECUTOR_OUT (padrao <motor>/runs/local-executor)."""
    from agent_bar.cockpit.profiles import DEFAULT_APPS
    engine = Path(os.environ.get("P2S_LOCAL_EXECUTOR_ENGINE", "").strip() or DEFAULT_APPS / "sketchup-mcp-next")
    out = Path(os.environ.get("P2S_LOCAL_EXECUTOR_OUT", "").strip() or engine / "runs" / "local-executor")
    return SketchUpSampleTool(engine, out, allowed_roots=default_roots())


DEFAULT_MODEL = "llama3.1:8b"                                                # E0: menor modelo local com tool_call valida


def executor_adapter(endpoint: str):
    """OllamaAdapter do perfil "Executor local (amostra)": publica SO `build_room_sample`; modelo preferido
    P2S_LOCAL_EXECUTOR_MODEL (padrao llama3.1:8b, escolhido no E0). Motor nao pronto = recusa com o motivo."""
    from agent_bar.platform.ollama import OllamaAdapter
    tool = sample_tool_from_env()
    ok, why = tool.preflight()
    if not ok:
        raise SampleConfigError(why)
    model = os.environ.get("P2S_LOCAL_EXECUTOR_MODEL", "").strip() or DEFAULT_MODEL
    from agent_bar.platform.render_sample import RenderSampleTool, ToolBox
    render = RenderSampleTool(tool)
    host = ToolBox([tool, render]) if render.preflight()[0] else tool    # LAB-16: so com build renderizavel
    return OllamaAdapter(endpoint, timeout=30, tool_host=host, preferred_model=model)


if __name__ == "__main__":                                                   # preflight a mao: python -m ...
    ok, why = preflight_from_env()
    print(json.dumps({"ready": ok, "reason": why}))
    sys.exit(0 if ok else 1)
