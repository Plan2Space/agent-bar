"""fake_adapter.py — adaptador de referencia (provedor simulado) que passa na suite de contrato, e variantes DEFEITUOSAS
para o autoteste do harness: cada uma quebra uma regra e tem de reprovar.

O provedor simulado sobe um processo filho de verdade (um `python` dormindo) para que posse de processo, ambiente
isolado e limpeza no disconnect sejam testados como num adaptador de CLI/ACP.
"""
from __future__ import annotations

import concurrent.futures as cf
import itertools
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from agent_bar.cockpit.acp import creation_flags, isolated_env
from agent_bar.platform.adapter import (
    AdapterStatus,
    AgentEvent,
    CapabilityUnsupported,
    EffortRejected,
    ModelRejected,
    NotConnected,
    PromptResult,
    ProviderAdapter,
    SessionNotFound,
    SessionRequest,
    load_images,
)
from agent_bar.platform.contracts import all_capabilities, capability

VERSION = "9.9.9-fake"
MODELS = {
    "fake-small": [],                                                        # sem esforco
    "fake-think": ["low", "medium", "high"],
}


class FakeAdapter(ProviderAdapter):
    adapter_id = "fake"

    def __init__(self, env: Mapping[str, str] | None = None, spawn: bool = True):
        self.env = dict(env or {})
        self.spawn = spawn
        self._proc: subprocess.Popen | None = None
        self._connected = False
        self._sessions: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._ids = itertools.count(1)
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="fake-adapter")
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------------------------- descricao
    def capabilities(self) -> dict:
        ev = f"fake {VERSION}"
        return all_capabilities(
            "SUPPORTED", ev, VERSION,
            images=capability("SUPPORTED", f"{ev}: so o fake-think ve imagem", VERSION),
        )

    def describe(self) -> dict:
        return {
            "schema": "p2s.provider.v1", "provider_id": "fake", "display_name": "Provedor simulado",
            "provider_type": "CLI", "adapter_id": self.adapter_id, "executable_path": sys.executable, "endpoint": None,
            "version": VERSION, "auth_status": "UNSUPPORTED",
            "connection_status": "CONNECTED" if self._connected else "DISCONNECTED",
            "capabilities": self.capabilities(), "discovery_source": "teste", "last_verified_at": None,
            "cost_class": "local",
        }

    # -------------------------------------------------------------------------------------------- ciclo de vida
    def process_env(self, base: Mapping[str, str]) -> dict[str, str] | None:
        return isolated_env(base)

    def connect(self, timeout: float = 30.0) -> None:
        if self._connected:
            return
        if self.spawn:
            self._proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                          env={**self.process_env(self.env), "SYSTEMROOT": "C:\\Windows"},
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=creation_flags())
        self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            for ev in self._cancel.values():
                ev.set()
            self._connected = False
            self._sessions.clear()
        if self._proc is not None:
            self._kill()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="fake-adapter")

    def _kill(self) -> None:
        self._proc.kill()
        self._proc.wait(timeout=10)
        self._proc = None

    def owned_pids(self) -> list[int]:
        return [self._proc.pid] if self._proc is not None and self._proc.poll() is None else []

    def get_status(self) -> AdapterStatus:
        return AdapterStatus("CONNECTED" if self._connected else "DISCONNECTED", tuple(self._sessions), "")

    def _need(self) -> None:
        if not self._connected:
            raise NotConnected("fake")

    # -------------------------------------------------------------------------------------------- modelos
    def list_models(self) -> list[dict]:
        return [{
            "schema": "p2s.model.v1", "model_id": mid, "display_name": mid.replace("-", " ").title(), "provider_id": "fake",
            "transport": "CLI", "availability": "AVAILABLE", "tools": "SUPPORTED",
            "images": "SUPPORTED" if mid == "fake-think" else "UNSUPPORTED",
            "effort_levels": list(levels), "limits": {"context_tokens": 8192}, "source": "lista fixa do simulador",
        } for mid, levels in MODELS.items()]

    def list_effort_levels(self, model_id: str | None = None) -> list[str]:
        return list(MODELS.get(model_id or "fake-small", []))

    # -------------------------------------------------------------------------------------------- sessoes
    def _accept(self, req: SessionRequest) -> tuple[str, str | None]:
        model = req.model_id or "fake-small"
        if model not in MODELS:
            raise ModelRejected(model)
        if req.effort is not None and req.effort not in MODELS[model]:
            raise EffortRejected(f"{model} nao aceita {req.effort}")
        return model, req.effort

    def create_session(self, request: SessionRequest) -> dict:
        self._need()
        model, effort = self._accept(request)
        sid = f"fake-{next(self._ids)}"
        rec = {
            "schema": "p2s.session.v1", "session_id": sid, "provider_id": "fake",
            "model_requested": request.model_id, "model_accepted": model,
            "effort_requested": request.effort, "effort_accepted": effort,
            "workspace": request.workspace,
            "links": {k: request.links.get(k) for k in ("spec", "issue", "pr", "run")},
            "state": "READY", "created_at": datetime(2026, 11, 22, tzinfo=UTC).isoformat(),
        }
        with self._lock:
            self._sessions[sid] = rec
            self._cancel[sid] = threading.Event()
        return dict(rec)

    def resume_session(self, session_id: str) -> dict:
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        return dict(self._sessions[session_id])

    def send_prompt(self, session_id: str, text: str, on_event, images=()) -> cf.Future:
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        payloads = self._check_images(session_id, images)
        stop = self._cancel[session_id]
        stop.clear()
        rec = self._sessions[session_id]
        slow = "devagar" in text

        def work() -> PromptResult:
            on_event(AgentEvent("status", "pensando"))
            for i in range(200 if slow else 2):
                if stop.is_set():
                    return PromptResult("cancelled")
                on_event(AgentEvent("text", ("ok" if not payloads else f"imagens={len(payloads)}") if not slow else f"{i + 1}\n"))
                if slow:
                    time.sleep(0.05)
            return PromptResult("end_turn", rec["model_accepted"], rec["effort_accepted"])
        return self._pool.submit(work)

    def _check_images(self, session_id: str, images):
        if not images:
            return []
        model = self._sessions[session_id]["model_accepted"]
        if model != "fake-think":
            raise CapabilityUnsupported("images", f"{model} nao ve imagem")
        return load_images(images)

    def cancel(self, session_id: str) -> None:
        self._need()
        if session_id not in self._cancel:
            raise SessionNotFound(session_id)
        self._cancel[session_id].set()

    def close_session(self, session_id: str) -> None:
        self._need()
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFound(session_id)
            self._cancel.pop(session_id).set()
            del self._sessions[session_id]


# ------------------------------------------------------------------------------------------------- defeituosos
class NoopCancel(FakeAdapter):
    """Anuncia cancel, mas o cancel nao faz nada."""

    def cancel(self, session_id: str) -> None:
        return None


class SilentUnsupported(FakeAdapter):
    """Diz que nao suporta resume, mas devolve None em vez de recusar."""

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps["resume"] = capability("UNSUPPORTED", "fake", VERSION)
        return caps

    def resume_session(self, session_id: str) -> dict:
        return None  # type: ignore[return-value]


class EchoEffort(FakeAdapter):
    """Aceita qualquer esforco e o ecoa como aceito (esforco simulado)."""

    def _accept(self, req: SessionRequest) -> tuple[str, str | None]:
        model = req.model_id or "fake-small"
        if model not in MODELS:
            raise ModelRejected(model)
        return model, req.effort


class AcceptsAnyModel(FakeAdapter):
    """Confirma como aceito um modelo que nao existe."""

    def _accept(self, req: SessionRequest) -> tuple[str, str | None]:
        if req.model_id and req.model_id not in MODELS:
            return req.model_id, None
        return super()._accept(req)


class LeakyEnv(FakeAdapter):
    """Repassa o ambiente inteiro (com tokens) ao processo filho."""

    def process_env(self, base: Mapping[str, str]) -> dict[str, str] | None:
        return dict(base)


class LeakyStatus(FakeAdapter):
    """Poe um segredo do ambiente no texto de status."""

    def get_status(self) -> AdapterStatus:
        st = super().get_status()
        return AdapterStatus(st.connection_status, st.sessions, f"auth={self.env.get('ANTHROPIC_API_KEY', '')}")


class OrphanProcess(FakeAdapter):
    """Esquece de encerrar o processo que iniciou."""

    def _kill(self) -> None:
        self._orphan = self._proc
        self._proc = None

    def owned_pids(self) -> list[int]:
        p = self._proc or getattr(self, "_orphan", None)
        return [p.pid] if p is not None and p.poll() is None else []


class CloseLeavesSessionOpen(FakeAdapter):
    """Diz que fechou, mas a sessao continua listada e aceitando prompt."""

    def close_session(self, session_id: str) -> None:
        self._need()


class DropsImagesSilently(FakeAdapter):
    """Diz que nao ve imagem, mas aceita a imagem e a ignora (o agente nunca a recebe)."""

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps["images"] = capability("UNSUPPORTED", "fake", VERSION)
        return caps

    def list_models(self) -> list[dict]:
        return [{**m, "images": "UNSUPPORTED"} for m in super().list_models()]

    def _check_images(self, session_id: str, images):
        return []


class AcceptsAnyImageFile(FakeAdapter):
    """Diz que ve imagem, mas nao confere o arquivo (manda lixo/nada ao agente)."""

    def _check_images(self, session_id: str, images):
        return list(images or ())


class InvalidProvider(FakeAdapter):
    """Registro de provedor sem `cost_class`."""

    def describe(self) -> dict:
        rec = super().describe()
        rec.pop("cost_class")
        return rec


class WorksAfterDisconnect(FakeAdapter):
    """Continua criando sessao depois do disconnect."""

    def _need(self) -> None:
        return None


class ClaimsCapabilityItLacks(FakeAdapter):
    """Diz UNSUPPORTED para effort_select, mas list_effort_levels responde normalmente."""

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps["effort_select"] = capability("UNSUPPORTED", "fake", VERSION)
        return caps


DEFECTIVE = {
    NoopCancel: "cancel",
    SilentUnsupported: "unsupported_raise_typed",
    EchoEffort: "effort_honest",
    AcceptsAnyModel: "unknown_model_rejected",
    LeakyEnv: "no_secret_leak",
    LeakyStatus: "no_secret_leak",
    OrphanProcess: "disconnect",
    InvalidProvider: "provider_record",
    WorksAfterDisconnect: "disconnect",
    ClaimsCapabilityItLacks: "unsupported_raise_typed",
    CloseLeavesSessionOpen: "close_session",
    DropsImagesSilently: "images",
    AcceptsAnyImageFile: "images",
}
