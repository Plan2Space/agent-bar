"""Executor `build_room_sample` (P2S-LOCAL-EXECUTOR-SKETCHUP-POC §5, §7) com um motor FALSO so aqui (o motor real e
provado na demonstracao com SketchUp 2026). Cobra: permissao antes de efeito com os argumentos exatos; negar/fechar/
vencer = zero motor; allowlist; um pedido = no maximo um run; prazo vencido = desconhecido reconciliado, nunca 2o run;
motor falho/SKP ausente/hash divergente/inspecao falha nunca viram "completed"; fixture alterada e SketchUp ocupado
bloqueiam; ambiente filtrado; sessoes nao se cruzam."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from agent_bar.platform.local_tools import ToolContext, call_digest, normalize_tool_calls
from agent_bar.platform.sketchup_sample import (
    ALLOWED_FIXTURES,
    TOOL_NAME,
    EngineExit,
    SampleConfigError,
    SketchUpSampleTool,
    filtered_env,
)

FIXTURE = {"schema_version": "1.0.0", "room_id": "room_001", "units": "m"}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FakeEngine:
    """Faz o papel do `python -m p2s_engine.run`: grava o run_dir como o motor real (manifest, skp, validacao)."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
        self.calls.append((list(argv), dict(env)))
        if self.mode == "launch_error":
            raise OSError("python do motor nao abriu")
        if on_start is not None:
            on_start(4242, 1234.5)                                            # identidade do processo do motor
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("engine log\n", encoding="utf-8")
        if self.mode == "timeout":
            return EngineExit(None, 4242)
        out, run_id, fixture = Path(argv[argv.index("--out-dir") + 1]), argv[argv.index("--run-id") + 1], Path(argv[3])
        self.write_run(out / run_id, fixture)
        return EngineExit(0 if self.mode == "ok" else 1, 4242)

    def write_run(self, run_dir: Path, fixture: Path):
        run_dir.mkdir(parents=True)
        skp = run_dir / "room_001.skp"
        data = b"" if self.mode == "empty_skp" else b"SKP-BYTES"
        skp_rel = "../outside.skp" if self.mode == "skp_outside" else "room_001.skp"
        if self.mode == "skp_outside":
            (run_dir.parent / "outside.skp").write_bytes(data)
        if self.mode != "no_skp":
            skp.write_bytes(data)
        verdict = "FAIL" if self.mode == "inspection_fail" else "PASS"
        man = {"run_id": run_dir.name, "status": "FAILED" if self.mode == "engine_failed" else "OK",
               "finished_at": "2026-11-24T00:00:00Z", "verdict": verdict,
               "engine": {"commit": "e" * 40}, "fixture": {"sha256": "f" * 64 if self.mode == "other_fixture" else sha(fixture.read_bytes())},
               "sketchup": {"version": "26.0.429", "build_pid": 1, "reopen_pid": 2},
               "artifacts": [{"role": "skp", "path": skp_rel,
                              "sha256": "0" * 64 if self.mode == "hash_mismatch" else sha(data), "bytes": len(data)}],
               "error": {"code": "SU"} if self.mode == "engine_failed" else None,
               "steps": ([{"name": "validate_fixture", "status": "OK"}, {"name": "build_and_save", "status": "FAILED"}]
                         if self.mode == "engine_failed" else
                         [{"name": n, "status": "OK"} for n in
                          ("validate_fixture", "build_and_save", "reopen_and_inspect", "validate")])}
        (run_dir / "run_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        if self.mode == "engine_failed":
            return                                                           # falhou antes da inspecao: sem relatorio
        (run_dir / "validation_report.json").write_text(json.dumps(
            {"verdict": verdict, "failed": [], "checks": [{"id": "reopened", "pass": self.mode != "not_reopened"}]}), encoding="utf-8")


@pytest.fixture
def engine_root(tmp_path):
    root = tmp_path / "apps" / "sketchup-mcp-next"
    (root / "src" / "p2s_engine").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='p2s-engine'\n", encoding="utf-8")
    py = root / ".venv" / "python.exe"
    py.parent.mkdir()
    py.write_bytes(b"")
    for rel in ALLOWED_FIXTURES.values():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(json.dumps(FIXTURE), encoding="utf-8")
    return root


class Perm:
    """Canal de permissao como o do Chat: registra o pedido e responde com `choice` (ou via `on_ask`)."""

    def __init__(self, choice="allow_once", on_ask=None):
        self.choice, self.on_ask = choice, on_ask
        self.requests = []

    def __call__(self, req, respond):
        self.requests.append(req)
        if self.on_ask is not None:
            self.on_ask(req)
        if self.choice != "never":
            respond(self.choice)


def make(engine_root, tmp_path, engine=None, busy=False, alive=True, clock_s=1_790_000_000.0, **kw):
    tool = SketchUpSampleTool(engine_root, engine_root / "runs" / "local-executor", allowed_roots=(tmp_path / "apps",),
                              runner=engine or FakeEngine(), sketchup_busy=lambda: busy, head=lambda _r: None,
                              alive=lambda pid, created: alive,
                              clock=lambda: clock_s, python=engine_root / ".venv" / "python.exe",
                              base_env={"PATH": "x", "GH_TOKEN": "segredo", "ANTHROPIC_API_KEY": "k", "TEMP": "t"}, **kw)
    return tool


def ctx(perm, session="ollama-1", turn=1, events=None, turn_id="turn-0001"):
    ev = events if events is not None else []
    return ToolContext(session_id=session, turn=turn, turn_id=turn_id, model_id="llama3.1:8b",
                       model_digest="46e0c10c" + "0" * 56,
                       provider_version="0.40.17", workspace=None, on_permission=perm, cancel=threading.Event(),
                       emit=lambda kind, data: ev.append((kind, dict(data))))


def call(args=None):
    ok, refused = normalize_tool_calls([{"id": "call_x", "function": {"name": TOOL_NAME,
                                                                    "arguments": args or {"fixture_ref": "room_001"}}}],
                                       {TOOL_NAME}, 1)
    assert refused == []
    return ok[0]


def test_allow_once_runs_the_engine_once_and_reports_the_real_artifact(engine_root, tmp_path):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine)
    perm, events = Perm(), []
    res = tool.run(call(), ctx(perm, events=events))
    assert res["status"] == "completed" and res["error"] is None
    assert len(engine.calls) == 1
    argv, env = engine.calls[0]
    assert argv[1:3] == ["-m", "p2s_engine.run"] and argv[3] == str((engine_root / ALLOWED_FIXTURES["room_001"]).resolve())
    assert "GH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env and env["PATH"] == "x"
    req = perm.requests[0]
    raw = req.tool_call["rawInput"]
    fixture_hash = sha((engine_root / ALLOWED_FIXTURES["room_001"]).read_bytes())
    assert raw["fixture_ref"] == "room_001" and raw["expected_fixture_version"] == fixture_hash
    assert raw["request_id"] == res["request_id"] and raw["run_id"] == res["run_id"]
    assert raw["model"] == "llama3.1:8b" and raw["model_digest"].startswith("46e0c10c")
    assert raw["call_digest"] == call_digest(TOOL_NAME, {"fixture_ref": "room_001"})
    assert {o["kind"] for o in req.options} == {"allow_once", "reject_once"}                  # nunca allow_always
    assert res["skp_sha256"] == sha(b"SKP-BYTES") and res["skp_bytes"] == 9
    assert res["inspection_result"]["verdict"] == "PASS" and res["inspection_result"]["visual_verdict"] == "PENDING_HUMAN"
    assert res["run_id"].startswith("lx-") and res["run_id"].endswith("-room_001") and res["mode"] == "test-runner"
    assert [e[1]["status"] for e in events if e[0] == "tool_update"][-1] == "completed"
    prov = json.loads((engine_root / "runs" / "local-executor" / "_studio" / f"{res['request_id']}.json").read_text())
    assert prov["skp_sha256"] == res["skp_sha256"] and prov["model_digest"].startswith("46e0c10c")


@pytest.mark.parametrize("choice", ["reject_once", None, "never"])
def test_deny_close_or_expire_never_reaches_the_engine(engine_root, tmp_path, choice):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine, permission_timeout_s=0.2)
    res = tool.run(call(), ctx(Perm(choice)))
    assert res["status"] == "refused" and res["error"]["code"] == "PERMISSION_DENIED"
    assert engine.calls == [] and not list((engine_root / "runs" / "local-executor").glob("lx-*"))


def test_without_a_permission_channel_nothing_runs(engine_root, tmp_path):
    engine = FakeEngine()
    res = make(engine_root, tmp_path, engine).run(call(), ctx(None))
    assert res["error"]["code"] == "PERMISSION_DENIED" and engine.calls == []


@pytest.mark.parametrize("args, code", [
    ({"fixture_ref": "planta_74"}, "FIXTURE_NOT_ALLOWED"),
    ({"fixture_ref": "../../etc/passwd"}, "FIXTURE_NOT_ALLOWED"),
    ({"fixture_ref": "room_001", "out_dir": "C:/Windows"}, "ARGUMENT_NOT_ALLOWED"),
    ({"fixture_ref": "https://example.com/room.json"}, "FIXTURE_NOT_ALLOWED"),
    ({"fixture_ref": 1}, "FIXTURE_NOT_ALLOWED"),
])
def test_arguments_outside_the_contract_are_refused_before_asking(engine_root, tmp_path, args, code):
    engine, perm = FakeEngine(), Perm()
    res = make(engine_root, tmp_path, engine).run(call(args), ctx(perm))
    assert res["error"]["code"] == code and engine.calls == [] and perm.requests == []


def test_the_same_request_never_starts_a_second_run(engine_root, tmp_path):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine)
    first = tool.run(call(), ctx(Perm()))
    again = tool.run(call(), ctx(Perm()))
    assert len(engine.calls) == 1 and again["replayed"] and again["run_id"] == first["run_id"]
    assert again["status"] == "completed"


def test_timeout_is_unknown_until_the_run_dir_reconciles_and_never_reruns(engine_root, tmp_path):
    engine = FakeEngine("timeout")
    tool = make(engine_root, tmp_path, engine)
    first = tool.run(call(), ctx(Perm()))
    assert first["status"] == "unknown" and first["error"]["code"] == "ENGINE_TIMEOUT"
    again = tool.run(call(), ctx(Perm()))
    assert again["status"] == "unknown" and again["error"]["code"] == "RESULT_UNKNOWN" and len(engine.calls) == 1
    other = tool.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm()))           # um em voo por vez
    assert other["error"]["code"] == "BUSY" and len(engine.calls) == 1
    FakeEngine().write_run(engine_root / "runs" / "local-executor" / first["run_id"],
                           engine_root / ALLOWED_FIXTURES["room_001"])                 # o motor terminou depois
    settled = tool.run(call(), ctx(Perm()))
    assert settled["status"] == "completed" and len(engine.calls) == 1


@pytest.mark.parametrize("mode, code", [("engine_failed", "ENGINE_FAILED"), ("no_skp", "SKP_MISSING"),
                                        ("empty_skp", "SKP_MISSING"), ("hash_mismatch", "SKP_HASH_MISMATCH"),
                                        ("inspection_fail", "INSPECTION_FAILED")])
def test_engine_problems_are_failed_never_completed(engine_root, tmp_path, mode, code):
    events = []
    res = make(engine_root, tmp_path, FakeEngine(mode)).run(call(), ctx(Perm(), events=events))
    assert res["status"] == "failed" and res["error"]["code"] == code
    final = [e[1] for e in events if e[0] == "tool_update"][-1]
    assert final["status"] == "failed" and "pronto" not in json.dumps(final)


def test_a_fixture_changed_after_permission_does_not_run(engine_root, tmp_path):
    engine = FakeEngine()
    path = engine_root / ALLOWED_FIXTURES["room_001"]
    perm = Perm(on_ask=lambda _req: path.write_text(json.dumps({**FIXTURE, "units": "mm"}), encoding="utf-8"))
    res = make(engine_root, tmp_path, engine).run(call(), ctx(perm))
    assert res["error"]["code"] == "FIXTURE_CHANGED" and engine.calls == []


def test_another_sketchup_open_blocks_the_run(engine_root, tmp_path):
    engine = FakeEngine()
    res = make(engine_root, tmp_path, engine, busy=True).run(call(), ctx(Perm()))
    assert res["error"]["code"] == "SKETCHUP_BUSY" and engine.calls == []


def test_sessions_do_not_share_request_or_output(engine_root, tmp_path):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine)
    pa, pb = Perm(), Perm()
    a = tool.run(call(), ctx(pa, session="ollama-1"))
    b = tool.run(call(), ctx(pb, session="ollama-2"))
    assert a["request_id"] != b["request_id"] and a["run_id"] != b["run_id"] and len(engine.calls) == 2
    assert pa.requests[0].session_id == "ollama-1" and pb.requests[0].session_id == "ollama-2"


def test_config_outside_the_allowed_roots_or_into_artifacts_is_refused(engine_root, tmp_path):
    with pytest.raises(SampleConfigError):
        SketchUpSampleTool(engine_root, tmp_path / "elsewhere", allowed_roots=(tmp_path / "apps",))
    with pytest.raises(SampleConfigError):
        SketchUpSampleTool(engine_root, engine_root / "artifacts" / "x", allowed_roots=(tmp_path / "apps",))
    with pytest.raises(SampleConfigError):
        SketchUpSampleTool(tmp_path / "other-engine", engine_root / "runs", allowed_roots=(tmp_path / "apps",))


def test_preflight_explains_what_is_missing(engine_root, tmp_path):
    tool = make(engine_root, tmp_path)
    assert tool.preflight() == (True, "")
    (engine_root / ".venv" / "python.exe").unlink()
    ok, why = tool.preflight()
    assert not ok and "uv sync" in why


def test_filtered_env_drops_secrets_case_insensitively():
    env = filtered_env({"Path": "p", "github_token": "x", "SystemRoot": "C:/Windows", "OPENAI_API_KEY": "y"})
    assert env == {"Path": "p", "SystemRoot": "C:/Windows", "PYTHONUTF8": "1"}


def test_a_new_turn_after_a_studio_restart_is_a_new_request(engine_root, tmp_path):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine)
    a = tool.run(call(), ctx(Perm(), turn_id="studio-1-turn"))
    b = tool.run(call(), ctx(Perm(), turn_id="studio-2-turn"))                 # mesma sessao "ollama-1", turno 1
    assert a["request_id"] != b["request_id"] and len(engine.calls) == 2 and not b.get("replayed")


def test_an_engine_that_died_without_a_manifest_is_failed_and_frees_the_slot(engine_root, tmp_path):
    engine = FakeEngine("timeout")
    tool = make(engine_root, tmp_path, engine, alive=False)
    first = tool.run(call(), ctx(Perm()))
    assert first["status"] == "unknown" and first["engine_pid"] == 4242
    again = tool.run(call(), ctx(Perm()))
    assert again["status"] == "failed" and again["error"]["code"] == "ENGINE_GONE" and len(engine.calls) == 1
    engine.mode = "ok"
    other = tool.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm()))
    assert other["status"] == "completed" and len(engine.calls) == 2


def test_an_engine_that_cannot_start_is_failed_and_does_not_block(engine_root, tmp_path):
    engine = FakeEngine("launch_error")
    tool = make(engine_root, tmp_path, engine)
    res = tool.run(call(), ctx(Perm()))
    assert res["status"] == "failed" and res["error"]["code"] == "ENGINE_LAUNCH_FAILED"
    engine.mode = "ok"
    assert tool.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm()))["status"] == "completed"


def test_provenance_and_permission_carry_the_studio_commit(engine_root, tmp_path):
    perm = Perm()
    tool = make(engine_root, tmp_path, studio_head=lambda: {"commit": "s" * 40, "dirty": False})
    res = tool.run(call(), ctx(perm))
    assert res["studio_commit"] == "s" * 40 and perm.requests[0].tool_call["rawInput"]["studio_commit"] == "s" * 40
    assert res["mode"] == "test-runner"                                      # runner injetado nunca se diz "real"


def test_two_tool_instances_never_run_two_engines_at_once(engine_root, tmp_path):
    gate, entered = threading.Event(), threading.Event()

    class Blocking(FakeEngine):
        def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
            entered.set()
            gate.wait(10)
            return super().__call__(argv, cwd, env, timeout_s, log_path, on_start)
    first, second = Blocking(), FakeEngine()
    a, b = make(engine_root, tmp_path, first), make(engine_root, tmp_path, second)   # duas abas do Chat
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", a.run(call(), ctx(Perm(), turn_id="ta"))))
    t.start()
    assert entered.wait(10)
    res_b = b.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm(), session="ollama-2", turn_id="tb"))
    gate.set()
    t.join(10)
    assert res_b["error"]["code"] == "BUSY" and second.calls == [] and out["a"]["status"] == "completed"


def test_a_running_row_without_pid_is_in_flight_while_recent(engine_root, tmp_path):
    engine = FakeEngine()
    tool = make(engine_root, tmp_path, engine)
    tool.registry.insert({"request_id": "rq-other", "session_id": "x", "turn": 1, "call_digest": "d",
                          "fixture_ref": "room_001", "fixture_hash": "h", "run_id": "lx-other", "status": "running",
                          "result": {"status": "running"}}, now=1_790_000_000.0 - 5)
    assert tool.run(call(), ctx(Perm()))["error"]["code"] == "BUSY" and engine.calls == []
    old = make(engine_root, tmp_path, engine, clock_s=1_790_000_000.0 + 3600)
    assert old.run(call(), ctx(Perm(), turn_id="later"))["status"] == "completed"   # velha e sem PID: falhou


def test_a_new_studio_instance_reconciles_the_previous_unknown_run(engine_root, tmp_path):
    first = make(engine_root, tmp_path, FakeEngine("timeout"))
    unknown = first.run(call(), ctx(Perm()))
    assert unknown["status"] == "unknown"
    engine = FakeEngine()
    second = make(engine_root, tmp_path, engine)                              # Studio reaberto, mesmo registro
    assert second.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm(), turn_id="n"))["error"]["code"] == "BUSY"
    FakeEngine().write_run(engine_root / "runs" / "local-executor" / unknown["run_id"],
                           engine_root / ALLOWED_FIXTURES["room_001"])
    assert second.run(call({"fixture_ref": "room_alt_5x3"}), ctx(Perm(), turn_id="m"))["status"] == "completed"
    assert second.registry.get(unknown["request_id"])["status"] == "completed"


@pytest.mark.parametrize("mode, code", [("not_reopened", "INSPECTION_FAILED"), ("other_fixture", "FIXTURE_HASH_MISMATCH"),
                                        ("skp_outside", "SKP_MISSING")])
def test_more_engine_lies_are_failed(engine_root, tmp_path, mode, code):
    res = make(engine_root, tmp_path, FakeEngine(mode)).run(call(), ctx(Perm()))
    assert res["status"] == "failed" and res["error"]["code"] == code


def test_unknown_is_shown_as_in_progress_not_failed(engine_root, tmp_path):
    events = []
    make(engine_root, tmp_path, FakeEngine("timeout")).run(call(), ctx(Perm(), events=events))
    assert [e[1]["status"] for e in events if e[0] == "tool_update"][-1] == "in_progress"


def test_cancel_while_waiting_for_permission_is_cancelled_not_denied(engine_root, tmp_path):
    engine = FakeEngine()
    c = ctx(Perm("never", on_ask=lambda _req: c.cancel.set()))
    res = make(engine_root, tmp_path, engine).run(call(), c)
    assert res["error"]["code"] == "CANCELLED" and engine.calls == []


def test_symlinked_engine_root_is_refused(engine_root, tmp_path):
    link = tmp_path / "apps" / "engine-link"
    try:
        link.symlink_to(engine_root, target_is_directory=True)
    except OSError:
        pytest.skip("sem privilegio para criar link simbolico nesta maquina")
    with pytest.raises(SampleConfigError):
        SketchUpSampleTool(link, engine_root / "runs", allowed_roots=(tmp_path / "apps",))


def test_engine_env_keeps_the_sketchup_override_but_no_secrets():
    env = filtered_env({"P2S_SKETCHUP_EXE": "D:/SU/SketchUp.exe", "GH_TOKEN": "x", "PATH": "p", "MY_API_KEY": "k"})
    assert env["P2S_SKETCHUP_EXE"] == "D:/SU/SketchUp.exe" and "GH_TOKEN" not in env and "MY_API_KEY" not in env


def test_inbox_operation_keeps_model_and_digest_within_its_limit(engine_root, tmp_path):
    from agent_bar.cockpit.permission_inbox import OP_LIMIT, describe
    perm = Perm()
    make(engine_root, tmp_path).run(call(), ctx(perm))
    op = describe(perm.requests[0].tool_call)[0]
    assert len(op) <= OP_LIMIT and '"model": "llama3.1:8b"' in op and '"model_digest": "46e0c10c' in op
    assert '"request_id": "rq-' in op


def test_studio_commit_outside_git_is_explicitly_unknown(engine_root, tmp_path):
    res = make(engine_root, tmp_path, studio_head=lambda: None).run(call(), ctx(Perm()))
    assert res["studio_commit"] == "desconhecido (fora de um checkout git)"


def test_provenance_separates_who_asked_who_planned_who_built_and_who_judged(engine_root, tmp_path):
    tool = make(engine_root, tmp_path, studio_head=lambda: {"commit": "s" * 40, "dirty": False})
    tool.head = lambda _r: {"commit": "e" * 40, "dirty": False}
    res = tool.run(call(), ctx(Perm()))
    p = res["provenance"]
    model = "llama3.1:8b@46e0c10c" + "0" * 56
    assert p["schema"] == "p2s.lab.provenance.v1" and p["provider"] == "ollama" and p["adapter"] == "ollama"
    assert p["model_id"] == "llama3.1:8b" and p["model_digest"].startswith("46e0c10c") and p["model_version"] is None
    roles = p["roles"]
    assert roles["tool_requester"] == {"kind": "model", "id": model}
    assert roles["planner"]["kind"] == "engine" and "so escolheu" in roles["planner"]["note"]   # nao e do modelo
    assert roles["builder"]["id"] == "sketchup-mcp-next@" + "e" * 40
    assert roles["evaluator"]["kind"] == "engine_harness" and roles["outcome_writer"]["id"] == "agent-bar@" + "s" * 40
    assert roles["renderer"] is None and roles["refiner"] is None and p["fallback_model"] is None
    assert p["experiment_id"] is None and p["parent_run_id"] is None       # nao medido = None, nunca inventado
    assert roles["builder"]["sketchup_version"] == "26.0.429"              # do manifest do motor, depois do run


def test_roles_are_only_what_the_engine_records_after_the_run(engine_root, tmp_path):
    """fmmodesto #122: nada de atribuir planejamento, construcao ou inspecao antes de acontecerem."""
    tool = make(engine_root, tmp_path, FakeEngine("timeout"))
    tool.head = lambda _r: {"commit": "e" * 40, "dirty": False}
    p = tool.run(call(), ctx(Perm()))["provenance"]
    assert p["roles"]["planner"] is None and p["roles"]["builder"] is None and p["roles"]["evaluator"] is None
    assert p["roles"]["tool_requester"]["kind"] == "model"                   # isso ocorreu de fato
    assert p["roles_expected"]["evaluator"]["kind"] == "engine_harness"      # previsto != efetivo


def test_an_engine_failure_before_inspection_does_not_credit_the_harness(engine_root, tmp_path):
    tool = make(engine_root, tmp_path, FakeEngine("engine_failed"))
    res = tool.run(call(), ctx(Perm()))
    roles = res["provenance"]["roles"]
    assert res["status"] == "failed"
    assert roles["planner"]["kind"] == "engine"                              # a fixture foi validada e planejada
    assert roles["builder"] is None and roles["evaluator"] is None          # build falhou; nada foi inspecionado


def test_refused_requests_record_no_engine_role(engine_root, tmp_path):
    res = make(engine_root, tmp_path).run(call(), ctx(Perm("reject_once")))
    assert res["status"] == "refused"
    roles = res.get("provenance", {}).get("roles", {})
    assert not any(roles.get(k) for k in ("planner", "builder", "evaluator", "renderer"))
