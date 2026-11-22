"""acp.py — cliente ACP (Agent Client Protocol) por stdio, JSON-RPC 2.0, uma mensagem por linha.

O loop asyncio roda numa thread propria (`AsyncRuntime`); a thread da UI so recebe `concurrent.futures.Future` e
callbacks (que a UI reencaminha para a thread dela). O cliente nao serve arquivos nem terminal ao agente
(`fs`/`terminal` desligados): o agente executa as ferramentas do lado dele e pede permissao por
`session/request_permission`, que vai ao callback da UI. Sem callback, ou depois de `cancel`, a resposta e `cancelled`.

Windows: processo com CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, num Job Object com KILL_ON_JOB_CLOSE (o encerramento
leva os netos junto); `close()` fecha stdin, espera, e so entao `taskkill /T /F` no PID que este cliente iniciou.
Nada de prompt/resposta vai para o log: so metodo, id, tamanho e duracao (R10).
"""
from __future__ import annotations

import asyncio
import concurrent.futures as cf
import itertools
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agent_bar.cockpit.acp")

PROTOCOL_VERSION = 1
LINE_LIMIT = 16 * 1024 * 1024
CLOSE_GRACE_S = 4.0

# ---------------------------------------------------------------------------------------------------------- erros


class AcpError(Exception):
    """Falha tipada do cliente. `code` e um destes: ACP_DOWN, TIMEOUT, NOT_FOUND, RPC, CLOSED."""

    def __init__(self, code: str, message: str, data: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def classify(exc: BaseException) -> str:
    """Rotulo curto para o banner da UI (R19): auth | not_found | timeout | acp_down | error."""
    if isinstance(exc, AcpError):
        if exc.code == "NOT_FOUND":
            return "not_found"
        if exc.code == "TIMEOUT":
            return "timeout"
        if exc.code in ("ACP_DOWN", "CLOSED"):
            return "acp_down"
        text = f"{exc.message} {exc.data or ''}".lower()
        if any(k in text for k in ("auth", "login", "unauthori", "credential")):
            return "auth"
        return "error"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, cf.TimeoutError)):
        return "timeout"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    return "error"


# ------------------------------------------------------------------------------------------------ ambiente isolado

ALLOWED_ENV = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "USERNAME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "HOMEDRIVE", "HOMEPATH", "HOME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG", "LC_ALL",
})
DENY_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "GH_", "GITHUB_", "AWS_", "OPENAI_", "AZURE_", "GOOGLE_")
DENY_SUBSTRINGS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASSWD", "CREDENTIAL")


def _denied(name: str) -> bool:
    up = name.upper()
    return up.startswith(DENY_PREFIXES) or any(s in up for s in DENY_SUBSTRINGS)


def isolated_env(base: Mapping[str, str] | None = None, allow: Sequence[str] = ()) -> dict[str, str]:
    """Ambiente do agente por lista permitida (falha fechado, R7). `allow` acrescenta chaves explicitas, e mesmo
    essas sao recusadas se parecerem credencial."""
    src = os.environ if base is None else base
    keep = ALLOWED_ENV | {a.upper() for a in allow}
    out: dict[str, str] = {}
    for k, v in src.items():
        up = k.upper()
        if _denied(up):
            continue
        if up in keep or up.startswith("AGENT_BAR_"):
            out[k] = v
    return out


# ------------------------------------------------------------------------------------------------ Windows: processo

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def creation_flags() -> int:
    return (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0


def kill_tree(pid: int) -> bool:
    """`taskkill /PID <pid> /T /F` (Windows) — chamado so para o PID que o proprio cliente iniciou."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,     # bytes: saida em OEM codepage
                       creationflags=CREATE_NO_WINDOW, check=False)
    return r.returncode == 0


class _Job:
    """Job Object com KILL_ON_JOB_CLOSE: fechar o handle encerra todos os processos do job (netos incluidos)."""

    def __init__(self) -> None:
        self.handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = k32
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000                     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # ExtendedLimitInformation
            k32.CloseHandle(h)
            return
        self.handle = h

    def assign(self, pid: int) -> bool:
        if not self.handle:
            return False
        ph = self._k32.OpenProcess(0x0001 | 0x0100, False, pid)           # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if not ph:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self.handle, ph))
        finally:
            self._k32.CloseHandle(ph)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


# -------------------------------------------------------------------------------------------------- runtime asyncio


class AsyncRuntime:
    """Um loop asyncio numa thread daemon. `submit` devolve `concurrent.futures.Future` (a UI nunca bloqueia)."""

    def __init__(self, name: str = "acp-runtime") -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def submit(self, coro) -> cf.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn: Callable[..., Any], *args: Any) -> None:
        self.loop.call_soon_threadsafe(fn, *args)

    def stop(self) -> None:
        """Cancela e aguarda as tasks pendentes (leitores de clientes ja encerrados), para o loop e o fecha."""
        if self.loop.is_closed():
            return
        if self.loop.is_running():
            async def _drain() -> None:
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            try:
                asyncio.run_coroutine_threadsafe(_drain(), self.loop).result(8)
            except Exception:                                                    # noqa: BLE001 - parar de qualquer jeito
                log.warning("runtime parou com tasks pendentes")
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive() and not self.loop.is_closed():
            self.loop.close()


# ---------------------------------------------------------------------------------------------------- tipos


UPDATE_KINDS = frozenset({"agent_message_chunk", "agent_thought_chunk", "user_message_chunk", "tool_call",
                          "tool_call_update", "plan", "current_mode_update", "available_commands_update",
                          "config_option_update"})


@dataclass(frozen=True)
class Update:
    """Um `session/update` tipado (R5). `kind` fora de UPDATE_KINDS vira `unknown` (com `raw_kind`)."""

    kind: str
    session_id: str
    data: dict = field(default_factory=dict)
    raw_kind: str = ""

    @property
    def text(self) -> str:
        c = self.data.get("content")
        if isinstance(c, dict) and c.get("type") == "text":
            return str(c.get("text", ""))
        return ""


@dataclass(frozen=True)
class PermissionRequest:
    session_id: str
    tool_call: dict
    options: tuple[dict, ...]

    def option_ids(self) -> list[str]:
        return [str(o.get("optionId")) for o in self.options]

    def strongest_once(self) -> str | None:
        """A opcao `allow_once` (nunca `allow_always`): unica permitida para aprovacao automatica (R6/R27)."""
        for o in self.options:
            if o.get("kind") == "allow_once":
                return str(o.get("optionId"))
        return None


def parse_update(params: Mapping[str, Any]) -> Update:
    sid = str(params.get("sessionId", ""))
    upd = params.get("update")
    if not isinstance(upd, dict):
        return Update("unknown", sid, {}, raw_kind="")
    raw = str(upd.get("sessionUpdate", ""))
    data = {k: v for k, v in upd.items() if k != "sessionUpdate"}
    return Update(raw if raw in UPDATE_KINDS else "unknown", sid, data, raw_kind=raw)


SAFE_MODE_IDS = ("default",)                                                # o modo "pede permissao" do Claude Code / Codex
SAFE_MODE_NAMES = ("manual", "ask")
READ_ONLY_MODE_IDS = ("plan",)                                               # so planeja, nao altera nada


def safe_mode_id(modes: Mapping[str, Any]) -> str | None:
    """O modo que pede permissao antes de agir, entre os anunciados. None = nao ha como saber (fail closed)."""
    available = [m for m in modes.get("availableModes") or [] if isinstance(m, dict)]
    ids = [str(m.get("id", "")) for m in available]
    for sid in SAFE_MODE_IDS:
        if sid in ids:
            return sid
    for m in available:
        if str(m.get("name", "")).strip().lower() in SAFE_MODE_NAMES:
            return str(m.get("id", ""))
    return None


def mode_is_safe(mode_id: str, modes: Mapping[str, Any]) -> bool:
    """Seguro = pede permissao (ou so planeja). Qualquer outro (auto, acceptEdits, bypass...) decide sem perguntar."""
    return bool(mode_id) and (mode_id == safe_mode_id(modes) or mode_id in READ_ONLY_MODE_IDS)


def find_config(options: Sequence[Mapping[str, Any]], config_id: str) -> Mapping[str, Any] | None:
    """Por id; se nao houver, pela categoria (o claude-agent-acp usa id "effort" com categoria "thought_level")."""
    for o in options:
        if o.get("id") == config_id:
            return o
    for o in options:
        if o.get("category") == config_id:
            return o
    return None


def config_values(option: Mapping[str, Any]) -> list[str]:
    out = []
    for item in option.get("options") or []:
        if isinstance(item, dict) and "value" in item:
            out.append(str(item["value"]))
        elif isinstance(item, dict) and isinstance(item.get("options"), list):   # grupos de opcoes
            out.extend(str(x["value"]) for x in item["options"] if isinstance(x, dict) and "value" in x)
    return out


@dataclass
class Timeouts:
    initialize: float = 30.0
    new_session: float = 90.0
    request: float = 30.0


PermissionCallback = Callable[[PermissionRequest, Callable[[str | None], None]], None]


# ---------------------------------------------------------------------------------------------------- cliente


class AcpClient:
    """Cliente de UM processo de agente ACP. Metodos publicos sao thread-safe e devolvem `cf.Future`."""

    _instances: "weakref.WeakSet[AcpClient]" = weakref.WeakSet()

    def __init__(self, argv: Sequence[str], cwd: str | Path, *, on_update: Callable[[Update], None] | None = None,
                 on_permission: PermissionCallback | None = None, on_exit: Callable[[int | None], None] | None = None,
                 runtime: AsyncRuntime | None = None, env: Mapping[str, str] | None = None,
                 stderr_path: str | Path | None = None, timeouts: Timeouts | None = None,
                 enforce_safe_mode: bool = True) -> None:
        if not argv:
            raise ValueError("argv vazio")
        self.argv = list(argv)
        self.cwd = str(Path(cwd).resolve())
        self.on_update = on_update
        self.on_permission = on_permission
        self.on_exit = on_exit
        self._own_runtime = runtime is None
        self.rt = runtime or AsyncRuntime()
        self.env = dict(env) if env is not None else isolated_env()
        self.stderr_path = Path(stderr_path) if stderr_path else None
        self.timeouts = timeouts or Timeouts()
        self.enforce_safe_mode = enforce_safe_mode
        self.proc: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.session_id: str | None = None
        self.agent_info: dict = {}
        self.modes: dict = {}
        self.config_options: list[dict] = []                                  # modelo/esforco/... anunciados (ACP configOptions)
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._perm_pending: dict[Any, tuple[str, Callable[[str | None], None]]] = {}
        self._wlock: asyncio.Lock | None = None
        self._reader: asyncio.Task | None = None
        self._stderr_file = None
        self._job: _Job | None = None
        self._closed = False
        self._starting: asyncio.Future | None = None
        AcpClient._instances.add(self)
        self.returncode: int | None = None

    # ------------------------------------------------------------------------------------------- API (thread-safe)
    @property
    def alive(self) -> bool:
        return self.proc is not None and self.returncode is None and not self._closed

    def start(self) -> cf.Future:
        """Inicia o processo e faz `initialize`. Resultado: o dict do agente (agentCapabilities, authMethods...)."""
        return self.rt.submit(self._start())

    def connect(self, resume_session: str | None = None) -> cf.Future:
        """`start` + `session/load` (se pedido e suportado) ou `session/new`. Resultado:
        {"agent": dict, "session_id": str, "modes": dict, "loaded": bool}. Falha no load cai para sessao nova."""
        return self.rt.submit(self._connect(resume_session))

    async def _connect(self, resume_session: str | None) -> dict:
        agent = await self._start()
        loaded = False
        if resume_session and (agent.get("agentCapabilities") or {}).get("loadSession"):
            try:
                res = await self._load_session(resume_session, self.cwd)
                self.modes = res.get("modes") or self.modes
                self._take_config(res)
                loaded = True
            except AcpError as exc:
                if exc.code != "RPC":
                    raise
                self.session_id = None
        if not loaded:
            await self._new_session(self.cwd)
        forced_from, safe = await self._ensure_safe_mode()
        return {"agent": agent, "session_id": self.session_id, "modes": self.modes, "loaded": loaded,
                "mode_forced_from": forced_from, "safe_mode": safe, "config_options": self.config_options}

    async def _ensure_safe_mode(self) -> tuple[str | None, bool]:
        """Modo perigoso nunca implicito: se o agente abriu a sessao num modo que decide sem pedir (o claude-agent-acp
        0.109 abre em "auto"), troca para o modo que pede permissao e so considera feito com a resposta do agente.
        Devolve (modo de onde saiu ou None, ha modo seguro?). Agente sem modos: nada a trocar."""
        if not self.enforce_safe_mode or not self.modes.get("availableModes"):
            return None, True
        safe = safe_mode_id(self.modes)
        current = str(self.modes.get("currentModeId") or "")
        if safe is None:
            return None, False
        if mode_is_safe(current, self.modes):
            return None, True
        await self._request("session/set_mode", {"sessionId": self._sid(), "modeId": safe}, self.timeouts.request)
        self.modes = {**self.modes, "currentModeId": safe}
        log.info("modo inicial %r trocado para %r (seguro por padrao)", current, safe)
        return current or None, True

    def new_session(self, cwd: str | Path | None = None) -> cf.Future:
        return self.rt.submit(self._new_session(str(Path(cwd).resolve()) if cwd else self.cwd))

    def load_session(self, session_id: str, cwd: str | Path | None = None) -> cf.Future:
        return self.rt.submit(self._load_session(session_id, str(Path(cwd).resolve()) if cwd else self.cwd))

    def set_config_option(self, config_id: str, value: str) -> cf.Future:
        """`session/set_config_option`. Resultado: a lista de configOptions DEVOLVIDA pelo agente, que e a confirmacao do
        que vale agora (trocar o modelo pode mudar os esforcos possiveis). Valor fora das opcoes anunciadas e recusado
        aqui, sem ir ao agente."""
        return self.rt.submit(self._set_config_option(config_id, value))

    async def _set_config_option(self, config_id: str, value: str) -> list[dict]:
        opt = find_config(self.config_options, config_id)
        if opt is None:
            raise AcpError("RPC", f"o agente nao anuncia a configuracao {config_id!r}")
        if value not in config_values(opt):
            raise AcpError("RPC", f"{config_id}={value!r} fora das opcoes anunciadas")
        res = await self._request("session/set_config_option",
                                  {"sessionId": self._sid(), "configId": config_id, "value": value}, self.timeouts.request)
        if not isinstance(res, dict) or not isinstance(res.get("configOptions"), list):
            raise AcpError("RPC", "session/set_config_option sem configOptions na resposta: valor nao confirmado")
        self._take_config(res)
        return self.config_options

    def _take_config(self, res: Mapping[str, Any]) -> None:
        opts = res.get("configOptions")
        if isinstance(opts, list):
            self.config_options = [o for o in opts if isinstance(o, dict) and o.get("id")]

    def set_mode(self, mode_id: str) -> cf.Future:
        return self.rt.submit(self._request("session/set_mode", {"sessionId": self._sid(), "modeId": mode_id},
                                            self.timeouts.request))

    def prompt(self, content: str | Sequence[dict]) -> cf.Future:
        """`session/prompt`. Resultado: o `stopReason` (end_turn, cancelled, max_tokens, refusal...)."""
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
        return self.rt.submit(self._prompt(blocks))

    def cancel(self) -> cf.Future:
        return self.rt.submit(self._cancel())

    def close(self, grace_s: float = CLOSE_GRACE_S) -> cf.Future:
        fut = self.rt.submit(self._close(grace_s))
        if self._own_runtime:
            fut.add_done_callback(lambda _f: threading.Thread(target=self.rt.stop, daemon=True).start())
        return fut

    # ------------------------------------------------------------------------------------------------- internos
    def _sid(self) -> str:
        if not self.session_id:
            raise AcpError("RPC", "sem sessao: chame new_session antes")
        return self.session_id

    def _resolve_argv(self) -> list[str]:
        exe = self.argv[0]
        found = exe if Path(exe).is_file() else shutil.which(exe, path=self.env.get("PATH") or self.env.get("Path"))
        if not found:
            raise AcpError("NOT_FOUND", f"executavel do agente nao encontrado: {exe}")
        return [found, *self.argv[1:]]

    async def _start(self) -> dict:
        if self.proc is not None or self._starting is not None:
            raise AcpError("RPC", "cliente ja iniciado")
        self._starting = asyncio.get_running_loop().create_future()
        try:
            return await self._spawn_and_initialize()
        finally:
            self._starting.set_result(None)

    async def _spawn_and_initialize(self) -> dict:
        argv = self._resolve_argv()
        self._wlock = asyncio.Lock()
        stderr: Any = subprocess.DEVNULL
        if self.stderr_path:
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_file = open(self.stderr_path, "ab")                  # noqa: SIM115 - fechado em _finish
            stderr = self._stderr_file
        self._job = _Job()
        t0 = time.monotonic()
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=stderr, limit=LINE_LIMIT, creationflags=creation_flags())
        self.pid = self.proc.pid
        self._job.assign(self.pid)
        log.info("agente iniciado pid=%s exe=%s", self.pid, Path(argv[0]).name)
        self._reader = asyncio.ensure_future(self._read_loop())
        if self._closed:                                                   # close() chegou durante o spawn: nao deixar orfao
            await self._shutdown_proc(1.0)
            raise AcpError("CLOSED", "cliente encerrado durante o start")
        res = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "agent-bar-cockpit", "version": "0.1"},
        }, self.timeouts.initialize)
        self.agent_info = res if isinstance(res, dict) else {}
        log.info("initialize ok em %.0f ms", (time.monotonic() - t0) * 1000)
        return self.agent_info

    async def _new_session(self, cwd: str) -> dict:
        res = await self._request("session/new", {"cwd": cwd, "mcpServers": []}, self.timeouts.new_session)
        res = res if isinstance(res, dict) else {}
        self.session_id = str(res.get("sessionId", "")) or None
        if not self.session_id:
            raise AcpError("RPC", "session/new sem sessionId")
        self.modes = res.get("modes") or {}
        self._take_config(res)
        return res

    async def _load_session(self, session_id: str, cwd: str) -> dict:
        caps = self.agent_info.get("agentCapabilities") or {}
        if not caps.get("loadSession"):
            raise AcpError("RPC", "agente nao anuncia loadSession")
        self.session_id = session_id
        res = await self._request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
                                  self.timeouts.new_session)
        return res if isinstance(res, dict) else {}

    async def _prompt(self, blocks: list[dict]) -> str:
        sid = self._sid()
        t0 = time.monotonic()
        res = await self._request("session/prompt", {"sessionId": sid, "prompt": blocks}, None)
        reason = str((res or {}).get("stopReason", "")) if isinstance(res, dict) else ""
        log.info("prompt terminou stopReason=%s em %.1f s", reason, time.monotonic() - t0)
        return reason

    async def _cancel(self) -> None:
        if self.session_id:
            await self._cancel_session(self.session_id)

    async def _cancel_session(self, session_id: str) -> None:
        if not self.alive:
            return
        for rid, (sid, _respond) in list(self._perm_pending.items()):          # pedidos de permissao em aberto -> cancelled
            if sid == session_id:
                await self._answer_permission(rid, None)
        await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})

    # ------------------------------------------------------------------- varias sessoes no mesmo processo (plataforma)
    def call(self, method: str, params: dict, timeout: float | None = 30.0) -> cf.Future:
        """Pedido JSON-RPC cru, com `sessionId` explicito nos params: o adaptador da plataforma usa varias sessoes no
        mesmo processo (como os editores ACP). `timeout=None` = sem limite (session/prompt)."""
        return self.rt.submit(self._request(method, params, timeout))

    def cancel_session(self, session_id: str) -> cf.Future:
        return self.rt.submit(self._cancel_session(session_id))

    async def _send(self, msg: dict) -> None:
        if self.proc is None or self.proc.stdin is None or self._closed or self.returncode is not None:
            raise AcpError("ACP_DOWN", "processo do agente nao esta rodando")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        assert self._wlock is not None
        async with self._wlock:
            try:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
                raise AcpError("ACP_DOWN", f"falha ao escrever no agente: {type(exc).__name__}") from exc
        log.debug("-> %s id=%s %d B", msg.get("method", "response"), msg.get("id"), len(data))

    async def _request(self, method: str, params: dict, timeout: float | None) -> Any:
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if timeout is None:
                return await fut
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout)
            except asyncio.TimeoutError as exc:
                raise AcpError("TIMEOUT", f"{method} sem resposta em {timeout:.0f} s") from exc
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        out = self.proc.stdout
        try:
            while True:
                try:
                    line = await out.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    log.warning("linha do agente acima de %d B descartada", LINE_LIMIT)
                    continue
                if not line:
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    log.warning("linha invalida do agente ignorada (%d B)", len(text))
                    continue
                if isinstance(msg, dict):
                    await self._dispatch(msg)
        except Exception:                                                        # noqa: BLE001 - leitura nunca derruba o loop
            log.exception("erro no read loop")
        finally:
            await self._finish()

    async def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None and "id" in msg:                                       # resposta a um pedido nosso
            fut = self._pending.get(msg["id"])
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg.get("error") or {}
                fut.set_exception(AcpError("RPC", str(err.get("message", "erro")), err.get("data")))
            else:
                fut.set_result(msg.get("result"))
            return
        params = msg.get("params") or {}
        if "id" in msg:                                                          # pedido do agente
            if method == "session/request_permission":
                self._on_permission_request(msg["id"], params)
            else:
                await self._send({"jsonrpc": "2.0", "id": msg["id"],
                                  "error": {"code": -32601, "message": f"metodo nao suportado pelo cliente: {method}"}})
            return
        if method == "session/update":
            self._emit(parse_update(params))

    def _emit(self, upd: Update) -> None:
        if upd.kind == "config_option_update":
            self._take_config(upd.data)
        if upd.kind == "current_mode_update" and upd.data.get("currentModeId"):
            self.modes = {**self.modes, "currentModeId": str(upd.data["currentModeId"])}
        if self.on_update is None:
            return
        try:
            self.on_update(upd)
        except Exception:                                                        # noqa: BLE001 - callback da UI nao derruba o cliente
            log.exception("on_update falhou")

    def _on_permission_request(self, rid: Any, params: dict) -> None:
        req = PermissionRequest(str(params.get("sessionId", "")), params.get("toolCall") or {},
                                tuple(o for o in params.get("options") or [] if isinstance(o, dict)))
        if self.on_permission is None or self._closed:
            asyncio.ensure_future(self._answer_permission(rid, None, force=True))
            return
        loop = asyncio.get_running_loop()

        def respond(option_id: str | None) -> None:                              # chamado de qualquer thread
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._answer_permission(rid, option_id)))

        self._perm_pending[rid] = (req.session_id, respond)
        try:
            self.on_permission(req, respond)
        except Exception:                                                        # noqa: BLE001
            log.exception("on_permission falhou: respondendo cancelled")
            asyncio.ensure_future(self._answer_permission(rid, None))

    async def _answer_permission(self, rid: Any, option_id: str | None, force: bool = False) -> None:
        if not force and self._perm_pending.pop(rid, None) is None:
            return                                                               # ja respondido (idempotente)
        outcome = {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"}
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
        except AcpError:
            pass

    async def _finish(self) -> None:
        if self.proc is not None and self.returncode is None:
            try:
                self.returncode = await asyncio.wait_for(self.proc.wait(), 2)
            except asyncio.TimeoutError:
                self.returncode = None
        self._perm_pending.clear()
        if self.returncode is not None:
            self._close_transport()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(AcpError("CLOSED" if self._closed else "ACP_DOWN",
                                           f"processo do agente terminou (codigo {self.returncode})"))
        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None
        log.info("agente pid=%s terminou codigo=%s", self.pid, self.returncode)
        if self.on_exit is not None and not self._closed:
            try:
                self.on_exit(self.returncode)
            except Exception:                                                    # noqa: BLE001
                log.exception("on_exit falhou")

    async def _close(self, grace_s: float) -> int | None:
        starting = self._starting
        if self.proc is None and starting is not None and not starting.done():
            self._closed = True                                                  # o _start ve a marca, encerra o processo
            await asyncio.shield(starting)                                       # e so entao este close termina
            return self.returncode
        if self.proc is None:
            self._closed = True
            return None
        return await self._shutdown_proc(grace_s)

    async def _shutdown_proc(self, grace_s: float) -> int | None:
        assert self.proc is not None
        self._closed = True
        for rid in list(self._perm_pending):
            await self._answer_permission(rid, None)
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), grace_s)
        except asyncio.TimeoutError:
            if self.pid:
                await asyncio.get_running_loop().run_in_executor(None, kill_tree, self.pid)
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._job is not None:                                                # leva netos que sobreviveram ao pai
            self._job.close()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader.cancel()
        self.returncode = self.proc.returncode
        self._close_transport()
        return self.returncode

    def _close_transport(self) -> None:
        """Fecha pipes e transporte do subprocesso ja terminado (sem isso o GC reclama de transporte aberto)."""
        transport = getattr(self.proc, "_transport", None)
        if transport is not None and not transport.is_closing():
            transport.close()

    @property
    def process_running(self) -> bool:
        """O processo que este cliente iniciou ainda existe (independente de `close` ter sido pedido)."""
        return self.proc is not None and self.proc.returncode is None


def live_clients() -> list[AcpClient]:
    """Clientes cujo processo de agente ainda roda (diagnostico e testes de vazamento)."""
    return [c for c in list(AcpClient._instances) if c.process_running]
