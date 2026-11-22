"""adapter.py — interface comum de adaptador de provedor (SPEC §4) e erros tipados.

Regras que a suite de contrato (`tests/platform/adapter_contract.py`) cobra de todo adaptador:
- operacao nao suportada levanta `CapabilityUnsupported`, nunca um no-op silencioso nem `None`;
- modelo/esforco so aparecem como ACEITOS quando o provedor confirmou (`model_accepted`/`effort_accepted` = null ate la);
- esforco fora dos niveis anunciados para o modelo e recusado (`EffortRejected`), nunca simulado;
- `cancel` faz o prompt em curso terminar com `stop_reason == "cancelled"`;
- `disconnect` e idempotente e encerra so os processos que o proprio adaptador iniciou (`owned_pids`);
- nenhum segredo do ambiente chega ao processo filho (`process_env`) nem a descricao, status, modelos, sessao ou eventos.

Interface sincrona e segura entre threads; so `send_prompt` devolve um `concurrent.futures.Future` (a UI nunca bloqueia).
"""
from __future__ import annotations

import concurrent.futures as cf
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_bar.platform.contracts import STOP_REASONS

EVENT_KINDS = ("text", "thought", "tool_call", "tool_update", "plan", "usage", "status")


# ---------------------------------------------------------------------------------------------------------- erros
class AdapterError(Exception):
    """Base. `code` curto e estavel para a UI e para o log (nunca com conteudo do prompt nem segredo)."""

    code = "ADAPTER_ERROR"

    def __init__(self, message: str = ""):
        super().__init__(f"{self.code}: {message}" if message else self.code)
        self.message = message


class CapabilityUnsupported(AdapterError):
    code = "CAPABILITY_UNSUPPORTED"

    def __init__(self, capability: str, message: str = ""):
        super().__init__(f"{capability}: {message}" if message else capability)
        self.capability = capability


class NotConnected(AdapterError):
    code = "NOT_CONNECTED"


class ProviderUnavailable(AdapterError):
    code = "PROVIDER_UNAVAILABLE"


class AuthRequired(AdapterError):
    code = "AUTH_REQUIRED"


class ModelRejected(AdapterError):
    code = "MODEL_REJECTED"


class EffortRejected(AdapterError):
    code = "EFFORT_REJECTED"


class SessionNotFound(AdapterError):
    code = "SESSION_NOT_FOUND"


class AdapterTimeout(AdapterError):
    code = "TIMEOUT"


class PromptFailed(AdapterError):
    code = "PROMPT_FAILED"


# ---------------------------------------------------------------------------------------------------------- dados
@dataclass(frozen=True)
class AgentEvent:
    kind: str                                                                # um de EVENT_KINDS
    text: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptResult:
    stop_reason: str                                                         # um de STOP_REASONS
    model_confirmed: str | None = None                                       # o que o provedor disse ter usado neste turno
    effort_confirmed: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.stop_reason not in STOP_REASONS:
            raise ValueError(f"stop_reason fora do contrato: {self.stop_reason!r}")


@dataclass(frozen=True)
class SessionRequest:
    model_id: str | None = None                                              # None = padrao do provedor
    effort: str | None = None                                                # None = padrao do provedor
    workspace: str | None = None
    links: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterStatus:
    connection_status: str                                                   # um de CONNECTION_STATUSES
    sessions: tuple[str, ...] = ()
    detail: str = ""                                                         # curto, sanitizado (sem saida bruta)


EventSink = Callable[[AgentEvent], None]


# ---------------------------------------------------------------------------------------------------------- base
class ProviderAdapter(ABC):
    """Base: o que depende de capacidade ja nasce recusando com `CapabilityUnsupported`; o adaptador sobrescreve so o
    que comprovou suportar (e declara SUPPORTED em `describe()` com a evidencia)."""

    adapter_id: str = ""

    @abstractmethod
    def describe(self) -> dict:
        """Registro Provider v1 atual (valido por `validate_provider`)."""

    @abstractmethod
    def connect(self, timeout: float = 30.0) -> None: ...

    @abstractmethod
    def disconnect(self) -> None:
        """Idempotente. Depois dele, `get_status().connection_status == "DISCONNECTED"` e `owned_pids()` morreram."""

    @abstractmethod
    def get_status(self) -> AdapterStatus: ...

    @abstractmethod
    def list_models(self) -> list[dict]:
        """Model v1. Provedor sem selecao de modelo devolve o modelo fixo que conhece, ou [] se nao sabe."""

    def list_effort_levels(self, model_id: str | None = None) -> list[str]:
        raise CapabilityUnsupported("effort_select")

    @abstractmethod
    def create_session(self, request: SessionRequest) -> dict:
        """Session v1. Esforco fora de `list_effort_levels(model)` -> EffortRejected; modelo fora de `list_models` ->
        ModelRejected."""

    def resume_session(self, session_id: str) -> dict:
        raise CapabilityUnsupported("resume")

    @abstractmethod
    def send_prompt(self, session_id: str, text: str, on_event: EventSink) -> cf.Future:
        """Future[PromptResult]. Eventos chegam em `on_event` na thread do adaptador (a UI reencaminha)."""

    def cancel(self, session_id: str) -> None:
        raise CapabilityUnsupported("cancel")

    def owned_pids(self) -> list[int]:
        """PIDs que ESTE adaptador iniciou e ainda nao encerrou (vazio para HTTP)."""
        return []

    def process_env(self, base: Mapping[str, str]) -> dict[str, str] | None:
        """Ambiente que o processo filho receberia a partir de `base`; None = adaptador sem processo."""
        return None
