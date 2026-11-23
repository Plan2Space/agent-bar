"""acp_adapter.py — agente ACP (Claude via claude-agent-acp, Codex via codex-acp) como `ProviderAdapter` da F1.

Reaproveita o `AcpClient` do cockpit (ambiente isolado, Job Object, encerramento so do que iniciou). UM processo de
agente por adaptador e varias sessoes nele (`session/new` com `sessionId` explicito), como os editores ACP fazem:
abrir 80 sessoes nao abre 80 processos (a primeira versao, um processo por sessao, estourou na suite real).

O que vale e o que o agente anuncia e confirma:
- modelos = opcoes do configOption "model"; esforco por modelo = opcoes do configOption de categoria `thought_level`
  com aquele modelo selecionado (levantado no `connect` na sessao de sondagem, voltando ao modelo inicial);
- `model_accepted`/`effort_accepted` = `currentValue` devolvido pelo agente no `session/set_config_option`
  (resposta sem configOptions = nao confirmado = erro);
- modo de permissao: toda sessao nova que abre num modo que decide sozinho (o claude-agent-acp 0.109 abre em "auto")
  e trocada para o modo que pede permissao antes de ser entregue; sem modo seguro, a sessao e recusada.
Permissao de ferramenta vai ao `on_permission` recebido (a UI); sem ele, o pedido e recusado (`cancelled`).
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import shutil
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_bar.cockpit.acp import (
    AcpClient,
    AcpError,
    Update,
    config_values,
    find_config,
    isolated_env,
    mode_is_safe,
    safe_mode_id,
)
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
from agent_bar.platform.contracts import STOP_REASONS, all_capabilities, capability
from agent_bar.platform.usage import from_acp_prompt_usage, from_acp_usage_update

KIND_MAP = {"agent_message_chunk": "text", "agent_thought_chunk": "thought", "tool_call": "tool_call",
            "tool_call_update": "tool_update", "plan": "plan"}
TIMEOUT = 120.0


def _names(option: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(i["value"]): str(i.get("name") or i["value"])
            for i in (option or {}).get("options") or [] if isinstance(i, dict) and "value" in i}


class AcpAdapter(ProviderAdapter):
    adapter_id = "acp"

    def __init__(self, argv: Sequence[str], cwd: str | Path, env: Mapping[str, str] | None = None, *,
                 provider_id: str = "claude-acp", display_name: str = "Claude Code (ACP)",
                 cost_class: str = "subscription_cli", on_permission=None):
        self.argv = list(argv)
        self.cwd = str(Path(cwd).resolve())
        self.env = dict(env) if env is not None else None
        self.provider_id, self.display_name, self.cost_class = provider_id, display_name, cost_class
        self.on_permission = on_permission
        self.client: AcpClient | None = None
        self._connected = False
        self._agent: dict = {}
        self._models: dict[str, dict] = {}
        self._probe_free = False                                             # a sessao de sondagem ainda nao foi entregue
        self._sessions: dict[str, dict] = {}
        self._config: dict[str, list[dict]] = {}                             # configOptions confirmadas por sessao
        self._sinks: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._verified_at: str | None = None

    # ------------------------------------------------------------------------------------------------ processo
    def process_env(self, base: Mapping[str, str]) -> dict[str, str] | None:
        return isolated_env(base)

    def _on_update(self, u: Update) -> None:
        sink = self._sinks.get(u.session_id)
        kind = KIND_MAP.get(u.kind)
        if u.kind == "config_option_update" and u.session_id in self._config and isinstance(u.data.get("configOptions"), list):
            self._config[u.session_id] = list(u.data["configOptions"])
        if sink is not None and kind is not None:
            sink(AgentEvent(kind, u.text, dict(u.data)))
        elif sink is not None and u.kind == "usage_update":                  # F6: used/size/cost do agente
            model = str((self._sessions.get(u.session_id) or {}).get("model_accepted") or "")
            snap = from_acp_usage_update(u.data, provider_id=self.provider_id, model_id=model)
            sink(AgentEvent("usage", "", snap.to_dict()))

    def owned_pids(self) -> list[int]:
        c = self.client
        return [c.pid] if c is not None and c.pid and c.alive else []

    # ------------------------------------------------------------------------------------------------ descricao
    def describe(self) -> dict:
        info = self._agent.get("agentInfo") or {}
        v = str(info.get("version")) if info.get("version") else None
        ev = f"{info.get('name', self.argv[0])} {v or ''}".strip()
        if not self._agent:
            caps = all_capabilities(method="nao verificado: desconectado")
        else:
            acaps = self._agent.get("agentCapabilities") or {}
            any_effort = any(m["effort_levels"] for m in self._models.values())
            caps = all_capabilities(
                "UNKNOWN", "nao verificado",
                streaming=capability("SUPPORTED", f"{ev}: session/update", v),
                sessions=capability("SUPPORTED", f"{ev}: varias session/new no mesmo processo", v),
                resume=capability("SUPPORTED" if acaps.get("loadSession") else "UNSUPPORTED",
                                  f"{ev}: agentCapabilities.loadSession={bool(acaps.get('loadSession'))}", v),
                tool_calls=capability("SUPPORTED", f"{ev}: tool_call + session/request_permission", v),
                cancel=capability("SUPPORTED", f"{ev}: session/cancel", v),
                model_select=capability("SUPPORTED" if self._models else "UNSUPPORTED",
                                        f"{ev}: configOption model" if self._models else f"{ev}: sem configOption model", v),
                effort_select=capability("SUPPORTED" if any_effort else "UNSUPPORTED",
                                         f"{ev}: configOption thought_level por modelo" if any_effort
                                         else f"{ev}: sem configOption thought_level", v),
                images=capability("SUPPORTED" if self._images_supported() else "UNSUPPORTED",
                                  f"{ev}: agentCapabilities.promptCapabilities.image={self._images_supported()}", v),
            )
        return {
            "schema": "p2s.provider.v1", "provider_id": self.provider_id, "display_name": self.display_name,
            "provider_type": "ACP", "adapter_id": self.adapter_id,
            "executable_path": shutil.which(self.argv[0]) or (self.argv[0] if Path(self.argv[0]).is_file() else None),
            "endpoint": None, "version": v, "auth_status": "UNKNOWN",
            "connection_status": "CONNECTED" if self._connected else "DISCONNECTED",
            "capabilities": caps, "discovery_source": f"argv ({self.argv[0]})", "last_verified_at": self._verified_at,
            "cost_class": self.cost_class,
        }

    # ------------------------------------------------------------------------------------------------ ciclo de vida
    def connect(self, timeout: float = TIMEOUT) -> None:
        if self._connected:
            return
        env = self.process_env(self.env) if self.env is not None else None
        client = AcpClient(self.argv, self.cwd, env=env, on_update=self._on_update, on_permission=self.on_permission)
        try:
            info = client.connect().result(timeout)
        except (AcpError, OSError, cf.TimeoutError) as exc:
            client.close()
            raise ProviderUnavailable(f"o agente nao iniciou: {getattr(exc, 'code', type(exc).__name__)}") from None
        if info.get("safe_mode") is False:
            client.close()
            raise ProviderUnavailable("o agente nao anuncia um modo que peca permissao")
        self.client = client
        self._agent = info.get("agent") or {}
        sid = str(client.session_id)
        self._config[sid] = list(client.config_options)
        self._models = self._survey_models(sid, timeout)
        self._probe_free = True
        self._verified_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._connected = True

    def _set(self, sid: str, cid: str, value: str, timeout: float = TIMEOUT) -> list[dict]:
        opt = find_config(self._config.get(sid, []), cid)
        if opt is None:
            raise AcpError("RPC", f"o agente nao anuncia {cid!r}")
        if value not in config_values(opt):
            raise AcpError("RPC", f"{cid}={value!r} fora das opcoes anunciadas")
        assert self.client is not None
        res = self.client.call("session/set_config_option",
                               {"sessionId": sid, "configId": str(opt["id"]), "value": value}, timeout).result(timeout + 5)
        if not isinstance(res, dict) or not isinstance(res.get("configOptions"), list):
            raise AcpError("RPC", "set_config_option sem configOptions: valor nao confirmado")
        self._config[sid] = [o for o in res["configOptions"] if isinstance(o, dict)]
        return self._config[sid]

    def _survey_models(self, sid: str, timeout: float) -> dict[str, dict]:
        """Esforcos por modelo, como o agente anuncia com cada modelo selecionado; volta ao modelo inicial."""
        model_opt = find_config(self._config[sid], "model")
        if model_opt is None:
            return {}
        names, start = _names(model_opt), str(model_opt.get("currentValue", ""))
        out: dict[str, dict] = {}
        for value in config_values(model_opt):
            try:
                opts = self._config[sid] if value == start else self._set(sid, "model", value, timeout)
            except AcpError:
                continue                                                     # o agente recusou: nao entra
            eff = find_config(opts, "thought_level")
            out[value] = {
                "schema": "p2s.model.v1", "model_id": value, "display_name": names.get(value, value),
                "provider_id": self.provider_id, "transport": "ACP", "availability": "AVAILABLE",
                "tools": "SUPPORTED", "images": "SUPPORTED" if self._images_supported() else "UNSUPPORTED",
                "effort_levels": config_values(eff) if eff else [], "limits": {"context_tokens": None},
                "source": "configOptions (session/new + session/set_config_option)",
            }
        current = find_config(self._config[sid], "model")
        if start and current is not None and current.get("currentValue") != start:
            self._set(sid, "model", start, timeout)
        return out

    def disconnect(self) -> None:
        with self._lock:
            client, self.client = self.client, None
            self._sessions.clear()
            self._config.clear()
            self._sinks.clear()
            self._connected = False
            self._probe_free = False
        if client is not None:
            try:
                client.close().result(30)
            except Exception:                                                 # noqa: BLE001 - encerrar e o melhor esforco
                pass

    def get_status(self) -> AdapterStatus:
        return AdapterStatus("CONNECTED" if self._connected else "DISCONNECTED", tuple(self._sessions),
                             f"{len(self._sessions)} sessao(oes)" if self._connected else "")

    def _need(self) -> None:
        if not self._connected or self.client is None:
            raise NotConnected(self.provider_id)
        if not self.client.alive:
            raise ProviderUnavailable("o processo do agente terminou")

    # ------------------------------------------------------------------------------------------------ modelos
    def list_models(self) -> list[dict]:
        return [dict(m) for m in self._models.values()]

    def list_effort_levels(self, model_id: str | None = None) -> list[str]:
        if not any(m["effort_levels"] for m in self._models.values()):
            return super().list_effort_levels(model_id)
        key = model_id or next(iter(self._models))
        if key not in self._models:
            raise ModelRejected(str(model_id))
        return list(self._models[key]["effort_levels"])

    # ------------------------------------------------------------------------------------------------ sessoes
    def _open_session(self) -> str:
        """Sessao nova no processo, ja no modo que pede permissao (confirmado pelo agente)."""
        assert self.client is not None
        with self._lock:
            if self._probe_free:
                self._probe_free = False
                return str(self.client.session_id)
        res = self.client.call("session/new", {"cwd": self.cwd, "mcpServers": []}, TIMEOUT).result(TIMEOUT + 5)
        sid = str((res or {}).get("sessionId") or "")
        if not sid:
            raise ProviderUnavailable("session/new sem sessionId")
        modes = res.get("modes") or {}
        self._config[sid] = [o for o in res.get("configOptions") or [] if isinstance(o, dict)]
        if modes.get("availableModes") and not mode_is_safe(str(modes.get("currentModeId") or ""), modes):
            safe = safe_mode_id(modes)
            if safe is None:
                raise ProviderUnavailable("o agente nao anuncia um modo que peca permissao")
            self.client.call("session/set_mode", {"sessionId": sid, "modeId": safe}, TIMEOUT).result(TIMEOUT + 5)
        return sid

    def create_session(self, request: SessionRequest) -> dict:
        self._need()
        if request.model_id is not None and request.model_id not in self._models:
            raise ModelRejected(request.model_id)
        if request.context_tokens is not None:                                # o ACP nao tem como fixar a janela
            raise CapabilityUnsupported("context_tokens", "o agente ACP nao aceita fixar a janela de contexto")
        if request.effort is not None:
            levels = self._models.get(request.model_id or next(iter(self._models), ""), {}).get("effort_levels", [])
            if request.effort not in levels:
                raise EffortRejected(f"{request.model_id or 'modelo atual'} nao anuncia {request.effort!r}")
        try:
            sid = self._open_session()
            if request.model_id is not None:
                self._set(sid, "model", request.model_id)
            if request.effort is not None:
                self._set(sid, "thought_level", request.effort)
        except AcpError as exc:
            raise ModelRejected(f"o agente recusou: {exc.message}") from None
        model = find_config(self._config.get(sid, []), "model")
        eff = find_config(self._config.get(sid, []), "thought_level")
        rec = {
            "schema": "p2s.session.v1", "session_id": sid, "provider_id": self.provider_id,
            "model_requested": request.model_id,
            "model_accepted": str(model.get("currentValue")) if model else None,
            "effort_requested": request.effort,
            "effort_accepted": str(eff.get("currentValue")) if eff and request.effort is not None else None,
            "workspace": request.workspace,
            "links": {k: request.links.get(k) for k in ("spec", "issue", "pr", "run")},
            "state": "READY", "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._sessions[sid] = rec
        return dict(rec)

    def resume_session(self, session_id: str) -> dict:
        self._need()
        if not (self._agent.get("agentCapabilities") or {}).get("loadSession"):
            return super().resume_session(session_id)
        if session_id in self._sessions:
            return dict(self._sessions[session_id])
        return self._load_session(session_id)                                # outro processo: o agente guarda a sessao

    def _load_session(self, session_id: str) -> dict:
        """`session/load` de uma sessao que este processo nao abriu (recuperacao depois de o agente cair, F4). Recusa do
        agente = SessionNotFound. A sessao volta no modo que pede permissao, como uma nova."""
        assert self.client is not None
        try:
            res = self.client.call("session/load", {"sessionId": session_id, "cwd": self.cwd, "mcpServers": []},
                                   TIMEOUT).result(TIMEOUT + 5)
        except AcpError as exc:
            raise SessionNotFound(f"{session_id}: {exc.message}") from None
        res = res if isinstance(res, dict) else {}
        self._config[session_id] = [o for o in res.get("configOptions") or [] if isinstance(o, dict)]
        modes = res.get("modes") or {}
        if modes.get("availableModes") and not mode_is_safe(str(modes.get("currentModeId") or ""), modes):
            safe = safe_mode_id(modes)
            if safe is None:
                raise ProviderUnavailable("o agente nao anuncia um modo que peca permissao")
            self.client.call("session/set_mode", {"sessionId": session_id, "modeId": safe}, TIMEOUT).result(TIMEOUT + 5)
        model = find_config(self._config[session_id], "model")
        rec = {
            "schema": "p2s.session.v1", "session_id": session_id, "provider_id": self.provider_id,
            "model_requested": None, "model_accepted": str(model.get("currentValue")) if model else None,
            "effort_requested": None, "effort_accepted": None, "workspace": None,
            "links": {k: None for k in ("spec", "issue", "pr", "run")},
            "state": "READY", "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._sessions[session_id] = rec
        return dict(rec)

    def _images_supported(self) -> bool:
        return bool(((self._agent.get("agentCapabilities") or {}).get("promptCapabilities") or {}).get("image"))

    def send_prompt(self, session_id: str, text: str, on_event, images=()) -> cf.Future:
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        assert self.client is not None
        blocks: list[dict] = [{"type": "text", "text": text}]
        if images:                                                           # bloco de imagem ACP, se o agente anuncia
            if not self._images_supported():
                raise CapabilityUnsupported("images", "o agente nao anuncia promptCapabilities.image")
            blocks += [{"type": "image", "mimeType": p.mime, "data": base64.b64encode(p.data).decode("ascii")}
                       for p in load_images(images)]
        self._sinks[session_id] = on_event
        rec = self._sessions[session_id]
        out: cf.Future = cf.Future()

        def done(f: cf.Future) -> None:
            exc = f.exception()
            if exc is not None:
                out.set_exception(PromptFailed(getattr(exc, "code", type(exc).__name__)))
                return
            res = f.result()
            reason = str((res or {}).get("stopReason", "")) if isinstance(res, dict) else ""
            model = find_config(self._config.get(session_id, []), "model")
            if isinstance(res, dict) and isinstance(res.get("usage"), dict):  # F6: totais da sessao
                meta = res.get("_meta") if isinstance(res.get("_meta"), dict) else {}
                snap = from_acp_prompt_usage({**res["usage"], "_meta": meta}, provider_id=self.provider_id,
                                             model_id=str(rec.get("model_accepted") or ""))
                on_event(AgentEvent("usage", "", snap.to_dict()))
            eff = find_config(self._config.get(session_id, []), "thought_level")
            out.set_result(PromptResult(reason if reason in STOP_REASONS else "error",
                                        str(model.get("currentValue")) if model else None,
                                        (str(eff.get("currentValue")) if eff else None) if rec["effort_accepted"] else None))
        self.client.call("session/prompt", {"sessionId": session_id, "prompt": blocks}, None).add_done_callback(done)
        return out

    def cancel(self, session_id: str) -> None:
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        assert self.client is not None
        self.client.cancel_session(session_id).result(30)

    def close_session(self, session_id: str) -> None:
        """`session/close` quando o agente anuncia (`sessionCapabilities.close`); sem isso, so esquece a sessao aqui."""
        self._need()
        if session_id not in self._sessions:
            raise SessionNotFound(session_id)
        assert self.client is not None
        caps = (self._agent.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
        if "close" in caps:
            self.client.call("session/close", {"sessionId": session_id}, TIMEOUT).result(TIMEOUT + 5)
        with self._lock:
            self._sessions.pop(session_id, None)
            self._config.pop(session_id, None)
            self._sinks.pop(session_id, None)
