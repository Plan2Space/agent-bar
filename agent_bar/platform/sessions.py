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

SCHEMA = "p2s.session-manager.v1"
DEFAULT_LIMITS = {"claude-acp": 16}
DEFAULT_LIMIT = 32
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
    def __init__(self, factories: Mapping[str, Callable[[], ProviderAdapter]], *, store_path: Path | None = None,
                 limits: Mapping[str, int] | None = None, default_limit: int = DEFAULT_LIMIT,
                 clock: Callable[[], float] = time.time):
        self.factories = dict(factories)
        self.store_path = store_path
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.default_limit = default_limit
        self.clock = clock
        self._adapters: dict[str, ProviderAdapter] = {}
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
    def _adapter(self, provider_id: str) -> ProviderAdapter:
        a = self._adapters.get(provider_id)
        if a is None:
            if provider_id not in self.factories:
                raise UnknownSession(f"provedor sem adaptador: {provider_id}")
            a = self.factories[provider_id]()
            self._adapters[provider_id] = a
        if a.get_status().connection_status != "CONNECTED":
            a.connect()
        return a

    def _mark_provider_down(self, provider_id: str, why: str) -> None:
        for s in self._sessions.values():
            if s["provider_id"] == provider_id and s["state"] not in ("CLOSED",):
                s["state"], s["error"] = "ERROR", why
        self._save()

    # ------------------------------------------------------------------------------------------------ ciclo
    def open(self, provider_id: str, request: SessionRequest | None = None, title: str = "") -> dict:
        request = request or SessionRequest()
        with self._lock:
            limit = self.limit_for(provider_id)
            if self.open_count(provider_id) >= limit:
                raise SessionLimitReached(f"{provider_id}: {limit} sessoes abertas (limite); feche uma antes de abrir outra")
            adapter = self._adapter(provider_id)
            rec = adapter.create_session(request)
            sid = f"p2s-{uuid.uuid4().hex[:12]}"
            s = {
                "id": sid, "provider_id": provider_id, "title": title, "state": "READY", "error": None,
                "request": {"model_id": request.model_id, "effort": request.effort, "workspace": request.workspace},
                "session": rec, "history": [], "recovered": 0, "recovered_without_context": False,
                "opened_at": self.clock(), "updated_at": self.clock(),
            }
            self._sessions[sid] = s
            self._save()
            return self._public(s)

    def send(self, sid: str, text: str, on_event: Callable[[AgentEvent], None] = lambda e: None) -> cf.Future:
        """Future[PromptResult]. Provedor caido: as sessoes dele vao para ERROR e o future falha com o erro tipado
        (a UI oferece `recover`; o prompt NAO e reenviado sozinho)."""
        with self._lock:
            s = self._need(sid)
            if s["state"] in ("CLOSED", "ERROR"):
                raise ProviderUnavailable(f"sessao {s['state']}: recupere antes de enviar ({s.get('error') or ''})".strip())
            if sid in self._running:
                raise SessionBusy(sid)
            adapter = self._adapters[s["provider_id"]]
            psid = s["session"]["session_id"]
            parts: list[str] = []

            def sink(ev: AgentEvent) -> None:
                if ev.kind == "text":
                    parts.append(ev.text)
                on_event(ev)
            try:
                inner = adapter.send_prompt(psid, text, sink)
            except DOWN as exc:
                self._mark_provider_down(s["provider_id"], f"{exc.code}: {exc.message}")
                raise
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
                        self._mark_provider_down(s["provider_id"], f"{type(exc).__name__}: {exc}")
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
                s["updated_at"] = self.clock()
                self._save()
            out.set_result(res)
        inner.add_done_callback(done)
        return out

    def cancel(self, sid: str) -> None:
        with self._lock:
            s = self._need(sid)
            if sid not in self._running:
                return
            adapter = self._adapters[s["provider_id"]]
            psid = s["session"]["session_id"]
        adapter.cancel(psid)

    def close(self, sid: str) -> None:
        """Fecha no provedor (obrigatorio: o claude-agent-acp segura um processo por sessao) e mantem o registro."""
        with self._lock:
            s = self._need(sid)
            if s["state"] == "CLOSED":
                return
            adapter = self._adapters.get(s["provider_id"])
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
            self._drop_idle_adapter(s["provider_id"])
            self._save()

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid)
        with self._lock:
            for pid in list(self._adapters):
                self._adapters.pop(pid).disconnect()

    def _drop_idle_adapter(self, provider_id: str) -> None:
        if self.open_count(provider_id) == 0 and provider_id in self._adapters:
            self._adapters.pop(provider_id).disconnect()

    def recover(self, sid: str) -> dict:
        """Reabre a sessao: reconecta o provedor se preciso; com resume, volta com contexto; sem, abre sessao nova com
        o mesmo modelo/esforco pedidos e marca `recovered_without_context`."""
        with self._lock:
            s = self._need(sid)
            if s["state"] in ("READY", "RUNNING"):
                return self._public(s)
            if self.open_count(s["provider_id"]) >= self.limit_for(s["provider_id"]):
                raise SessionLimitReached(f"{s['provider_id']}: limite de sessoes abertas atingido")
            pid = s["provider_id"]
            old = self._adapters.pop(pid, None)
            if old is not None and s["state"] == "ERROR":
                try:
                    old.disconnect()
                except Exception:                                             # noqa: BLE001 - processo ja morto
                    pass
                old = None
            if old is not None:
                self._adapters[pid] = old
            adapter = self._adapter(pid)
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
                if other is not s and other["provider_id"] == pid and other["state"] in ("READY", "RUNNING") \
                        and other["session"]["session_id"] not in adapter.get_status().sessions:
                    other["state"], other["error"] = "ERROR", "o processo do provedor foi reiniciado"
            self._save()
            return self._public(s)

    # ------------------------------------------------------------------------------------------------ persistencia
    def _save(self) -> None:
        if self.store_path is None:
            return
        data = {"schema": SCHEMA, "sessions": list(self._sessions.values())}
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
        for s in data.get("sessions") or []:
            if isinstance(s, dict) and s.get("id"):
                if s.get("state") != "CLOSED":                                # processo da execucao anterior nao existe mais
                    s["state"], s["error"] = "CLOSED", "o Studio foi fechado"
                self._sessions[s["id"]] = s

    def __enter__(self) -> SessionManager:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close_all()
