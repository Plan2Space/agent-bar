"""sessions.py — Session Manager minimo da F4: sessoes simultaneas em varios provedores, identidade propria, cancelamento
e fechamento por sessao, limite por provedor, persistencia e recuperacao.

Regras (SPEC §5 F4 + achado das ~75 sessoes do claude-agent-acp, Plan2Space/agent-bar#47):
- cada sessao tem um id do Studio (estavel) e o id do provedor (muda numa recuperacao sem contexto);
- **limite de sessoes abertas por provedor** (padrao 16 para o claude-agent-acp, que cai perto de 75): ao atingir,
  `SessionLimitReached` com o motivo, em vez de deixar o agente cair;
- **fechamento obrigatorio**: `close` fecha no provedor (`close_session`), `close_all` fecha tudo no fim, e o
  adaptador e desconectado quando nao sobra sessao dele;
- **recuperacao**: se o provedor cair (`ProviderUnavailable`/`NotConnected`), as sessoes dele ficam em ERROR com o
  motivo; `recover` reconecta e tenta `resume_session` (com contexto); sem resume, abre sessao nova com o MESMO modelo
  e esforco pedidos e marca `recovered_without_context` — nada de fingir continuidade, nada de reenviar prompt sozinho;
- **persistencia**: registro Session v1 + historico visivel em JSON atomico; ao reabrir o Studio as sessoes voltam
  como CLOSED e `recover` as reabre.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_bar.platform.adapter import (
    AdapterError,
    AgentEvent,
    CapabilityUnsupported,
    NotConnected,
    PromptResult,
    ProviderAdapter,
    ProviderUnavailable,
    SessionNotFound,
    SessionRequest,
)
from agent_bar.platform.image_ledger import ImageLedger, ReferenceLink, send_with_images
from agent_bar.platform.proc import creation_time, kill_tree, same_process

SCHEMA = "p2s.session-manager.v1"
# Limite medido, nao chutado (F5-B, E:\Claude\data\runs\p2s-f-studio-platform\f5\claude-acp-load.json, 2026-11-22):
# claude-agent-acp 0.109.1 custa ~2 processos e ~250 MB por sessao, linear, abertura ~0,6 s, sem erro ate 40 sessoes;
# caiu perto de 75 (0xC0000409). 32 = folga sobre o medido estavel e ~8 GB de arvore. Ajustavel por
# AGENT_BAR_SESSION_LIMITS="claude-acp=24,ollama=8" (o processo por sessao limita processos: ~2n+3).
DEFAULT_LIMITS = {"claude-acp": 32}
DEFAULT_LIMIT = 32


def limits_from_env(env: Mapping[str, str] | None = None) -> dict[str, int]:
    """`AGENT_BAR_SESSION_LIMITS="prov=n,prov2=m"`; entrada invalida e ignorada (fica o padrao medido)."""
    raw = (os.environ if env is None else env).get("AGENT_BAR_SESSION_LIMITS", "")
    out: dict[str, int] = {}
    for part in raw.split(","):
        name, _, value = part.partition("=")
        if name.strip() and value.strip().isdigit() and int(value) > 0:
            out[name.strip()] = int(value)
    return out
STALL_TIMEOUT_S = 180.0                                                        # turno sem nenhum evento: "parado?"
IDLE_TIMEOUT_S = 45 * 60.0                                                     # sessao parada: fecha e libera o processo
DOWN = (ProviderUnavailable, NotConnected)


def _process_died(adapter: ProviderAdapter) -> bool:
    """Adaptador de processo (tem ambiente de filho) sem nenhum processo vivo = o agente caiu."""
    return adapter.process_env({}) is not None and not adapter.owned_pids()


class SessionLimitReached(AdapterError):
    code = "SESSION_LIMIT"


class UnknownSession(AdapterError):
    code = "UNKNOWN_SESSION"


class SessionBusy(AdapterError):
    code = "SESSION_BUSY"


class SessionManager:
    def __init__(self, factories: Mapping[str, Callable[..., ProviderAdapter]], *, store_path: Path | None = None,
                 limits: Mapping[str, int] | None = None, default_limit: int = DEFAULT_LIMIT,
                 clock: Callable[[], float] = time.time, stall_timeout: float = STALL_TIMEOUT_S,
                 idle_timeout: float = IDLE_TIMEOUT_S, killer: Callable[[int], bool] = kill_tree,
                 image_ledger: ImageLedger | None = None):
        self.factories = dict(factories)
        self.image_ledger = image_ledger                                     # sem ele, imagem nao sai (S01C-R3)
        self.store_path = store_path
        self.limits = {**DEFAULT_LIMITS, **limits_from_env(), **(limits or {})}
        self.default_limit = default_limit
        self.clock = clock
        self.stall_timeout = stall_timeout
        self.idle_timeout = idle_timeout
        self.killer = killer
        self.orphans: list[dict] = []                                        # processos da execucao anterior ainda vivos
        self._adapters: dict[tuple[str, str], ProviderAdapter] = {}          # (provedor, workspace): nada cruza workspace
        self._sessions: dict[str, dict] = {}
        self._running: dict[str, cf.Future] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------------------------------------ consulta
    def sessions(self) -> list[dict]:
        with self._lock:
            return [self._public(s) for s in self._sessions.values()]

    def get(self, sid: str) -> dict:
        with self._lock:
            return self._public(self._need(sid))

    def limit_for(self, provider_id: str) -> int:
        return int(self.limits.get(provider_id, self.default_limit))

    def open_count(self, provider_id: str) -> int:
        return sum(1 for s in self._sessions.values() if s["provider_id"] == provider_id and s["state"] != "CLOSED")

    @staticmethod
    def _public(s: dict) -> dict:
        return json.loads(json.dumps(s))                                     # copia profunda, sem referencia interna

    def _need(self, sid: str) -> dict:
        s = self._sessions.get(sid)
        if s is None:
            raise UnknownSession(sid)
        return s

    # ------------------------------------------------------------------------------------------------ provedores
    @staticmethod
    def _key(s: Mapping[str, Any]) -> tuple[str, str]:
        return (str(s["provider_id"]), str((s.get("request") or {}).get("workspace") or ""))

    def _adapter(self, provider_id: str, workspace: str = "") -> ProviderAdapter:
        key = (provider_id, workspace or "")
        a = self._adapters.get(key)
        if a is None:
            if provider_id not in self.factories:
                raise UnknownSession(f"provedor sem adaptador: {provider_id}")
            factory = self.factories[provider_id]
            try:
                a = factory(workspace or "")                                 # um processo por workspace (isolamento)
            except TypeError:
                a = factory()
            self._adapters[key] = a
        if a.get_status().connection_status != "CONNECTED":
            a.connect()
        return a

    def _mark_provider_down(self, key: tuple[str, str], why: str) -> None:
        for s in self._sessions.values():
            if self._key(s) == key and s["state"] not in ("CLOSED",):
                s["state"], s["error"] = "ERROR", why
        self._save()

    # ------------------------------------------------------------------------------------------------ ciclo
    def open(self, provider_id: str, request: SessionRequest | None = None, title: str = "") -> dict:
        request = request or SessionRequest()
        with self._lock:
            limit = self.limit_for(provider_id)
            if self.open_count(provider_id) >= limit:
                raise SessionLimitReached(f"{provider_id}: {limit} sessoes abertas (limite); feche uma antes de abrir outra")
            adapter = self._adapter(provider_id, request.workspace or "")
            rec = adapter.create_session(request)
            sid = f"p2s-{uuid.uuid4().hex[:12]}"
            s = {
                "id": sid, "provider_id": provider_id, "title": title, "state": "READY", "error": None,
                "request": {"model_id": request.model_id, "effort": request.effort, "workspace": request.workspace},
                "session": rec, "history": [], "recovered": 0, "recovered_without_context": False,
                "opened_at": self.clock(), "updated_at": self.clock(), "last_event_at": None, "stalled": False,
            }
            self._sessions[sid] = s
            self._save()
            return self._public(s)

    def send(self, sid: str, text: str, on_event: Callable[[AgentEvent], None] = lambda e: None, *,
             images=(), purpose: str = "CHAT", requested_by: str = "studio",
             reference: ReferenceLink | None = None) -> cf.Future:
        """Future[PromptResult]. Provedor caido: as sessoes dele vao para ERROR e o future falha com o erro tipado
        (a UI oferece `recover`; o prompt NAO e reenviado sozinho).
        `images`: passa por `send_with_images` (registro antes do envio); sem `image_ledger`, ImageLedgerError."""
        with self._lock:
            s = self._need(sid)
            if s["state"] in ("CLOSED", "ERROR"):
                raise ProviderUnavailable(f"sessao {s['state']}: recupere antes de enviar ({s.get('error') or ''})".strip())
            if sid in self._running:
                raise SessionBusy(sid)
            key = self._key(s)
            adapter = self._adapters[key]
            psid = s["session"]["session_id"]
            parts: list[str] = []

            def sink(ev: AgentEvent) -> None:
                s["last_event_at"], s["stalled"] = self.clock(), False         # heartbeat do turno
                if ev.kind == "text":
                    parts.append(ev.text)
                on_event(ev)
            try:
                if images:
                    inner = send_with_images(adapter, self.image_ledger, psid, text, sink, images,
                                             provider_id=s["provider_id"], model_id=s["session"].get("model_accepted") or "provider-default",
                                             requested_by=requested_by, purpose=purpose, studio_session_id=sid,
                                             reference=reference)
                else:
                    inner = adapter.send_prompt(psid, text, sink)
            except DOWN as exc:
                self._mark_provider_down(key, f"{exc.code}: {exc.message}")
                raise
            s["last_event_at"], s["stalled"] = self.clock(), False
            s["state"] = "RUNNING"                                           # so depois que o provedor aceitou o prompt
            s["history"].append({"role": "user", "text": text, "t": self.clock()})
            out: cf.Future = cf.Future()
            self._running[sid] = out

        def done(f: cf.Future) -> None:
            with self._lock:
                self._running.pop(sid, None)
                exc = f.exception()
                if exc is not None:
                    if isinstance(exc, DOWN) or _process_died(adapter):
                        self._mark_provider_down(key, f"{type(exc).__name__}: {exc}")
                    else:
                        s["state"] = "READY"
                        self._save()
                    out.set_exception(exc)
                    return
                res: PromptResult = f.result()
                if parts:
                    s["history"].append({"role": "agent", "text": "".join(parts), "t": self.clock(),
                                         "stop_reason": res.stop_reason, "model": res.model_confirmed})
                if s["state"] == "RUNNING":
                    s["state"] = "READY"
                s["updated_at"], s["stalled"] = self.clock(), False
                self._save()
            out.set_result(res)
        inner.add_done_callback(done)
        return out

    def cancel(self, sid: str) -> None:
        with self._lock:
            s = self._need(sid)
            if sid not in self._running:
                return
            adapter = self._adapters[self._key(s)]
            psid = s["session"]["session_id"]
        adapter.cancel(psid)

    def close(self, sid: str, why: str | None = None) -> None:
        """Fecha no provedor (obrigatorio: o claude-agent-acp segura um processo por sessao) e mantem o registro."""
        with self._lock:
            s = self._need(sid)
            if s["state"] == "CLOSED":
                return
            adapter = self._adapters.get(self._key(s))
            if adapter is not None and s["state"] != "ERROR":
                if sid in self._running:
                    try:
                        adapter.cancel(s["session"]["session_id"])
                    except AdapterError:
                        pass
                try:
                    adapter.close_session(s["session"]["session_id"])
                except (CapabilityUnsupported, SessionNotFound, *DOWN):
                    pass
            s["state"], s["updated_at"] = "CLOSED", self.clock()
            if why:
                s["error"] = why
            idle = self._pop_idle_adapter(self._key(s))
            self._save()
        if idle is not None:                                                 # FORA do lock: o disconnect espera as threads
            idle.disconnect()                                                # do adaptador, cujo callback pega este lock

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid)
        with self._lock:
            rest = [self._adapters.pop(k) for k in list(self._adapters)]
        for a in rest:
            a.disconnect()

    def _pop_idle_adapter(self, key: tuple[str, str]) -> ProviderAdapter | None:
        busy = any(self._key(s) == key and s["state"] != "CLOSED" for s in self._sessions.values())
        return self._adapters.pop(key) if not busy and key in self._adapters else None

    def recover(self, sid: str) -> dict:
        """Reabre a sessao: reconecta o provedor se preciso; com resume, volta com contexto; sem, abre sessao nova com
        o mesmo modelo/esforco pedidos e marca `recovered_without_context`."""
        with self._lock:
            s = self._need(sid)
            dead = self._adapters.pop(self._key(s), None) if s["state"] == "ERROR" else None
        if dead is not None:                                                 # fora do lock (ver close)
            try:
                dead.disconnect()
            except Exception:                                                 # noqa: BLE001 - processo ja morto
                pass
        with self._lock:
            s = self._need(sid)
            if s["state"] in ("READY", "RUNNING"):
                return self._public(s)
            if self.open_count(s["provider_id"]) >= self.limit_for(s["provider_id"]):
                raise SessionLimitReached(f"{s['provider_id']}: limite de sessoes abertas atingido")
            pid = s["provider_id"]
            key = self._key(s)
            adapter = self._adapter(pid, key[1])
            rec = None
            try:
                rec = adapter.resume_session(s["session"]["session_id"])
                s["recovered_without_context"] = False
            except (CapabilityUnsupported, SessionNotFound):
                req = s["request"]
                rec = adapter.create_session(SessionRequest(model_id=req["model_id"], effort=req["effort"],
                                                            workspace=req["workspace"]))
                s["recovered_without_context"] = True
            s["session"], s["state"], s["error"] = rec, "READY", None
            s["recovered"] += 1
            s["updated_at"] = self.clock()
            for other in self._sessions.values():                            # o processo velho morreu para todas
                if other is not s and self._key(other) == key and other["state"] in ("READY", "RUNNING") \
                        and other["session"]["session_id"] not in adapter.get_status().sessions:
                    other["state"], other["error"] = "ERROR", "o processo do provedor foi reiniciado"
            self._save()
            return self._public(s)

    # ------------------------------------------------------------------------------------------------ heartbeat
    def heartbeat(self) -> dict:
        """Uma volta de vigia: processo do provedor morto -> sessoes em ERROR; turno sem evento ha `stall_timeout` ->
        `stalled` (continua rodando; a UI oferece cancelar); sessao parada ha `idle_timeout` -> fechada (libera o
        processo; volta por `recover`). Devolve o que mudou."""
        now = self.clock()
        report: dict[str, list[str]] = {"down": [], "stalled": [], "idle_closed": []}
        with self._lock:
            for key, adapter in list(self._adapters.items()):
                if _process_died(adapter):
                    ids = [s["id"] for s in self._sessions.values() if self._key(s) == key and s["state"] not in ("CLOSED", "ERROR")]
                    if ids:
                        self._mark_provider_down(key, "o processo do provedor terminou (heartbeat)")
                        report["down"] += ids
            for s in self._sessions.values():
                if s["state"] == "RUNNING" and s.get("last_event_at") and now - s["last_event_at"] > self.stall_timeout:
                    if not s.get("stalled"):
                        s["stalled"] = True
                        report["stalled"].append(s["id"])
            idle = [s["id"] for s in self._sessions.values()
                    if s["state"] == "READY" and s["id"] not in self._running and now - s["updated_at"] > self.idle_timeout]
        for sid in idle:
            self.close(sid, why=f"fechada por ociosidade ({int(self.idle_timeout // 60)} min); recupere para continuar")
            report["idle_closed"].append(sid)
        if any(report.values()):
            self._save()
        return report

    def end_orphans(self) -> list[int]:
        """Encerra so os processos que ESTE Studio iniciou numa execucao anterior e que ainda batem (PID + criacao)."""
        ended = []
        for p in list(self.orphans):
            if same_process(p["pid"], p.get("created")) and self.killer(p["pid"]):
                ended.append(p["pid"])
            self.orphans.remove(p)
        self._save()
        return ended

    # ------------------------------------------------------------------------------------------------ persistencia
    def _save(self) -> None:
        if self.store_path is None:
            return
        procs = [{"provider_id": k[0], "workspace": k[1], "pid": pid, "created": creation_time(pid)}
                 for k, a in self._adapters.items() for pid in a.owned_pids()]
        data = {"schema": SCHEMA, "sessions": list(self._sessions.values()), "processes": procs}
        tmp = self.store_path.with_suffix(".tmp")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.store_path)

    def _load(self) -> None:
        if self.store_path is None or not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                                                           # arquivo corrompido: comeca vazio, nao inventa
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return
        self.orphans = [p for p in data.get("processes") or []
                        if isinstance(p, dict) and isinstance(p.get("pid"), int) and same_process(p["pid"], p.get("created"))]
        for s in data.get("sessions") or []:
            if isinstance(s, dict) and s.get("id"):
                if s.get("state") != "CLOSED":                                # processo da execucao anterior nao existe mais
                    s["state"], s["error"] = "CLOSED", "o Studio foi fechado"
                self._sessions[s["id"]] = s

    def __enter__(self) -> SessionManager:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close_all()
