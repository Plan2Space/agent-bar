"""ollama.py — adaptador LOCAL_HTTP do Ollama (F2a): streaming, cancelamento, modelos reais e esforco so onde existe.

Tudo vem do servidor local, nada e presumido:
- modelos: `GET /api/tags` + `POST /api/show` por modelo. So entra quem anuncia `completion` (embedding nao conversa);
  ferramentas, imagens e contexto saem de `capabilities`/`model_info` do proprio Ollama;
- esforco: so para modelo que anuncia `thinking`, com os valores que o `/api/show` lista em `thinking.values`
  (booleanos viram "off"/"on"; valores de texto, como low/medium/high, passam como estao). Modelo sem thinking = [];
- conversa: `POST /api/chat` com `stream: true`; `message.thinking` vira evento "thought", `message.content` vira "text";
  o modelo confirmado e o `model` que o servidor devolve no fim do turno;
- cancelamento: fecha a resposta HTTP em curso (o Ollama para de gerar quando o cliente desconecta).

Nao ha processo filho (o servidor e do usuario): `owned_pids()` vazio e `process_env()` None. O historico da sessao
fica so na memoria do adaptador; o Ollama nao guarda sessao, entao `resume` e UNSUPPORTED. Ferramentas tambem ficam
UNSUPPORTED aqui: o adaptador ainda nao executa tool calls (anunciar seria mentir).
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import itertools
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent_bar.platform.adapter import (
    AdapterStatus,
    AgentEvent,
    CapabilityUnsupported,
    EffortRejected,
    ModelRejected,
    NotConnected,
    PromptFailed,
    PromptResult,
    ProviderAdapter,
    ProviderUnavailable,
    SessionNotFound,
    SessionRequest,
    load_images,
)
from agent_bar.platform.contracts import all_capabilities, capability
from agent_bar.platform.discovery import OLLAMA_DEFAULT
from agent_bar.platform.usage import from_ollama_done

Opener = Callable[..., Any]


def effort_levels_from_show(show: dict) -> list[str]:
    """Niveis de esforco que o proprio Ollama anuncia para o modelo. Sem `thinking` nas capabilities = []."""
    if "thinking" not in (show.get("capabilities") or []):
        return []
    values = (show.get("thinking") or {}).get("values")
    if not isinstance(values, list) or not values:
        return ["off", "on"]                                                 # capability sem lista: o think booleano do /api/chat
    out = []
    for v in values:
        if isinstance(v, bool):
            out.append("on" if v else "off")
        elif isinstance(v, str) and v:
            out.append(v)
    return out


def _ollama_error(exc: urllib.error.HTTPError) -> str:
    """O campo `error` do corpo da resposta do Ollama, curto e numa linha (ex.: contexto excedido); vazio se não há."""
    try:
        msg = str(json.loads(exc.read(2048).decode("utf-8", "replace")).get("error") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    msg = " ".join(msg.split())[:200]
    return f": {msg}" if msg else ""


def think_param(level: str | None) -> bool | str | None:
    if level is None:
        return None
    return {"off": False, "on": True}.get(level, level)


class OllamaAdapter(ProviderAdapter):
    adapter_id = "ollama"
    supports_context_tokens = True                                            # options.num_ctx

    def __init__(self, endpoint: str = OLLAMA_DEFAULT, env: Any = None, opener: Opener = urllib.request.urlopen,
                 timeout: float = 10.0):
        self.endpoint = endpoint.rstrip("/")
        self.opener = opener
        self.timeout = timeout
        self.version: str | None = None
        self._connected = False
        self._models: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._history: dict[str, list[dict]] = {}
        self._num_ctx: dict[str, int] = {}                                    # sid -> options.num_ctx pedido
        self._digests: dict[str, str] = {}                                    # model_id -> digest do /api/tags
        self._running: dict[str, dict] = {}                                   # sid -> {"resp": ..., "cancel": Event}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ollama")
        self._verified_at: str | None = None

    # ------------------------------------------------------------------------------------------------ http
    def _get(self, path: str) -> dict:
        with self.opener(f"{self.endpoint}{path}", timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, body: dict, stream: bool = False, timeout: float | None = None):
        req = urllib.request.Request(f"{self.endpoint}{path}", json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        resp = self.opener(req, timeout=timeout or self.timeout)
        if stream:
            return resp
        with resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------------------------------------ descricao
    def describe(self) -> dict:
        ev = f"ollama {self.version}" if self.version else "ollama (nao conectado)"
        v = self.version
        thinking = any(m["effort_levels"] for m in self._models.values())
        caps = all_capabilities(
            "UNKNOWN", "nao verificado",
            streaming=capability("SUPPORTED", f"{ev}: POST /api/chat stream=true", v),
            sessions=capability("SUPPORTED", "historico mantido pelo adaptador (o Ollama nao guarda sessao)", v),
            resume=capability("UNSUPPORTED", "o Ollama nao tem sessao no servidor", v),
            tool_calls=capability("UNSUPPORTED", "o adaptador ainda nao executa tool calls", v),
            cancel=capability("SUPPORTED", f"{ev}: fechar a resposta HTTP interrompe a geracao", v),
            model_select=capability("SUPPORTED", f"{ev}: GET /api/tags + POST /api/show", v),
            effort_select=capability("SUPPORTED" if thinking else "UNSUPPORTED",
                                     f"{ev}: thinking.values do /api/show" if thinking else
                                     f"{ev}: nenhum modelo local anuncia thinking", v),
            images=capability("SUPPORTED" if any(m["images"] == "SUPPORTED" for m in self._models.values()) else "UNSUPPORTED",
                              f"{ev}: capabilities.vision do /api/show (imagem em base64 no /api/chat)", v),
        )
        if not self._connected and not self.version:
            caps = all_capabilities(method="nao verificado: desconectado")
        return {
            "schema": "p2s.provider.v1", "provider_id": "ollama", "display_name": "Ollama (local)",
            "provider_type": "LOCAL_HTTP", "adapter_id": self.adapter_id, "executable_path": None,
            "endpoint": self.endpoint, "version": self.version, "auth_status": "UNSUPPORTED",
            "connection_status": "CONNECTED" if self._connected else "DISCONNECTED",
            "capabilities": caps, "discovery_source": "endpoint configurado", "last_verified_at": self._verified_at,
            "cost_class": "local",
        }

    # ------------------------------------------------------------------------------------------------ ciclo de vida
    def connect(self, timeout: float = 30.0) -> None:
        if self._connected:
            return
        try:
            data = self._get("/api/version")
            tags = self._get("/api/tags")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderUnavailable(f"servidor local nao respondeu em {self.endpoint}") from exc
        self.version = str(data.get("version") or "") or None
        models: dict[str, dict] = {}
        digests: dict[str, str] = {}
        for m in tags.get("models") or []:
            name = str(m.get("name") or m.get("model") or "")
            if not name:
                continue
            if m.get("digest"):
                digests[name] = str(m["digest"])                            # GET /api/tags: digest do blob instalado
            try:
                show = self._post("/api/show", {"model": name})
            except (urllib.error.URLError, OSError, ValueError):
                continue                                                     # sem /api/show nao ha evidencia: fica fora
            caps = show.get("capabilities") or []
            if "completion" not in caps:
                continue                                                     # embedding etc. nao conversa
            ctx = next((v for k, v in (show.get("model_info") or {}).items()
                        if k.endswith("context_length") and isinstance(v, int)), None)
            models[name] = {
                "schema": "p2s.model.v1", "model_id": name, "display_name": name, "provider_id": "ollama",
                "transport": "LOCAL_HTTP", "availability": "AVAILABLE",
                "tools": "SUPPORTED" if "tools" in caps else "UNSUPPORTED",
                "images": "SUPPORTED" if "vision" in caps else "UNSUPPORTED",
                "effort_levels": effort_levels_from_show(show), "limits": {"context_tokens": ctx},
                "source": "GET /api/tags + POST /api/show",
            }
        self._models = models
        self._digests = {k: v for k, v in digests.items() if k in models}
        self._verified_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            running = list(self._running.values())
            self._connected = False
            self._sessions.clear()
            self._history.clear()
        for r in running:
            r["cancel"].set()
            self._close_resp(r)
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ollama")

    def get_status(self) -> AdapterStatus:
        return AdapterStatus("CONNECTED" if self._connected else "DISCONNECTED", tuple(self._sessions),
                             f"{len(self._models)} modelo(s) de conversa" if self._connected else "")

    def _need(self) -> None:
        if not self._connected:
            raise NotConnected("ollama")

    # ------------------------------------------------------------------------------------------------ modelos
    def list_models(self) -> list[dict]:
        return [dict(m) for m in self._models.values()]

    def list_effort_levels(self, model_id: str | None = None) -> list[str]:
        if not any(m["effort_levels"] for m in self._models.values()):
            return super().list_effort_levels(model_id)                      # CapabilityUnsupported
        model = self._models.get(model_id or self._default_model() or "")
        if model is None:
            raise ModelRejected(str(model_id))
        return list(model["effort_levels"])

    def _default_model(self) -> str | None:
        return next(iter(self._models), None)

    def model_digest(self, model_id: str) -> str:
        self._need()
        d = self._digests.get(model_id)
        if not d:
            raise CapabilityUnsupported("model_digest", f"{model_id}: /api/tags sem digest")
        return d

    # ------------------------------------------------------------------------------------------------ sessoes
    def create_session(self, request: SessionRequest) -> dict:
        self._need()
        model = request.model_id or self._default_model()
        if model is None or model not in self._models:
            raise ModelRejected(str(request.model_id or "nenhum modelo de conversa no servidor"))
        levels = self._models[model]["effort_levels"]
        if request.effort is not None and request.effort not in levels:
            raise EffortRejected(f"{model} nao anuncia o esforco {request.effort!r} (anuncia {levels})")
        limit = (self._models[model].get("limits") or {}).get("context_tokens")
        ctx = request.context_tokens
        if ctx is not None and (ctx <= 0 or (limit and ctx > limit)):
            raise ModelRejected(f"{model}: contexto {ctx} fora do limite anunciado ({limit})")
        sid = f"ollama-{next(self._ids)}"
        rec = {
            "schema": "p2s.session.v1", "session_id": sid, "provider_id": "ollama",
            "model_requested": request.model_id, "model_accepted": model,          # /api/show confirmou que existe local
            "effort_requested": request.effort, "effort_accepted": request.effort,  # validado contra thinking.values
            "workspace": request.workspace,
            "links": {k: request.links.get(k) for k in ("spec", "issue", "pr", "run")},
            "state": "READY", "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._sessions[sid] = rec
            self._history[sid] = []
            if ctx is not None:                                               # fora do Session v1: só vira num_ctx
                self._num_ctx[sid] = ctx
        return dict(rec)

    def send_prompt(self, session_id: str, text: str, on_event, images=()) -> cf.Future:
        self._need()
        payloads = []
        if images:                                                           # antes de reservar o turno: recusa limpa
            if session_id not in self._sessions:
                raise SessionNotFound(session_id)
            model = self._sessions[session_id]["model_accepted"]
            if self._models.get(model, {}).get("images") != "SUPPORTED":
                raise CapabilityUnsupported("images", f"{model} nao anuncia vision no /api/show")
            payloads = load_images(images)
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFound(session_id)
            if session_id in self._running:
                raise PromptFailed("ja ha um turno em curso nesta sessao")
            run = {"resp": None, "cancel": threading.Event()}
            self._running[session_id] = run
        rec = self._sessions[session_id]
        messages = [*self._history[session_id], {"role": "user", "content": text}]   # historico: so o texto
        sent = [*messages[:-1], {**messages[-1], "images": [base64.b64encode(p.data).decode("ascii") for p in payloads]}] \
            if payloads else messages                                        # a imagem vai so neste turno
        body: dict = {"model": rec["model_accepted"], "messages": sent, "stream": True}
        if self._num_ctx.get(session_id):
            body["options"] = {"num_ctx": self._num_ctx[session_id]}
        think = think_param(rec["effort_accepted"])
        if think is not None:
            body["think"] = think

        def work() -> PromptResult:
            try:
                return self._stream(session_id, run, body, messages, on_event)
            finally:
                with self._lock:
                    self._running.pop(session_id, None)
        return self._pool.submit(work)

    def _stream(self, sid: str, run: dict, body: dict, messages: list[dict], on_event) -> PromptResult:
        text: list[str] = []
        confirmed = None
        try:
            resp = self._post("/api/chat", body, stream=True, timeout=300)
        except urllib.error.HTTPError as exc:
            raise PromptFailed(f"HTTP {exc.code}{_ollama_error(exc)}") from None
        except (urllib.error.URLError, OSError) as exc:
            if run["cancel"].is_set():
                return PromptResult("cancelled")
            raise PromptFailed("servidor local nao respondeu") from exc
        run["resp"] = resp
        if run["cancel"].is_set():
            self._close_resp(run)
            return PromptResult("cancelled")
        try:
            for raw in resp:
                if run["cancel"].is_set():
                    break
                line = raw.strip()
                if not line:
                    continue
                msg = json.loads(line.decode("utf-8"))
                if msg.get("error"):
                    raise PromptFailed("o servidor devolveu erro no stream")
                part = msg.get("message") or {}
                if part.get("thinking"):
                    on_event(AgentEvent("thought", str(part["thinking"])))
                if part.get("content"):
                    text.append(str(part["content"]))
                    on_event(AgentEvent("text", str(part["content"])))
                if msg.get("done"):
                    confirmed = msg.get("model")
                    model = str(confirmed or body.get("model") or "")
                    max_ctx = ((self._models.get(body.get("model")) or {}).get("limits") or {}).get("context_tokens")
                    snap = from_ollama_done(msg, provider_id="ollama", model_id=model, model_max_context=max_ctx,
                                            num_ctx=(body.get("options") or {}).get("num_ctx"))
                    on_event(AgentEvent("usage", "", snap.to_dict()))           # F6: contadores reais do turno
                    break
        except (OSError, ValueError, AttributeError):
            if not run["cancel"].is_set():
                raise PromptFailed("stream interrompido") from None
        finally:
            self._close_resp(run)
        if run["cancel"].is_set() or confirmed is None:
            if run["cancel"].is_set():
                return PromptResult("cancelled")
            raise PromptFailed("stream terminou sem done")
        with self._lock:
            if sid in self._history:                                         # so turno completo entra no historico
                self._history[sid] = [*messages, {"role": "assistant", "content": "".join(text)}]
        rec = self._sessions.get(sid) or {}
        return PromptResult("end_turn", str(confirmed), rec.get("effort_accepted"))

    @staticmethod
    def _close_resp(run: dict) -> None:
        resp = run.get("resp")
        if resp is not None:
            try:
                resp.close()
            except Exception:                                                 # noqa: BLE001 - fechar e o melhor esforco
                pass

    def cancel(self, session_id: str) -> None:
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        with self._lock:
            run = self._running.get(session_id)
        if run is not None:
            run["cancel"].set()
            self._close_resp(run)

    def close_session(self, session_id: str) -> None:
        """Interrompe o turno em curso (se houver) e esquece a sessao e o historico dela."""
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        self.cancel(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)
            self._history.pop(session_id, None)
            self._num_ctx.pop(session_id, None)
