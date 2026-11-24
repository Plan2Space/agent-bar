"""Ollama com ferramentas publicadas pelo Studio (P2S-LOCAL-EXECUTOR-SKETCHUP-POC §4, §7): so `tool_calls` estruturado
do turno COMPLETO chega ao executor; recusa tipada para inventada/malformada/duplicada; resultado volta como papel
`tool`; uma rodada de ferramenta por turno; capacidade honesta. Contra o Ollama falso por HTTP de verdade."""
from __future__ import annotations

import json
import threading
import time

import pytest

from agent_bar.platform.adapter import CapabilityUnsupported, PromptFailed, SessionRequest
from agent_bar.platform.local_tools import ToolCall, ToolContext, call_digest, normalize_tool_calls
from agent_bar.platform.ollama import OllamaAdapter
from tests.platform.fake_ollama import FakeOllama, fake_digest

SPEC = {"type": "function", "function": {"name": "build_room_sample", "description": "amostra",
                                         "parameters": {"type": "object", "properties": {"fixture_ref": {"type": "string"}},
                                                        "required": ["fixture_ref"]}}}


def call(name="build_room_sample", args=None, cid="call_1"):
    return {"id": cid, "function": {"index": 0, "name": name, "arguments": {"fixture_ref": "room_001"} if args is None else args}}


class Host:
    """ToolHost de teste: registra cada chamada e devolve um resultado fixo."""

    def __init__(self, status="completed"):
        self.calls: list[tuple[ToolCall, ToolContext]] = []
        self.status = status

    def specs(self):
        return [SPEC]

    def names(self):
        return {"build_room_sample"}

    def run(self, c, ctx):
        self.calls.append((c, ctx))
        ctx.emit("tool_call", {"toolCallId": c.call_id, "title": "amostra", "kind": "execute", "status": "pending"})
        return {"status": self.status, "run_id": "lx-1", "request_id": "rq-1"}


@pytest.fixture
def server():
    with FakeOllama() as s:
        yield s


def make(server, host):
    a = OllamaAdapter(server.endpoint, tool_host=host)
    a.connect()
    return a


def ask(a, text="crie uma amostra"):
    events = []
    sid = a.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    res = a.send_prompt(sid, text, events.append).result(timeout=15)
    return sid, res, events


def chats(server):
    return [b for p, b in server.requests if p == "/api/chat"]


def test_valid_tool_call_runs_once_and_the_result_goes_back_as_tool_role(server):
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [call()]}
    _, res, events = ask(a)
    assert res.stop_reason == "end_turn"
    assert len(host.calls) == 1
    c, ctx = host.calls[0]
    assert (c.name, dict(c.arguments), c.call_id) == ("build_room_sample", {"fixture_ref": "room_001"}, "call_1")
    assert c.digest == call_digest("build_room_sample", {"fixture_ref": "room_001"})
    assert ctx.model_id == "chat-a" and ctx.model_digest == fake_digest("chat-a") and ctx.provider_version
    first, follow = chats(server)
    assert first["tools"] == [SPEC]
    assert "tools" not in follow                                              # uma rodada de ferramenta por turno
    tool_msg = follow["messages"][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["run_id"] == "lx-1"
    assert follow["messages"][-2]["tool_calls"][0]["function"]["name"] == "build_room_sample"
    assert any(e.kind == "tool_call" for e in events)
    assert "".join(e.text for e in events if e.kind == "text").startswith("resultado:")
    a.disconnect()


def test_text_that_only_looks_like_a_tool_call_is_never_executed(server):
    host = Host()
    a = make(server, host)
    server.tool_script = {"content": json.dumps({"name": "build_room_sample", "arguments": {"fixture_ref": "room_001"}})}
    _, res, _ = ask(a)
    assert res.stop_reason == "end_turn" and host.calls == [] and len(chats(server)) == 1
    a.disconnect()


@pytest.mark.parametrize("raw, code", [
    (call(name="rm_rf"), "UNKNOWN_TOOL"),
    (call(args='{"fixture_ref": "room_0'), "MALFORMED"),
    ({"id": "x", "function": {"arguments": {}}}, "MALFORMED"),
    (call(args=["room_001"]), "MALFORMED"),
])
def test_invented_or_malformed_calls_are_refused_without_reaching_the_executor(server, raw, code):
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [raw]}
    _, res, events = ask(a)
    assert host.calls == []
    refusal = json.loads(chats(server)[1]["messages"][-1]["content"])
    assert refusal["status"] == "refused" and refusal["error"]["code"] == code
    assert any(e.kind == "tool_update" and e.data.get("status") == "failed" for e in events)
    a.disconnect()


def test_duplicate_call_in_the_same_turn_runs_once(server):
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [call(cid="a"), call(cid="b")]}
    ask(a)
    assert len(host.calls) == 1
    codes = [json.loads(m["content"]).get("error") for m in chats(server)[1]["messages"] if m["role"] == "tool"]
    assert codes[0]["code"] == "DUPLICATE"
    a.disconnect()


def test_a_stream_cut_before_done_executes_nothing(server):
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [call()], "drop": True}
    sid = a.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    with pytest.raises(PromptFailed):
        a.send_prompt(sid, "crie", lambda e: None).result(timeout=15)
    assert host.calls == []
    a.disconnect()


def test_a_second_tool_round_in_the_same_turn_is_refused(server):
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [call()], "again": True}
    _, res, events = ask(a)
    assert len(host.calls) == 1 and res.stop_reason == "end_turn"
    assert any(e.kind == "tool_update" and "TURN_LIMIT" in json.dumps(dict(e.data)) for e in events)
    a.disconnect()


def test_without_executor_no_tools_are_sent_and_capability_stays_unsupported(server):
    a = OllamaAdapter(server.endpoint)
    a.connect()
    server.tool_script = {"calls": [call()], "untold": True}
    ask(a)
    assert "tools" not in chats(server)[0]
    assert a.describe()["capabilities"]["tool_calls"]["state"] == "UNSUPPORTED"
    a.disconnect()


def test_capability_is_supported_only_after_an_executed_call(server):
    a = make(server, Host(status="failed"))
    assert a.describe()["capabilities"]["tool_calls"]["state"] == "UNKNOWN"
    server.tool_script = {"calls": [call()]}
    ask(a)
    assert a.describe()["capabilities"]["tool_calls"]["state"] == "UNKNOWN"     # falhou: nao e prova
    a.disconnect()
    b = make(server, Host())
    ask(b)
    cap = b.describe()["capabilities"]["tool_calls"]
    assert cap["state"] == "SUPPORTED" and "chat-a" in cap["evidence"]["method"]
    b.disconnect()


def test_executor_profile_offers_only_models_with_tools(server):
    a = make(server, Host())
    assert [m["model_id"] for m in a.list_models()] == ["chat-a"]
    with pytest.raises(CapabilityUnsupported):
        a.create_session(SessionRequest(model_id="think-b"))
    a.disconnect()


def test_normalize_accepts_json_text_arguments_only_when_complete():
    ok, refused = normalize_tool_calls([call(args='{"fixture_ref": "room_001"}')], {"build_room_sample"}, 1)
    assert [dict(c.arguments) for c in ok] == [{"fixture_ref": "room_001"}] and refused == []
    ok, refused = normalize_tool_calls("nao e lista", {"build_room_sample"}, 1)
    assert ok == [] and refused == []


class HostWithView(Host):
    def system_prompt(self):
        return "voce e o executor local; resuma o resultado da ferramenta sem inventar"

    def model_view(self, result):
        return {"status": result["status"], "run_id": result["run_id"]}


def test_executor_sends_its_system_prompt_and_a_compact_result_to_the_model(server):
    host = HostWithView()
    a = make(server, host)
    server.tool_script = {"calls": [call()]}
    sid, _, _ = ask(a)
    first, follow = chats(server)
    assert first["messages"][0] == {"role": "system", "content": host.system_prompt()}
    assert json.loads(follow["messages"][-1]["content"]) == {"status": "completed", "run_id": "lx-1"}
    assert all(m["role"] != "system" for m in a._history[sid])                 # historico do Chat: so a conversa
    a.disconnect()


def test_each_turn_gets_a_fresh_studio_turn_id_even_after_a_restart(server):
    seen = []
    for _ in range(2):                                                       # dois "Studios": ids de sessao repetem
        host = Host()
        a = make(server, host)
        server.tool_script = {"calls": [call()]}
        ask(a)
        c, ctx = host.calls[0]
        seen.append((ctx.session_id, ctx.turn, ctx.turn_id))
        a.disconnect()
    assert seen[0][:2] == seen[1][:2] and seen[0][2] != seen[1][2] and len(seen[0][2]) >= 16


class HostWithFinal(Host):
    def final_text(self, results):
        return "Studio: " + "; ".join(f"{r['status']} {r['run_id']}" for r in results)


def test_executor_that_writes_the_final_text_skips_the_model_summary(server):
    host = HostWithFinal()
    a = make(server, host)
    server.tool_script = {"calls": [call()]}
    sid, res, events = ask(a)
    assert res.stop_reason == "end_turn" and len(chats(server)) == 1             # nenhum resumo pedido ao modelo
    assert "".join(e.text for e in events if e.kind == "text") == "Studio: completed lx-1"
    roles = [m["role"] for m in a._history[sid]]
    assert roles == ["user", "assistant", "tool", "assistant"]                 # o resultado fica para o proximo turno
    assert a._history[sid][-1]["content"] == "Studio: completed lx-1"
    a.disconnect()


class SlowHost(Host):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def run(self, c, ctx):
        self.entered.set()
        self.release.wait(10)
        return super().run(c, ctx)


def test_disconnect_does_not_freeze_while_the_engine_runs(server):
    host = SlowHost()
    a = make(server, host)
    server.tool_script = {"calls": [call()]}
    sid = a.create_session(SessionRequest(model_id="chat-a"))["session_id"]
    fut = a.send_prompt(sid, "crie", lambda e: None)
    assert host.entered.wait(10)
    t0 = time.monotonic()
    a.disconnect()                                                           # trocar de agente / fechar a aba
    assert time.monotonic() - t0 < 1.0, "a UI ficaria congelada durante o build"
    host.release.set()
    fut.result(timeout=10)                                                   # o turno termina sozinho depois


def test_capability_proof_names_the_model_that_executed(server):
    a = make(server, Host())
    server.tool_script = {"calls": [call()]}
    ask(a)
    method = a.describe()["capabilities"]["tool_calls"]["evidence"]["method"]
    assert f"chat-a@{fake_digest('chat-a')[:12]}" in method and "so para" in method
    a.disconnect()


def test_executor_without_any_tool_model_says_so():
    with FakeOllama({"think-b": {"capabilities": ["completion", "thinking"]}}) as s:
        a = OllamaAdapter(s.endpoint, tool_host=Host())
        a.connect()
        with pytest.raises(Exception) as exc:
            a.create_session(SessionRequest())
        assert "tools" in str(exc.value)
        a.disconnect()


def test_tool_capability_is_per_model(server):
    models = {"chat-a": {"capabilities": ["completion", "tools"]}, "chat-b": {"capabilities": ["completion", "tools"]}}
    with FakeOllama(models) as s:
        a = OllamaAdapter(s.endpoint, tool_host=Host())
        a.connect()
        s.tool_script = {"calls": [call()]}
        sid = a.create_session(SessionRequest(model_id="chat-a"))["session_id"]
        a.send_prompt(sid, "crie", lambda e: None).result(timeout=15)
        assert a.tool_calls_for("chat-a")["state"] == "SUPPORTED"
        assert a.tool_calls_for("chat-b")["state"] == "UNKNOWN"              # prova de um nao vale para o outro
        a.disconnect()


def test_tool_context_carries_prompt_hash_and_inference_parameters(server):
    import hashlib
    host = Host()
    a = make(server, host)
    server.tool_script = {"calls": [call()]}
    ask(a, "crie uma amostra")
    ctx = host.calls[0][1]
    assert ctx.prompt_sha256 == hashlib.sha256("crie uma amostra".encode()).hexdigest()
    assert ctx.provider == "ollama" and ctx.adapter == "ollama"
    assert ctx.inference == {"think": None, "num_ctx": None, "temperature": None, "seed": None}
    a.disconnect()
