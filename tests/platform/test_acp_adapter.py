"""Agente ACP como ProviderAdapter: passa na suite de contrato comum contra o agente ACP falso por stdio, e o que
anuncia vem das configOptions do agente (modelos, esforcos por modelo)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_bar.platform.acp_adapter import AcpAdapter
from agent_bar.platform.adapter import AgentEvent, EffortRejected, SessionRequest
from tests.platform.adapter_contract import ContractContext, run_contract

STUB = Path(__file__).resolve().parents[1] / "cockpit" / "fake_acp_agent.py"
CTX = ContractContext(prompt="echo 3", slow_prompt="slow", prompt_timeout=20, cancel_timeout=8, connect_timeout=30)


def make(tmp_path, env=None):
    state = tmp_path / "fake-state"                                          # sessoes "em disco" do agente falso (load estrito)
    state.mkdir(exist_ok=True)
    base = {"PATH": sys.prefix, "SYSTEMROOT": "C:\\Windows", "AGENT_BAR_FAKE_STATE_DIR": str(state), **(env or {})}
    return AcpAdapter([sys.executable, str(STUB)], tmp_path, env=base, provider_id="fake-acp", display_name="ACP falso",
                      cost_class="local")


def test_passes_the_common_contract_suite(tmp_path):
    assert run_contract(lambda env: make(tmp_path, env), CTX) == {}


def test_models_and_per_model_efforts_come_from_the_agent(tmp_path):
    a = make(tmp_path)
    a.connect()
    try:
        models = {m["model_id"]: m["effort_levels"] for m in a.list_models()}
        assert models == {"default": ["default", "low", "high"], "fake-pro": ["default", "low", "high", "max"],
                          "fake-mini": []}
        with pytest.raises(EffortRejected):
            a.create_session(SessionRequest(model_id="fake-mini", effort="low"))
    finally:
        a.disconnect()


def test_a_session_really_runs_with_the_model_and_effort_it_reports(tmp_path):
    a = make(tmp_path)
    a.connect()
    try:
        rec = a.create_session(SessionRequest(model_id="fake-pro", effort="max"))
        assert (rec["model_accepted"], rec["effort_accepted"]) == ("fake-pro", "max")
        ev: list[AgentEvent] = []
        res = a.send_prompt(rec["session_id"], "whoami", ev.append).result(20)
        assert "".join(e.text for e in ev if e.kind == "text") == "model=fake-pro effort=max"
        assert (res.model_confirmed, res.effort_confirmed) == ("fake-pro", "max")
    finally:
        a.disconnect()


def test_many_sessions_share_one_agent_process_that_dies_on_disconnect(tmp_path):
    """A primeira versao abria um processo por sessao e estourou na suite real (80+ sessoes no esforco honesto)."""
    from tests.support.process import pid_alive
    a = make(tmp_path)
    a.connect()
    sids = [a.create_session(SessionRequest())["session_id"] for _ in range(20)]
    pids = a.owned_pids()
    assert len(set(sids)) == 20 and len(pids) == 1
    a.disconnect()
    import time
    end = time.monotonic() + 10
    while time.monotonic() < end and any(pid_alive(p) for p in pids):
        time.sleep(0.05)
    assert not any(pid_alive(p) for p in pids)


def test_an_agent_that_opens_in_auto_ends_up_in_the_safe_mode(tmp_path):
    a = make(tmp_path, {"AGENT_BAR_FAKE_START_MODE": "auto"})
    a.connect()
    try:
        assert a.client.modes["currentModeId"] == "default"                  # a de sondagem: o AcpClient ja trocou
        calls = []
        real_call = a.client.call
        a.client.call = lambda method, params, timeout=30.0: (calls.append((method, dict(params))), real_call(method, params, timeout))[1]
        a.create_session(SessionRequest())                                   # entrega a de sondagem
        sid = a.create_session(SessionRequest())["session_id"]               # nova: abre em "auto", o adaptador troca
        assert ("session/set_mode", {"sessionId": sid, "modeId": "default"}) in calls
        ev: list[AgentEvent] = []
        assert a.send_prompt(sid, "echo 1", ev.append).result(20).stop_reason == "end_turn"
    finally:
        a.disconnect()


@pytest.mark.real_agent
def test_real_claude_agent_acp_passes_the_contract(tmp_path):
    """Opt-in (`-m real_agent`): claude-agent-acp desta maquina. CUSTA tokens da assinatura (prompts curtos)."""
    import os

    from tests.platform.adapter_contract import CANARY
    ctx = ContractContext(prompt="Responda apenas: ok", slow_prompt="Conte devagar de 1 a 300, um numero por linha.",
                          prompt_timeout=180, cancel_timeout=20, connect_timeout=120,
                          base_env={**os.environ, **CANARY})                 # PATH real (acha o adaptador) + segredos-canario
    ws = tmp_path / "ws"
    ws.mkdir()
    failures = run_contract(lambda env: AcpAdapter(["claude-agent-acp"], ws, env=env), ctx)
    assert failures == {}, failures


def test_a_session_comes_back_through_session_load_after_the_agent_process_dies(tmp_path):
    from agent_bar.platform.adapter import SessionNotFound
    first = make(tmp_path)
    first.connect()
    sid = first.create_session(SessionRequest(model_id="fake-pro"))["session_id"]
    first.disconnect()                                                       # o processo do agente morre
    second = make(tmp_path)
    second.connect()
    try:
        rec = second.resume_session(sid)
        assert rec["session_id"] == sid and sid in second.get_status().sessions
        ev: list[AgentEvent] = []
        assert second.send_prompt(sid, "echo 2", ev.append).result(20).stop_reason == "end_turn"
        with pytest.raises(SessionNotFound):
            second.resume_session("s-que-nunca-existiu")
    finally:
        second.disconnect()


def test_session_manager_recovers_a_claude_like_session_with_context(tmp_path):
    import subprocess
    import time

    from agent_bar.platform.adapter import ProviderUnavailable
    from agent_bar.platform.sessions import SessionManager
    m = SessionManager({"fake-acp": lambda: make(tmp_path)})
    try:
        s = m.open("fake-acp")
        adapter = m._adapters[("fake-acp", "")]
        subprocess.run(["taskkill", "/PID", str(adapter.owned_pids()[0]), "/T", "/F"], capture_output=True)
        end = time.monotonic() + 10
        while time.monotonic() < end and adapter.owned_pids():
            time.sleep(0.05)
        with pytest.raises(ProviderUnavailable):
            m.send(s["id"], "echo 1").result(20)
        assert m.get(s["id"])["state"] == "ERROR"
        r = m.recover(s["id"])
        assert r["recovered_without_context"] is False and r["session"]["session_id"] == s["session"]["session_id"]
        assert m.send(s["id"], "echo 1").result(20).stop_reason == "end_turn"
    finally:
        m.close_all()


def test_image_blocks_reach_an_agent_that_announces_prompt_image(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                                  "0000000d49444154789c63f8cfc0f01f00050001ffa59b8cd50000000049454e44ae426082"))
    a = make(tmp_path, {"AGENT_BAR_FAKE_IMAGES": "1"})
    a.connect()
    try:
        assert a.describe()["capabilities"]["images"]["state"] == "SUPPORTED"
        sid = a.create_session(SessionRequest())["session_id"]
        ev: list[AgentEvent] = []
        a.send_prompt(sid, "echo 1", ev.append, images=[png]).result(20)
        assert any(e.text.startswith("imagens=image/png:") for e in ev)
    finally:
        a.disconnect()


def test_contract_passes_with_an_agent_that_takes_images(tmp_path):
    assert run_contract(lambda env: make(tmp_path, {**env, "AGENT_BAR_FAKE_IMAGES": "1"}), CTX, only=("images",)) == {}


def test_usage_update_from_the_agent_becomes_a_usage_event(tmp_path):
    from agent_bar.platform.usage import UsageSnapshot
    a = make(tmp_path)
    a.connect()
    try:
        sid = a.create_session(SessionRequest())["session_id"]
        ev: list[AgentEvent] = []
        a.send_prompt(sid, "usage", ev.append).result(20)
        snaps = [UsageSnapshot.from_dict(e.data) for e in ev if e.kind == "usage"]
        assert [s.context_used.value for s in snaps[:3]] == [41200, 41950, 41950]
        assert snaps[2].cost_api_equivalent_usd.state == "ESTIMATED" and snaps[0].cost_api_equivalent_usd.state == "UNAVAILABLE"
        assert (snaps[1].account_5h_utilization.value, snaps[1].account_7d_utilization.resets_at) == (0.16, 1795716000)
        assert snaps[0].model_reported == "claude-opus-5-5"
        totals = snaps[3]                                                     # resposta do prompt: totais da sessao
        assert (totals.session_input_tokens.value, totals.session_output_tokens.value,
                totals.session_cache_tokens.value, totals.session_background_tokens.value) == (1200, 340, 40400, 1189)
        assert totals.model_reported == "claude-opus-5-5"
    finally:
        a.disconnect()


def test_a_fixed_context_window_is_refused_not_ignored(tmp_path):
    """Review do #94 (NIT): o ACP não fixa a janela; pedir context_tokens é recusado com erro tipado."""
    from agent_bar.platform.adapter import CapabilityUnsupported
    a = make(tmp_path)
    a.connect()
    try:
        assert a.supports_context_tokens is False
        with pytest.raises(CapabilityUnsupported, match="context_tokens"):
            a.create_session(SessionRequest(context_tokens=16384))
        assert a.create_session(SessionRequest())["session_id"]
    finally:
        a.disconnect()
