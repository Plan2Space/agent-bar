"""Contrato do cliente ACP contra um agente falso por stdio (tests/cockpit/fake_acp_agent.py). Sem rede."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import acp
from agent_bar.cockpit.acp import AcpClient, AcpError, PermissionRequest, Update

STUB = Path(__file__).with_name("fake_acp_agent.py")
T = 20


class Rec:
    def __init__(self) -> None:
        self.updates: list[Update] = []
        self.lock = threading.Lock()

    def __call__(self, u: Update) -> None:
        with self.lock:
            self.updates.append(u)

    def texts(self, kind: str = "agent_message_chunk") -> list[str]:
        with self.lock:
            return [u.text for u in self.updates if u.kind == kind]


def make(tmp_path, on_permission=None, env=None, on_exit=None) -> tuple[AcpClient, Rec]:
    rec = Rec()
    c = AcpClient([sys.executable, str(STUB)], tmp_path, on_update=rec, on_permission=on_permission,
                  env=env, on_exit=on_exit, stderr_path=tmp_path / "stderr.log")
    c.start().result(T)
    c.new_session().result(T)
    return c, rec


@pytest.fixture
def client(tmp_path):
    c, rec = make(tmp_path)
    yield c, rec
    c.close().result(T)


def test_initialize_declares_fs_and_terminal_off(client):
    c, rec = client
    assert c.agent_info["agentCapabilities"]["loadSession"] is True
    assert c.prompt("caps").result(T) == "end_turn"
    assert json.loads(rec.texts()[-1]) == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}


def test_new_session_exposes_modes_and_set_mode_round_trips(client):
    c, rec = client
    assert c.session_id == "s-1"
    assert [m["id"] for m in c.modes["availableModes"]][:1] == ["default"]
    c.set_mode("plan").result(T)
    assert any(u.kind == "current_mode_update" and u.data["currentModeId"] == "plan" for u in rec.updates)


def test_streaming_chunks_arrive_in_order(client):
    c, rec = client
    assert c.prompt("echo 50").result(T) == "end_turn"
    assert rec.texts() == [f"c{i} " for i in range(50)]


def test_cancel_ends_prompt_with_cancelled(client):
    c, rec = client
    fut = c.prompt("slow")
    deadline = time.monotonic() + T
    while not rec.texts() and time.monotonic() < deadline:
        time.sleep(0.02)
    c.cancel().result(T)
    assert fut.result(T) == "cancelled"
    assert 0 < len(rec.texts()) < 400


def test_permission_selected_by_callback(tmp_path):
    seen: list[PermissionRequest] = []

    def on_perm(req, respond):
        seen.append(req)
        threading.Timer(0.05, respond, args=("allow",)).start()           # responde de outra thread, como a UI

    c, rec = make(tmp_path, on_permission=on_perm)
    try:
        assert c.prompt("perm").result(T) == "end_turn"
        assert "outcome=selected:allow" in rec.texts()
        assert seen[0].option_ids() == ["allow", "always", "reject"]
        assert seen[0].strongest_once() == "allow"
        assert seen[0].tool_call["toolCallId"] == "t1"
    finally:
        c.close().result(T)


def test_permission_dismissed_answers_cancelled(tmp_path):
    c, rec = make(tmp_path, on_permission=lambda req, respond: respond(None))
    try:
        c.prompt("perm").result(T)
        assert "outcome=cancelled" in rec.texts()
    finally:
        c.close().result(T)


def test_permission_without_callback_is_cancelled(client):
    c, rec = client
    c.prompt("perm").result(T)
    assert "outcome=cancelled" in rec.texts()


def test_cancel_answers_open_permission_with_cancelled(tmp_path):
    asked = threading.Event()
    c, rec = make(tmp_path, on_permission=lambda req, respond: asked.set())   # a UI nunca responde
    try:
        fut = c.prompt("perm")
        assert asked.wait(T)
        c.cancel().result(T)
        fut.result(T)
        assert "outcome=cancelled" in rec.texts()
    finally:
        c.close().result(T)


def test_invalid_line_is_ignored_and_connection_survives(client):
    c, rec = client
    assert c.prompt("garbage").result(T) == "end_turn"
    assert "after-garbage" in rec.texts()
    assert c.prompt("echo 1").result(T) == "end_turn"


def test_agent_request_for_fs_gets_method_not_found(client):
    c, rec = client
    c.prompt("askfs").result(T)
    assert rec.texts()[-1] == "-32601"


def test_tool_updates_plan_thought_and_unknown_are_typed(client):
    c, rec = client
    c.prompt("tools").result(T)
    c.prompt("unknown").result(T)
    kinds = [u.kind for u in rec.updates]
    assert kinds.count("tool_call") == 1 and kinds.count("tool_call_update") == 3
    assert "plan" in kinds and rec.texts("agent_thought_chunk") == ["hmm"]
    unk = [u for u in rec.updates if u.kind == "unknown"]
    assert unk and unk[0].raw_kind == "something_new"


def test_process_death_fails_pending_future_with_acp_down(tmp_path):
    exits: list = []
    c, rec = make(tmp_path, on_exit=exits.append)
    try:
        with pytest.raises(AcpError) as ei:
            c.prompt("die").result(T)
        assert ei.value.code == "ACP_DOWN"
        assert acp.classify(ei.value) == "acp_down"
        assert exits == [3]
        with pytest.raises(AcpError):
            c.prompt("echo 1").result(T)
    finally:
        c.close().result(T)


def test_agent_environment_has_no_credentials(tmp_path):
    base = {"PATH": "C:/x", "SYSTEMROOT": "C:/Windows", "ANTHROPIC_API_KEY": "s", "GH_TOKEN": "s",
            "GITHUB_TOKEN": "s", "CLAUDE_CODE_OAUTH_TOKEN": "s", "MY_SECRET": "s", "AWS_PROFILE": "s",
            "AGENT_BAR_COCKPIT_X": "1", "RANDOM_VAR": "1", "AGENT_BAR_API_KEY": "s"}
    env = acp.isolated_env(base)
    assert sorted(env) == ["AGENT_BAR_COCKPIT_X", "PATH", "SYSTEMROOT"]
    # ponta a ponta: o stub ecoa as chaves que recebeu
    import os
    real = acp.isolated_env({**os.environ, "ANTHROPIC_API_KEY": "s", "GH_TOKEN": "s", "SOME_TOKEN": "s"})
    c, rec = make(tmp_path, env=real)
    try:
        c.prompt("env").result(T)
        keys = json.loads(rec.texts()[-1])
        bad = [k for k in keys if k.upper().startswith(acp.DENY_PREFIXES) or "TOKEN" in k.upper() or "KEY" in k.upper()]
        assert bad == []
    finally:
        c.close().result(T)


def test_missing_executable_is_not_found(tmp_path):
    c = AcpClient(["definitely-not-an-acp-agent-xyz"], tmp_path)
    with pytest.raises(AcpError) as ei:
        c.start().result(T)
    assert acp.classify(ei.value) == "not_found"
    c.close().result(T)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             creationflags=acp.CREATE_NO_WINDOW).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.mark.skipif(sys.platform != "win32", reason="encerramento da arvore e especifico do Windows")
def test_close_kills_the_whole_tree(tmp_path):
    c, rec = make(tmp_path)
    c.prompt("spawn").result(T)
    grandchild = int(rec.texts()[-1])
    assert _alive(grandchild)
    c.close(grace_s=1).result(T)
    deadline = time.monotonic() + 10
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(grandchild)
    assert not _alive(c.pid)


def test_ui_never_waits_calls_return_futures_immediately(client):
    c, rec = client
    t0 = time.perf_counter()
    fut = c.prompt("slow")
    assert time.perf_counter() - t0 < 0.05
    c.cancel().result(T)
    fut.result(T)
