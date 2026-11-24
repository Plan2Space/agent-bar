"""provider_client.py — liga um adaptador da plataforma (F1, `ProviderAdapter`) ao Chat do Studio.

Expoe a mesma superficie do `AcpClient` que o `ChatView` usa (connect, prompt, cancel, close, set_mode,
set_config_option, config_options, modes), para o Chat conversar com provedor nao-ACP (Ollama, F2a) sem um segundo Chat.

- Eventos do adaptador viram `Update` ("text" -> agent_message_chunk, "thought" -> agent_thought_chunk, "status" fica
  de fora).
- Modelo e esforco viram `configOptions` no formato ACP, montadas so com o que o adaptador comprovou
  (`list_models` / `effort_levels`); o valor atual e o que a SESSAO devolveu como aceito.
- Trocar modelo ou esforco cria uma sessao nova no adaptador com o pedido; vale o que ela aceitar. Os turnos
  anteriores ficam na sessao antiga (o modelo novo nao os recebe) e o Chat avisa isso (`config_opens_new_session`);
  continuidade entre modelos e trabalho da F3.
- Sem modos de permissao (`set_mode` recusa). Ferramenta so existe no perfil "Executor local (amostra)": o adaptador
  recebe o `on_permission` do Chat (o mesmo canal do ACP: PermissionCard, Permission Inbox, broker) e os eventos
  "tool_call"/"tool_update" viram `tool_call`/`tool_call_update` (o ToolCard do Chat mostra o resultado do MOTOR).
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
from collections.abc import Callable
from typing import Any

from agent_bar.cockpit.acp import AcpError, Update
from agent_bar.platform.adapter import AgentEvent, ProviderAdapter, SessionRequest

log = logging.getLogger("agent_bar.cockpit.provider_client")

DEFAULT_EFFORT = "default"                                                   # "sem pedir esforco": padrao do modelo


class ProviderClient:
    config_opens_new_session = True

    def __init__(self, adapter: ProviderAdapter, *, on_update: Callable[[Update], None] | None = None,
                 on_exit: Callable[[int | None], None] | None = None, on_permission: Callable | None = None,
                 **_ignored: Any) -> None:
        self.adapter = adapter
        if on_permission is not None and hasattr(adapter, "on_permission"):  # so adaptador com executor usa
            adapter.on_permission = on_permission
        self.on_update = on_update
        self.on_exit = on_exit
        self.session_id: str | None = None
        self.session: dict = {}
        self.modes: dict = {}
        self.config_options: list[dict] = []
        self.pid = None
        self._pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"provider-{adapter.adapter_id}")
        self._prompt: cf.Future | None = None
        self._closed = False

    @property
    def alive(self) -> bool:
        return not self._closed and self.session_id is not None

    # ------------------------------------------------------------------------------------------------ conexao
    def connect(self, resume_session: str | None = None) -> cf.Future:
        return self._pool.submit(self._connect)

    def _connect(self) -> dict:
        self.adapter.connect()
        self._open(SessionRequest())
        return {"agent": {"agentInfo": {"name": self.adapter.adapter_id}}, "session_id": self.session_id,
                "modes": {}, "loaded": False, "mode_forced_from": None, "safe_mode": True,
                "config_options": self.config_options}

    def _open(self, request: SessionRequest) -> None:
        rec = self.adapter.create_session(request)
        self.session, self.session_id = rec, rec["session_id"]
        self.config_options = self._build_config()

    def _build_config(self) -> list[dict]:
        models = self.adapter.list_models()
        if not models:
            return []
        current = self.session.get("model_accepted")
        opts = [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": current,
                 "options": [{"value": m["model_id"], "name": m["display_name"]} for m in models]}]
        levels = next((m["effort_levels"] for m in models if m["model_id"] == current), [])
        if levels:
            opts.append({"id": "effort", "name": "Effort", "category": "thought_level", "type": "select",
                         "currentValue": self.session.get("effort_accepted") or DEFAULT_EFFORT,
                         "options": [{"value": DEFAULT_EFFORT, "name": "Padrão do modelo"},
                                     *({"value": lv, "name": lv} for lv in levels)]})
        return opts

    # ------------------------------------------------------------------------------------------------ config
    def set_config_option(self, config_id: str, value: str) -> cf.Future:
        return self._pool.submit(self._set_config, config_id, value)

    def _set_config(self, config_id: str, value: str) -> list[dict]:
        model = self.session.get("model_accepted")
        effort = self.session.get("effort_accepted")
        if config_id == "model":
            model, effort = value, None                                     # esforco do modelo antigo nao vale no novo
        elif config_id in ("effort", "thought_level"):
            effort = None if value == DEFAULT_EFFORT else value
        else:
            raise AcpError("RPC", f"o provedor nao anuncia a configuracao {config_id!r}")
        old = self.session_id
        self._open(SessionRequest(model_id=model, effort=effort, workspace=self.session.get("workspace")))
        log.info("sessao %s trocada por %s (%s=%s)", old, self.session_id, config_id, value)
        return self.config_options

    def set_mode(self, mode_id: str) -> cf.Future:
        f: cf.Future = cf.Future()
        f.set_exception(AcpError("RPC", "este provedor nao tem modos de permissao (ferramenta so no perfil Executor local, sempre com permissao por pedido)"))
        return f

    # ------------------------------------------------------------------------------------------------ conversa
    def prompt(self, content: str | list) -> cf.Future:
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        sid = self.session_id or ""
        out: cf.Future = cf.Future()

        def emit(ev: AgentEvent) -> None:
            if ev.kind == "usage" and self.on_update is not None:            # F6: snapshot com estado por campo
                self.on_update(Update("usage_snapshot", sid, {"snapshot": dict(ev.data)}))
                return
            if ev.kind in ("tool_call", "tool_update") and self.on_update is not None:
                kind = "tool_call" if ev.kind == "tool_call" else "tool_call_update"
                self.on_update(Update(kind, sid, dict(ev.data)))
                return
            kind = {"text": "agent_message_chunk", "thought": "agent_thought_chunk"}.get(ev.kind)
            if kind and self.on_update is not None:
                self.on_update(Update(kind, sid, {"content": {"type": "text", "text": ev.text}}))
        try:
            inner = self.adapter.send_prompt(sid, text, emit)
        except Exception as exc:                                             # noqa: BLE001 - vira erro do turno
            out.set_exception(exc)
            return out
        self._prompt = inner

        def done(f: cf.Future) -> None:
            exc = f.exception()
            if exc is not None:
                out.set_exception(exc)
            else:
                res = f.result()
                if res.model_confirmed and res.model_confirmed != self.session.get("model_accepted"):
                    log.warning("o provedor respondeu com %s, sessao em %s", res.model_confirmed, self.session.get("model_accepted"))
                out.set_result(res.stop_reason)
        inner.add_done_callback(done)
        return out

    def cancel(self) -> cf.Future:
        def run() -> None:
            if self.session_id:
                self.adapter.cancel(self.session_id)
        return self._pool.submit(run)

    def close(self, grace_s: float = 0) -> cf.Future:
        f: cf.Future = cf.Future()
        if self._closed:
            f.set_result(None)
            return f
        self._closed = True
        try:
            self.adapter.disconnect()
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            f.set_result(None)
        return f
