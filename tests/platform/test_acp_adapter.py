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
    base = {"PATH": sys.prefix, "SYSTEMROOT": "C:\\Windows", **(env or {})}
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
