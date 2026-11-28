"""Benchmark de compatibilidade modelo x motor (pedido do Felipe 2026-11-27 §8-§11), com Ollama FALSO e motor FALSO so
aqui (a medicao real usa o Ollama e o SketchUp de verdade, com o slot).

Cobra: mesmas ferramentas/system prompt/opcoes de inferencia para todos os modelos; cada caso numa conversa nova;
criterio de cada caso pelo que chegou ao executor e pelo resultado do motor, nunca pela frase do modelo; falha do motor
e INFRA_ERROR (nao FAIL do modelo); casos sem ferramenta no motor ficam BLOCKED com dono; modelo sem tools =
NOT_SUPPORTED sem conversa; eixos separados e nenhuma nota unica; nenhuma assistencia escondida."""
from __future__ import annotations

import json

import pytest

from agent_bar.platform import compat_bench as cb
from agent_bar.platform.ollama import OllamaAdapter
from agent_bar.platform.render_sample import ToolBox
from tests.platform.fake_ollama import FakeOllama
from tests.platform.test_render_sample import FakeEngine, engine_root, tools  # noqa: F401

MODELS = {"tool-a": {"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 8192}},
          "tool-b": {"capabilities": ["completion", "tools", "thinking"], "model_info": {"x.context_length": 8192}},
          "plain-c": {"capabilities": ["completion"], "model_info": {"x.context_length": 8192}}}
C0 = "liste os nomes exatos"
C1 = "Chame a ferramenta build_room_sample"
C2 = "sala de 5 x 3 metros com cama"


def raw(fixture, cid="call_1", name="build_room_sample"):
    return {"id": cid, "function": {"index": 0, "name": name, "arguments": {"fixture_ref": fixture}}}


@pytest.fixture
def server():
    with FakeOllama(MODELS) as s:
        yield s


def bench(server, engine_root, tmp_path, busy=False):
    build, render = tools(engine_root, tmp_path, FakeEngine(), busy=busy)
    a = OllamaAdapter(server.endpoint, tool_host=ToolBox([build, render]))
    a.inference_options = dict(cb.INFERENCE)
    host = cb.RecordingHost(a.tool_host)
    a.tool_host = host
    a.connect()
    return a, host


def good_script(server, model=None):
    pre = f"@{model}|" if model else ""
    server.script_by_prompt.update({
        pre + C0: {"content": "As ferramentas sao:\n- build_room_sample\n- render_room_sample"},
        pre + C1: {"calls": [raw("room_001")]},
        pre + C2: {"calls": [raw("plan_alt_5x3_e3a")]},
    })


def case(run, cid):
    return next(c for c in run["cases"] if c["id"] == cid)


def test_a_model_that_operates_the_engine_passes_c0_to_c2(server, engine_root, tmp_path):
    good_script(server)
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", ["C0", "C1", "C2"])
    assert [(c["id"], c["status"]) for c in run["cases"]] == [("C0", "PASS"), ("C1", "PASS"), ("C2", "PASS")]
    c2 = case(run, "C2")
    assert c2["tool_calls"][0]["arguments"] == {"fixture_ref": "plan_alt_5x3_e3a"}
    assert c2["tool_calls"][0]["result"]["status"] == "completed"
    assert c2["tool_calls"][0]["result"]["inspection_verdict"] == "PASS"
    assert run["schema"] == cb.SCHEMA and run["assistance"] == [] and all(c["assistance"] == [] for c in run["cases"])
    assert len(run["permissions"]) == 2 and all(p["decided_by"] == "runner" for p in run["permissions"])


def test_each_case_runs_in_a_new_conversation(server, engine_root, tmp_path):
    good_script(server)
    a, host = bench(server, engine_root, tmp_path)
    cb.run_model(a, host, "tool-a", ["C0", "C1", "C2"])
    users = [[m for m in b["messages"] if m["role"] == "user"] for p, b in server.requests if p == "/api/chat"]
    assert all(len(u) == 1 for u in users)                                   # nenhum caso herda o historico de outro
    assert a._sessions == {}                                                 # cada conversa do caso foi fechada


def test_the_same_conditions_go_to_every_model(server, engine_root, tmp_path):
    good_script(server)
    a, host = bench(server, engine_root, tmp_path)
    ra = cb.run_model(a, host, "tool-a", ["C0", "C1"])
    rb = cb.run_model(a, host, "tool-b", ["C0", "C1"])
    keys = ("tools", "tools_sha256", "system_prompt_sha256", "inference", "permission")
    assert {k: ra["conditions"][k] for k in keys} == {k: rb["conditions"][k] for k in keys}
    chats = [b for p, b in server.requests if p == "/api/chat"]
    assert {json.dumps(b.get("options"), sort_keys=True) for b in chats} == {json.dumps(cb.INFERENCE, sort_keys=True)}
    assert len({json.dumps(b["tools"], sort_keys=True) for b in chats if "tools" in b}) == 1
    assert ra["versions"]["model"]["id"] == "tool-a" and rb["versions"]["model"]["id"] == "tool-b"
    assert ra["versions"]["model"]["digest"] != rb["versions"]["model"]["digest"]


@pytest.mark.parametrize("content, why", [
    ("build_room_sample", "faltou"),                                         # esqueceu uma
    ("build_room_sample\nrender_room_sample\ndelete_room", "fora do publicado"),   # inventou uma
])
def test_c0_fails_on_a_missing_or_invented_tool(server, engine_root, tmp_path, content, why):
    server.script_by_prompt[C0] = {"content": content}
    a, host = bench(server, engine_root, tmp_path)
    c0 = case(cb.run_model(a, host, "tool-a", ["C0"]), "C0")
    assert c0["status"] == "FAIL" and any(why in r for r in c0["reasons"])


def test_c0_fails_when_the_model_calls_instead_of_listing(server, engine_root, tmp_path):
    server.script_by_prompt[C0] = {"calls": [raw("room_001")]}
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", ["C0"])
    c0 = case(run, "C0")
    assert c0["status"] == "FAIL" and "chamou ferramenta" in c0["reasons"][0]
    # descoberta nunca abre o SketchUp: a permissao e negada e o motor nao roda
    assert c0["tool_calls"][0]["result"]["error"]["code"] == "PERMISSION_DENIED"
    assert run["permissions"] == [{**run["permissions"][0], "case": "C0", "decided": "reject_once"}]
    assert not (engine_root / "runs" / "local-executor").exists() or not any(
        p.name.startswith("lx-") for p in (engine_root / "runs" / "local-executor").iterdir())


def test_c0_accepts_published_fixtures_and_parameter_names_in_the_answer(server, engine_root, tmp_path):
    server.script_by_prompt[C0] = {"content": "build_room_sample (fixture_ref: room_001 ou plan_alt_5x3_e3a)\n"
                                              "render_room_sample (run_id, cameras: amostra_lateral)"}
    a, host = bench(server, engine_root, tmp_path)
    assert case(cb.run_model(a, host, "tool-a", ["C0"]), "C0")["status"] == "PASS"


def test_c2_fails_when_the_model_picks_another_fixture(server, engine_root, tmp_path):
    server.script_by_prompt[C2] = {"calls": [raw("room_alt_5x3")]}           # 5x3, mas sem cama
    a, host = bench(server, engine_root, tmp_path)
    c2 = case(cb.run_model(a, host, "tool-a", ["C2"]), "C2")
    assert c2["status"] == "FAIL" and "plan_alt_5x3_e3a" in c2["reasons"][0]


@pytest.mark.parametrize("calls, why", [
    ([], "nenhuma chamada"),
    ([raw("room_001", name="build_everything")], "recusada pelo Studio"),    # nome fora do publicado
    ([raw("room_001", cid="a"), raw("room_alt_5x3", cid="b")], "2 chamadas"),
])
def test_c1_fails_on_wrong_calls(server, engine_root, tmp_path, calls, why):
    server.script_by_prompt[C1] = {"calls": calls} if calls else {"content": "vou construir"}
    a, host = bench(server, engine_root, tmp_path)
    c1 = case(cb.run_model(a, host, "tool-a", ["C1"]), "C1")
    assert c1["status"] == "FAIL" and any(why in r for r in c1["reasons"])


def test_an_engine_failure_is_infra_not_a_model_failure(server, engine_root, tmp_path):
    good_script(server)
    a, host = bench(server, engine_root, tmp_path, busy=True)               # outro SketchUp aberto
    c1 = case(cb.run_model(a, host, "tool-a", ["C1"]), "C1")
    assert c1["status"] == "INFRA_ERROR" and "SKETCHUP_BUSY" in c1["reasons"][0]


def test_an_executor_exception_is_infra_never_a_model_failure(server, engine_root, tmp_path):
    """Revisao do #159 (MUST): o executor levantou excecao numa chamada CERTA do modelo; o adapter transforma em
    EXECUTOR_ERROR, mas a chamada precisa ficar registrada e o caso e INFRA_ERROR, nunca FAIL do modelo."""
    good_script(server)
    a, host = bench(server, engine_root, tmp_path)

    def boom(call, ctx):
        raise RuntimeError("executor quebrou")
    host.inner.tools[0].run = boom                                            # build_room_sample levanta
    c1 = case(cb.run_model(a, host, "tool-a", ["C1"]), "C1")
    assert c1["status"] == "INFRA_ERROR" and "EXECUTOR_ERROR" in c1["reasons"][0]
    assert c1["tool_calls"][0]["arguments"] == {"fixture_ref": "room_001"}
    assert c1["tool_calls"][0]["result"]["error"]["code"] == "EXECUTOR_ERROR"


def test_a_model_without_tools_is_not_supported_and_never_chats(server, engine_root, tmp_path):
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "plain-c", ["C0", "C1"])
    assert {c["status"] for c in run["cases"]} == {"NOT_SUPPORTED"}
    assert not [p for p, b in server.requests if p == "/api/chat"]


def test_cases_without_an_engine_tool_are_blocked_with_an_owner(server, engine_root, tmp_path):
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", [c.id for c in cb.CASES])
    by = {c["id"]: c for c in run["cases"]}
    for cid in ("C3", "C7", "C8", "C9"):
        assert by[cid]["status"] == "BLOCKED" and by[cid]["owner"] and by[cid]["reasons"]
    for cid in ("C4", "C5", "C6"):
        assert by[cid]["status"] == "NOT_RUN"
    assert not [p for p, b in server.requests if p == "/api/chat" and "tools" in b and
                any("Parametr" in str(m.get("content")) for m in b["messages"])]


def test_blocked_cases_cite_the_exact_gap_of_the_engine_matrix(server, engine_root, tmp_path):
    import hashlib
    matrix = {"schema_version": "1.0.0", "engine": {"ref": "e" * 40}, "gaps": [
        {"id": "G1", "title": "write_fixture fora do motor"}, {"id": "G2", "title": "allowlist fora do motor"},
        {"id": "G3", "title": "sem inspecao avulsa"}, {"id": "G4", "title": "sem refinamento"},
        {"id": "G5", "title": "publicacao fora do motor"}, {"id": "G10", "title": "sem troca de material"}]}
    path = engine_root / "contracts" / "tool_compat_matrix.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(matrix), encoding="utf-8")
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", ["C3", "C7", "C8", "C9"])
    by = {c["id"]: c for c in run["cases"]}
    assert by["C3"]["gaps"] == ["G1"] and "G1: write_fixture fora do motor" in by["C3"]["reasons"]
    assert by["C7"]["gaps"] == ["G4", "G10"] and "G10: sem troca de material" in by["C7"]["reasons"]
    assert by["C8"]["gaps"] == ["G5", "G3"] and by["C9"]["gaps"] == ["G2"]
    assert run["conditions"]["matrix"] == {"path": "contracts/tool_compat_matrix.json", "present": True,
                                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                           "engine": "e" * 40}


def test_without_the_matrix_in_the_pinned_engine_the_record_says_so(server, engine_root, tmp_path):
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", ["C3"])
    assert run["conditions"]["matrix"]["present"] is False
    assert any("G1" in r and "ausente" in r for r in run["cases"][0]["reasons"])


def test_axes_are_reported_separately_without_a_single_score(server, engine_root, tmp_path):
    good_script(server)
    a, host = bench(server, engine_root, tmp_path)
    run = cb.run_model(a, host, "tool-a", [c.id for c in cb.CASES])
    assert set(run["axes"]) == set(cb.AXES)
    assert run["axes"]["operation"]["by_status"]["PASS"] == 3
    assert run["axes"]["layout"]["cases"] == ["C9"] and run["axes"]["layout"]["by_status"] == {"BLOCKED": 1}
    assert not {"score", "nota", "overall"} & set(run)


def test_every_case_maps_to_one_axis_and_the_ladder_is_complete():
    assert [c.id for c in cb.CASES] == [f"C{i}" for i in range(10)]
    assert all(c.axis in cb.AXES for c in cb.CASES)


def test_inference_options_reach_the_provider_only_when_set(server):
    a = OllamaAdapter(server.endpoint)
    a.connect()
    from agent_bar.platform.adapter import SessionRequest
    sid = a.create_session(SessionRequest(model_id="tool-a"))["session_id"]
    a.send_prompt(sid, "oi", lambda e: None).result(10)
    assert "options" not in [b for p, b in server.requests if p == "/api/chat"][-1]   # Studio: padrao do modelo
    a.inference_options = {"temperature": 0, "seed": 42}
    sid = a.create_session(SessionRequest(model_id="tool-a", context_tokens=4096))["session_id"]
    a.send_prompt(sid, "oi", lambda e: None).result(10)
    assert [b for p, b in server.requests if p == "/api/chat"][-1]["options"] == {"temperature": 0, "seed": 42,
                                                                                    "num_ctx": 4096}
