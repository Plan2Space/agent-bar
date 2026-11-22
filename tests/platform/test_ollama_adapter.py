"""Adaptador Ollama (LOCAL_HTTP): passa na suite de contrato comum contra um Ollama falso de verdade (HTTP), e o
que anuncia vem do servidor (modelos, ferramentas, contexto, esforco so com thinking)."""
from __future__ import annotations

import threading
import time

import pytest

from agent_bar.platform.adapter import (
    AgentEvent,
    CapabilityUnsupported,
    EffortRejected,
    ProviderUnavailable,
    SessionRequest,
)
from agent_bar.platform.contracts import validate_provider
from agent_bar.platform.ollama import OllamaAdapter, effort_levels_from_show, think_param
from tests.platform.adapter_contract import ContractContext, run_contract
from tests.platform.fake_ollama import FakeOllama

CTX = ContractContext(prompt="diga ok", slow_prompt="conte devagar", prompt_timeout=15, cancel_timeout=5)


@pytest.fixture
def server():
    with FakeOllama() as s:
        yield s


@pytest.fixture
def adapter(server):
    a = OllamaAdapter(server.endpoint)
    a.connect()
    yield a
    a.disconnect()


def test_passes_the_common_contract_suite(server):
    assert run_contract(lambda env: OllamaAdapter(server.endpoint, env=env), CTX) == {}


def test_models_come_from_the_server_and_embedding_models_are_left_out(adapter):
    models = {m["model_id"]: m for m in adapter.list_models()}
    assert set(models) == {"chat-a", "think-b"}
    assert models["chat-a"]["tools"] == "SUPPORTED" and models["think-b"]["tools"] == "UNSUPPORTED"
    assert models["chat-a"]["limits"]["context_tokens"] == 8192
    assert models["chat-a"]["effort_levels"] == [] and models["think-b"]["effort_levels"] == ["off", "on"]


def test_effort_is_only_offered_where_the_model_has_thinking(adapter):
    assert adapter.list_effort_levels("think-b") == ["off", "on"]
    assert adapter.list_effort_levels("chat-a") == []
    with pytest.raises(EffortRejected):
        adapter.create_session(SessionRequest(model_id="chat-a", effort="on"))


def test_without_any_thinking_model_effort_is_unsupported_not_faked():
    with FakeOllama({"chat-a": {"capabilities": ["completion"]}}) as s:
        a = OllamaAdapter(s.endpoint)
        a.connect()
        try:
            assert a.describe()["capabilities"]["effort_select"]["state"] == "UNSUPPORTED"
            with pytest.raises(CapabilityUnsupported):
                a.list_effort_levels("chat-a")
        finally:
            a.disconnect()


def test_the_think_parameter_reaches_the_server_and_thoughts_stream_separately(adapter, server):
    sid = adapter.create_session(SessionRequest(model_id="think-b", effort="on"))["session_id"]
    events: list[AgentEvent] = []
    res = adapter.send_prompt(sid, "diga ok", events.append).result(10)
    assert res.stop_reason == "end_turn" and res.model_confirmed == "think-b" and res.effort_confirmed == "on"
    assert [e.kind for e in events][:1] == ["thought"] and "".join(e.text for e in events if e.kind == "text") == "ok"
    chat = [b for p, b in server.requests if p == "/api/chat"][-1]
    assert chat["think"] is True and chat["stream"] is True
    plain = adapter.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    adapter.send_prompt(plain, "diga ok", lambda e: None).result(10)
    assert "think" not in [b for p, b in server.requests if p == "/api/chat"][-1]   # sem esforco: nada de think


def test_history_carries_completed_turns_only(adapter):
    sid = adapter.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    adapter.send_prompt(sid, "diga ok", lambda e: None).result(10)
    fut = adapter.send_prompt(sid, "conte devagar", lambda e: None)
    time.sleep(0.2)
    adapter.cancel(sid)
    assert fut.result(5).stop_reason == "cancelled"
    out: list[AgentEvent] = []
    adapter.send_prompt(sid, "historico", out.append).result(10)
    assert "".join(e.text for e in out) == "users=2"                         # o turno cancelado nao entrou


def test_cancel_closes_the_stream_on_the_server_side(adapter, server):
    sid = adapter.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    first = threading.Event()
    fut = adapter.send_prompt(sid, "conte devagar", lambda e: first.set())
    assert first.wait(5)
    t0 = time.monotonic()
    adapter.cancel(sid)
    assert fut.result(5).stop_reason == "cancelled" and time.monotonic() - t0 < 2
    end = time.monotonic() + 5
    while time.monotonic() < end and server.aborted == 0:
        time.sleep(0.05)
    assert server.aborted == 1                                               # o servidor viu o cliente desconectar


def test_server_down_is_a_typed_error_with_no_fake_capabilities():
    a = OllamaAdapter("http://127.0.0.1:9", timeout=1)
    with pytest.raises(ProviderUnavailable):
        a.connect()
    rec = validate_provider(a.describe())
    assert rec["connection_status"] == "DISCONNECTED"
    assert all(c["state"] == "UNKNOWN" for c in rec["capabilities"].values())


@pytest.mark.parametrize("show,expected", [
    ({"capabilities": ["completion"]}, []),
    ({"capabilities": ["completion", "thinking"], "thinking": {"values": [False, True]}}, ["off", "on"]),
    ({"capabilities": ["completion", "thinking"], "thinking": {"values": ["low", "medium", "high"]}}, ["low", "medium", "high"]),
    ({"capabilities": ["completion", "thinking"]}, ["off", "on"]),
])
def test_effort_levels_are_read_from_the_show_response(show, expected):
    assert effort_levels_from_show(show) == expected


def test_think_parameter_mapping():
    assert (think_param(None), think_param("off"), think_param("on"), think_param("high")) == (None, False, True, "high")


# ------------------------------------------------------------------------------------------------ opt-in: real
@pytest.mark.real_agent
def test_real_local_ollama_passes_the_contract_and_streams():
    """Opt-in (`-m real_agent`): o Ollama desta maquina (localhost:11434). Custo zero, mas depende de modelo baixado."""
    ctx = ContractContext(prompt="Responda apenas: ok", slow_prompt="Conte devagar de 1 a 300, um numero por linha.",
                          prompt_timeout=180, cancel_timeout=10)
    failures = run_contract(lambda env: OllamaAdapter(env=env, timeout=30), ctx)
    a = OllamaAdapter(timeout=30)
    a.connect()
    try:
        print("modelos:", [(m["model_id"], m["effort_levels"], m["tools"]) for m in a.list_models()])
        if "qwen3:14b" in {m["model_id"] for m in a.list_models()}:
            sid = a.create_session(SessionRequest(model_id="qwen3:14b", effort="on"))["session_id"]
            ev: list[AgentEvent] = []
            res = a.send_prompt(sid, "Quanto e 2+2? Responda so o numero.", ev.append).result(300)
            print("qwen3 think=on:", res, "thought chunks:", sum(e.kind == "thought" for e in ev),
                  "text:", "".join(e.text for e in ev if e.kind == "text")[:40])
            assert res.model_confirmed == "qwen3:14b" and any(e.kind == "thought" for e in ev)
    finally:
        a.disconnect()
    assert failures == {}, failures



def test_close_session_stops_a_running_turn_and_forgets_the_session(adapter, server):
    import threading as _t

    from agent_bar.platform.adapter import SessionNotFound
    sid = adapter.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    first = _t.Event()
    fut = adapter.send_prompt(sid, "conte devagar", lambda e: first.set())
    assert first.wait(5)
    adapter.close_session(sid)
    assert fut.result(5).stop_reason == "cancelled" and sid not in adapter.get_status().sessions
    with pytest.raises(SessionNotFound):
        adapter.send_prompt(sid, "diga ok", lambda e: None)
