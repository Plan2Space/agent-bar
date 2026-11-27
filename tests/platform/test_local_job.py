"""Interface do executor local com o roteador Local First (Frente E, agent-runtime#72/#73), com Ollama FALSO e motor
FALSO so aqui. Cobra: job validado antes de rodar; motor fixado (caminho de producao + sha40 + limpo) ou REFUSED;
deterministico sem LLM e com zero token; local pelo MESMO adapter/ferramentas; desfecho pelo executor e pelo motor;
INFRA nao conta contra o executor; falha reutilizavel; nenhum provedor cloud; documentos conferidos contra os JSON
Schemas de docs/contracts; agregado de prova por (executor, task_type)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_bar.platform import capability_proofs as cp
from agent_bar.platform import compat_bench as cb
from agent_bar.platform import local_job as lj
from agent_bar.platform.ollama import OllamaAdapter
from agent_bar.platform.render_sample import ToolBox
from tests.platform.fake_ollama import FakeOllama, fake_digest
from tests.platform.test_render_sample import CAMS, FakeEngine, engine_root, tools  # noqa: F401

SHA = "a" * 40
CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
MODELS = {"tool-a": {"capabilities": ["completion", "tools"], "model_info": {"llama.context_length": 8192}}}


# ------------------------------------------------------------------------------- verificador minimo de JSON Schema
def check(doc, schema, path="$"):
    """Subconjunto de JSON Schema usado em docs/contracts (type, const, enum, required, properties,
    additionalProperties false, items, min/maxItems, uniqueItems, pattern, minimum, maximum, oneOf). Levanta no erro."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool,
             "null": type(None)}
    if "oneOf" in schema:
        ok = 0
        for sub in schema["oneOf"]:
            try:
                check(doc, sub, path)
                ok += 1
            except AssertionError:
                pass
        assert ok == 1, f"{path}: casa com {ok} alternativas de oneOf"
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        assert any(isinstance(doc, types[x]) and not (x in ("integer", "number") and isinstance(doc, bool))
                   for x in ts), f"{path}: tipo {type(doc).__name__} fora de {ts}"
    if "const" in schema:
        assert doc == schema["const"], f"{path}: {doc!r} != {schema['const']!r}"
    if "enum" in schema:
        assert doc in schema["enum"], f"{path}: {doc!r} fora de {schema['enum']}"
    if isinstance(doc, str) and "pattern" in schema:
        assert re.search(schema["pattern"], doc), f"{path}: {doc!r} nao casa {schema['pattern']}"
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        assert doc >= schema.get("minimum", doc) and doc <= schema.get("maximum", doc), f"{path}: fora do intervalo"
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            assert k in doc, f"{path}: falta {k}"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert not set(doc) - set(props), f"{path}: campos fora do contrato {set(doc) - set(props)}"
        for k, sub in props.items():
            if k in doc:
                check(doc[k], sub, f"{path}.{k}")
    if isinstance(doc, list):
        assert len(doc) >= schema.get("minItems", 0) and len(doc) <= schema.get("maxItems", len(doc)), f"{path}: tamanho"
        if schema.get("uniqueItems"):
            assert len({json.dumps(x, sort_keys=True) for x in doc}) == len(doc), f"{path}: itens repetidos"
        for i, x in enumerate(doc):
            if "items" in schema:
                check(x, schema["items"], f"{path}[{i}]")


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
