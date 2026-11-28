"""Interface do executor local com o roteador Local First (Frente E, agent-runtime#72/#73), com Ollama FALSO e motor
FALSO so aqui. Cobra: job validado antes de rodar; motor fixado (caminho de producao + sha40 + limpo) ou REFUSED;
deterministico sem LLM e com zero token; local pelo MESMO adapter/ferramentas; desfecho pelo executor e pelo motor;
INFRA nao conta contra o executor; falha reutilizavel; nenhum provedor cloud; documentos conferidos contra os JSON
Schemas de docs/contracts; agregado de prova por (executor, task_type)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bar.platform import capability_proofs as cp
from agent_bar.platform import compat_bench as cb
from agent_bar.platform import local_job as lj
from agent_bar.platform.contracts import validate
from agent_bar.platform.ollama import OllamaAdapter
from agent_bar.platform.render_sample import ToolBox
from tests.platform.fake_ollama import FakeOllama, fake_digest
from tests.platform.test_render_sample import CAMS, FakeEngine, engine_root, tools  # noqa: F401

SHA = "a" * 40
CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
MODELS = {"tool-a": {"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 8192}}}


# ------------------------------------------------------------------------------- contratos (validador do produto)
def check(doc, schema):
    validate(doc, schema)                                                    # levanta ContractError no 1o desvio


def contract(name):
    return json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------- montagem
@pytest.fixture
def server():
    with FakeOllama(MODELS) as s:
        yield s


def job(**over):
    base = {"schema": "p2s.local_job.v1", "job_id": "j-1", "task_type": "build",
            "executor": {"kind": "deterministic"}, "inputs": {"fixture_ref": "room_001"},
            "engine_ref": {"repo": "Plan2Space/sketchup-mcp-next", "sha40": SHA}, "attempt": 1, "feedback": None}
    return {**base, **over}


def local(model="tool-a"):
    return {"kind": "local", "model_id": model, "adapter": "ollama"}


class Rig:
    def __init__(self, server, engine_root, tmp_path, busy=False, head=None):
        self.engine = FakeEngine()
        self.build, self.render = tools(engine_root, tmp_path, self.engine, busy=busy)
        self.box = ToolBox([self.build, self.render])
        self.server = server
        self.engine_root = engine_root
        self.head = head or (lambda _p: {"commit": SHA, "dirty": False})

    def adapter(self):
        return OllamaAdapter(self.server.endpoint, tool_host=self.box)

    def run(self, j, production="SAME"):
        prod = str(self.engine_root) if production == "SAME" else production
        return lj.run_job(j, build_host=lambda: self.box, local_adapter=self.adapter, engine_root=self.engine_root,
                          production=prod, head=self.head, timeout_s=30)

    def chats(self):
        return [b for p, b in self.server.requests if p == "/api/chat"]


def raw(name="build_room_sample", args=None, cid="c1"):
    return {"id": cid, "function": {"index": 0, "name": name, "arguments": args or {"fixture_ref": "room_001"}}}


# ------------------------------------------------------------------------------- deterministico
def test_a_deterministic_build_runs_the_engine_without_any_model(server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    out = rig.run(job())
    assert out["status"] == "SUCCEEDED" and out["error"] is None and out["executor_id"] == "deterministic"
    assert out["tokens_measured"] == 0 and out["cloud"] == {"tokens": 0, "providers_called": []}
    assert out["artifacts"][0]["kind"] == "skp" and out["artifacts"][0]["sha256"]
    assert rig.chats() == []                                                 # nenhum modelo foi chamado
    stored = json.loads(rig.build.registry.get(out["request_id"])["result"])
    assert stored["provenance"]["roles"]["tool_requester"] == {"kind": "runtime", "id": "local_job:deterministic"}
    check(out, contract("p2s.local_outcome.v1"))


def test_a_deterministic_executor_exception_is_infra_and_the_job_still_has_an_outcome(server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)

    def boom(call, ctx):
        raise RuntimeError("executor quebrou")
    rig.box.tools[0].run = boom
    out = rig.run(job())
    assert out["status"] == "FAILED" and out["error"] == "INFRA:EXECUTOR_ERROR" and out["failure"] is None
    check(out, contract("p2s.local_outcome.v1"))


def test_a_deterministic_render_of_a_build(server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    b = rig.run(job(inputs={"fixture_ref": "plan_alt_5x3_e3a"}))
    r = rig.run(job(job_id="j-2", task_type="render", inputs={"run_id": b["run_id"], "cameras": CAMS}))
    assert r["status"] == "SUCCEEDED" and [a["kind"] for a in r["artifacts"]] == ["render", "render"]
    check(r, contract("p2s.local_outcome.v1"))


def test_the_same_attempt_replays_and_a_new_attempt_runs_again(server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    first = rig.run(job())
    again = rig.run(job())
    n = len(rig.engine.calls)
    assert again["request_id"] == first["request_id"] and n == 1             # mesmo job e tentativa: replay
    retry = rig.run(job(attempt=2))
    assert retry["request_id"] != first["request_id"] and len(rig.engine.calls) == 2


# ------------------------------------------------------------------------------- local (modelo)
def test_a_local_model_builds_through_the_same_tools(server, engine_root, tmp_path):
    server.tool_script = {"calls": [raw()]}
    rig = Rig(server, engine_root, tmp_path)
    out = rig.run(job(executor=local()))
    assert out["status"] == "SUCCEEDED"
    assert out["executor_id"] == f"ollama:tool-a@{fake_digest('tool-a')[:8]}"
    assert out["tokens_measured"] == 15                                      # 13 de entrada + 2 de saida (Ollama)
    assert rig.chats()[0]["options"] == cb.INFERENCE                         # mesmas opcoes do benchmark
    assert {t["function"]["name"] for t in rig.chats()[0]["tools"]} == {"build_room_sample", "render_room_sample"}
    assert out["failure"] is None and out["assistance"] == []
    check(out, contract("p2s.local_outcome.v1"))


@pytest.mark.parametrize("script, status, klass", [
    ({"content": "vou fazer"}, "FAILED", "CAPABILITY"),                                   # nao chamou nada
    ({"calls": [raw(name="build_everything")]}, "TOOL_ERROR", "PROTOCOL"),               # nome fora do publicado
    ({"calls": [raw(args={"fixture_ref": "room_999"})]}, "TOOL_ERROR", "SCHEMA"),         # fixture fora da lista
    ({"calls": [raw(args={"fixture_ref": "room_alt_5x3"})]}, "TOOL_ERROR", "CONTEXT"),    # valida, mas nao a do job
])
def test_local_failures_are_classified_and_recorded(server, engine_root, tmp_path, script, status, klass):
    server.tool_script = script
    rig = Rig(server, engine_root, tmp_path)
    out = rig.run(job(executor=local()))
    assert out["status"] == status and out["failure_class"] == klass
    f = out["failure"]
    assert f["failure_class"] == klass and f["helpful_example"] == {"tool": "build_room_sample",
                                                                    "arguments": {"fixture_ref": "room_001"}}
    check(f, contract("p2s.local_failure.v1"))
    check(out, contract("p2s.local_outcome.v1"))
    if klass == "CONTEXT":
        assert f["invalid_argument"] == {"name": "fixture_ref", "sent": "room_alt_5x3", "expected": "room_001"}


def test_an_engine_failure_is_infra_and_not_a_failure_record(server, engine_root, tmp_path):
    server.tool_script = {"calls": [raw()]}
    rig = Rig(server, engine_root, tmp_path, busy=True)
    out = rig.run(job(executor=local()))
    assert out["status"] == "FAILED" and out["error"] == "INFRA:SKETCHUP_BUSY" and out["failure"] is None


def test_the_router_feedback_reaches_the_model_and_is_recorded_as_assistance(server, engine_root, tmp_path):
    server.tool_script = {"calls": [raw()]}
    rig = Rig(server, engine_root, tmp_path)
    fb = {"failure_class": "SCHEMA", "attempt": 1, "executor_id": "ollama:tool-a@x", "error": "FIXTURE_NOT_ALLOWED"}
    out = rig.run(job(executor=local(), attempt=2, feedback=fb))
    user = [m for m in rig.chats()[0]["messages"] if m["role"] == "user"][-1]["content"]
    assert "FIXTURE_NOT_ALLOWED" in user and "Corrija" in user
    assert out["assistance"] == [{"kind": "structured_feedback", "from": "roteador (agent-runtime)", "content": fb,
                                  "attempt": 2}]


# ------------------------------------------------------------------------------- pino do motor e job invalido
@pytest.mark.parametrize("production, head, why", [
    (None, None, "P2S_PRODUCTION_ENGINE"),
    ("OTHER", None, "nao e o checkout de producao"),
    ("SAME", {"commit": "b" * 40, "dirty": False}, "!= engine_ref.sha40"),
    ("SAME", {"commit": SHA, "dirty": True}, "mudancas locais"),
    ("SAME", {"commit": SHA, "dirty": None}, "mudancas locais"),
])
def test_an_unpinned_engine_is_refused_before_anything_runs(server, engine_root, tmp_path, production, head, why):
    rig = Rig(server, engine_root, tmp_path, head=(lambda _p: head) if head else None)
    prod = str(tmp_path / "outro-motor") if production == "OTHER" else production
    out = rig.run(job(executor=local()), production=prod)
    assert out["status"] == "REFUSED" and out["error"].startswith("ENGINE_NOT_PINNED") and why in out["error"]
    assert rig.engine.calls == [] and rig.chats() == []
    check(out, contract("p2s.local_outcome.v1"))


@pytest.mark.parametrize("bad", [
    {"schema": "x"}, {"job_id": "../etc"}, {"task_type": "delete"}, {"executor": {"kind": "cloud"}},
    {"executor": {"kind": "local"}}, {"inputs": {"fixture_ref": "room_001", "skp": "C:/x"}},
    {"task_type": "render", "inputs": {"run_id": "lx-1", "cameras": ["a", "b", "c"]}},
    {"engine_ref": {"repo": "Plan2Space/sketchup-mcp-next", "sha40": "main"}}, {"attempt": 0},
    {"feedback": {"failure_class": "WHATEVER"}},
])
def test_an_invalid_job_is_refused_and_nothing_runs(server, engine_root, tmp_path, bad):
    rig = Rig(server, engine_root, tmp_path)
    out = rig.run(job(**bad))
    assert out["status"] == "REFUSED" and out["error"].startswith("JOB_INVALID") and rig.engine.calls == []


def test_valid_jobs_match_the_job_contract():
    for j in (job(), job(executor=local()), job(task_type="render", inputs={"run_id": "lx-1", "cameras": CAMS})):
        assert lj.validate_job(j) == []
        check(j, contract("p2s.local_job.v1"))


# ------------------------------------------------------------------------------- prova agregada
def test_capability_proofs_aggregate_benchmark_and_jobs(server, engine_root, tmp_path):
    server.script_by_prompt.update({"liste os nomes exatos": {"content": "build_room_sample\nrender_room_sample"},
                                    "Chame a ferramenta build_room_sample": {"calls": [raw()]},
                                    "sala de 5 x 3": {"calls": [raw(args={"fixture_ref": "room_alt_5x3"})]}})
    rig = Rig(server, engine_root, tmp_path)
    a = rig.adapter()
    a.inference_options = dict(cb.INFERENCE)
    host = cb.RecordingHost(a.tool_host)
    a.tool_host = host
    a.connect()
    run = cb.run_model(a, host, "tool-a", ["C0", "C1", "C2"])
    bdir = tmp_path / "proofs" / "bench-x"
    bdir.mkdir(parents=True)
    (bdir / "benchmark_run.json").write_text(json.dumps(run, default=str), encoding="utf-8")
    server.script_by_prompt.clear()
    server.tool_script = {"calls": [raw()]}
    ok = rig.run(job(job_id="j-ok", executor=local()))
    busy = Rig(server, engine_root, tmp_path, busy=True).run(job(job_id="j-busy", executor=local()))
    for o in (ok, busy):
        d = tmp_path / "proofs" / o["job_id"]
        d.mkdir(parents=True)
        (d / "outcome.json").write_text(json.dumps(o, default=str), encoding="utf-8")
    doc, failures = cp.aggregate(cp.find_sources([tmp_path / "proofs"]))
    check(doc, contract("p2s.local_capability.v1"))
    ex = f"ollama:tool-a@{fake_digest('tool-a')[:8]}"
    by = {(i["executor_id"], i["task_type"]): i for i in doc["items"]}
    build = by[(ex, "build")]                                                # C1 PASS + C2 FAIL + job ok + job infra
    assert (build["runs"], build["passes"], build["fails"], build["infra_errors"]) == (4, 2, 1, 1)
    assert build["success_rate"] == round(2 / 3, 4)                         # INFRA fora do denominador
    assert by[(ex, "discovery")]["success_rate"] == 1.0
    assert len(build["source"]) == 3 and all(len(s["sha256"]) == 64 for s in build["source"])
    assert len(failures) == 1 and failures[0]["invalid_argument"] == {"name": "fixture_ref", "sent": "room_alt_5x3",
                                                                       "expected": "plan_alt_5x3_e3a"}
    for f in failures:
        check(f, contract("p2s.local_failure.v1"))


def test_without_a_real_denominator_there_is_no_success_rate(tmp_path):
    out = {"schema": "p2s.local_outcome.v1", "executor_id": "ollama:x@1", "task_type": "build", "status": "FAILED",
           "error": "INFRA:SKETCHUP_BUSY", "engine": {"sha40": SHA}}
    (tmp_path / "outcome.json").write_text(json.dumps(out), encoding="utf-8")
    doc, _ = cp.aggregate([tmp_path / "outcome.json"])
    assert doc["items"][0]["success_rate"] is None and doc["items"][0]["infra_errors"] == 1


def test_a_refused_job_never_counts_as_a_run(tmp_path):
    out = {"schema": "p2s.local_outcome.v1", "executor_id": "ollama:x@1", "task_type": "build", "status": "REFUSED",
           "error": "ENGINE_NOT_PINNED: sujo", "engine": {"sha40": SHA}}
    (tmp_path / "outcome.json").write_text(json.dumps(out), encoding="utf-8")
    doc, failures = cp.aggregate([tmp_path / "outcome.json"])
    assert doc["items"] == [] and failures == []                            # nao rodou: nao mede o executor


# ------------------------------------------------------------------------------- caminho do contrato (revisao #223)
class _Tool:
    engine_root = Path("motor")


def _cli(monkeypatch, tmp_path, results):
    """Roda a CLI `local_job run` com o motor/modelo trocados por desfechos prontos (um por execucao)."""
    from agent_bar.platform import sketchup_sample
    seq = iter(results)

    def fake_run_job(j, **_):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return {"schema": "p2s.local_outcome.v1", "job_id": j["job_id"], "task_type": j["task_type"],
                "started_at": "t0", "finished_at": "t1", "cloud": {"tokens": 0, "providers_called": []},
                "assistance": [], "permissions": [], "executor_id": "deterministic", "tokens_measured": 0,
                "tokens_estimated": None, "attempt": j["attempt"], **r}

    monkeypatch.setattr(lj, "run_job", fake_run_job)
    monkeypatch.setattr(sketchup_sample, "sample_tool_from_env", lambda: _Tool())
    jf = tmp_path / "job.json"
    jf.write_text(json.dumps(job()), encoding="utf-8")
    out = tmp_path / "out"
    rcs = [lj.main(["run", str(jf), "--out", str(out)]) for _ in results]
    return rcs, out / "j-1"


OK = {"status": "SUCCEEDED", "error": None, "failure": None}
BAD = {"status": "FAILED", "error": "INFRA:SKETCHUP_BUSY", "failure": None}


def test_a_failed_rerun_of_the_same_job_id_is_what_the_router_reads(monkeypatch, tmp_path):
    rcs, d = _cli(monkeypatch, tmp_path, [OK, BAD])
    assert rcs == [0, 1]
    cur = json.loads((d / "outcome.json").read_text(encoding="utf-8"))
    assert cur["status"] == "FAILED"                                         # o roteador le a execucao mais recente
    check(cur, contract("p2s.local_outcome.v1"))
    hist = json.loads((d / "outcome.1.json").read_text(encoding="utf-8"))
    assert hist["status"] == "SUCCEEDED"                                     # a anterior vai para o historico
    assert sorted(p.name for p in d.iterdir()) == ["outcome.1.json", "outcome.json"]


def test_a_new_success_is_not_hidden_behind_an_old_failure(monkeypatch, tmp_path):
    _, d = _cli(monkeypatch, tmp_path, [BAD, BAD, OK])
    assert json.loads((d / "outcome.json").read_text(encoding="utf-8"))["status"] == "SUCCEEDED"
    assert [json.loads((d / f"outcome.{n}.json").read_text(encoding="utf-8"))["status"] for n in (1, 2)] == \
        ["FAILED", "FAILED"]


def test_a_crashed_rerun_never_leaves_the_old_success_in_place(monkeypatch, tmp_path):
    rcs, d = _cli(monkeypatch, tmp_path, [OK, RuntimeError("ollama caiu")])
    cur = json.loads((d / "outcome.json").read_text(encoding="utf-8"))
    assert rcs == [0, 1] and cur["status"] == "FAILED" and cur["error"] == "INFRA:RUNNER_ERROR: RuntimeError"
    assert cur["job_id"] == "j-1" and cur["attempt"] == 1 and cur["cloud"] == {"tokens": 0, "providers_called": []}
    check(cur, contract("p2s.local_outcome.v1"))


def test_the_previous_outcome_leaves_the_contract_path_before_the_run_starts(tmp_path):
    d = tmp_path / "j-1"
    d.mkdir()
    (d / "outcome.json").write_text(json.dumps({"status": "SUCCEEDED"}), encoding="utf-8")
    seen = {}

    def execute():
        seen["during"] = (d / "outcome.json").exists()                       # o roteador nao ve o velho durante o run
        return {**OK, "schema": "p2s.local_outcome.v1"}
    lj.record_outcome(tmp_path, "j-1", job(), execute)
    assert seen == {"during": False}
    assert not list(d.glob("*.tmp"))                                          # escrita atomica, sem sobra


def test_an_adapter_other_than_ollama_is_refused():
    assert any("adapter" in e for e in lj.validate_job(job(executor={**local(), "adapter": "openai"})))


@pytest.mark.parametrize("model", ["gpt-oss:120b-cloud", "deepseek-v3.1:671b-cloud", "qwen3-coder:cloud"])
def test_an_ollama_cloud_model_is_refused(model):
    assert any("cloud" in e for e in lj.validate_job(job(executor=local(model))))


@pytest.mark.parametrize("endpoint,ok", [("http://127.0.0.1:11434", True), ("http://localhost:11434", True),
                                         ("http://[::1]:11434", True), ("https://ollama.com", False),
                                         ("http://192.168.0.9:11434", False)])
def test_only_a_loopback_ollama_endpoint_is_local(endpoint, ok):
    assert (lj.endpoint_refusal(endpoint) is None) is ok


def test_a_remote_endpoint_refuses_the_local_job_before_any_model_call(server, engine_root, tmp_path):
    rig = Rig(server, engine_root, tmp_path)
    out = lj.run_job(job(executor=local()), build_host=lambda: rig.box, local_adapter=rig.adapter,
                     engine_root=engine_root, production=str(engine_root), head=rig.head, timeout_s=30,
                     endpoint="https://ollama.com")
    assert out["status"] == "REFUSED" and out["error"].startswith("NOT_LOCAL") and rig.chats() == []
    check(out, contract("p2s.local_outcome.v1"))
