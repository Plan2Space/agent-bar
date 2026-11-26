"""LAB-16 (P2S-STUDIO-LLM-LAB-001 §12 E4, §17): render acionado pelo modelo local, com motor FALSO so aqui (a prova
real usa V-Ray com o slot concedido). Cobra: o modelo so nomeia um build `completed` do proprio executor e camaras da
spec (no maximo 2); caminho, spec e slot sao do Studio; sem slot = recusa antes de perguntar; permissao antes de
efeito; .skp re-conferido pelo sha256; `completed` so com manifest OK + PASS + cada PNG dentro do run com o sha256
registrado; papel `renderer` so pelo manifest; run do render liga ao build (parent_run_id)."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from agent_bar.platform.local_tools import ToolContext, normalize_tool_calls
from agent_bar.platform.render_sample import RENDER_TOOL, RenderSampleTool, ToolBox
from agent_bar.platform.sketchup_sample import TOOL_NAME, EngineExit, SketchUpSampleTool

E3A = "tests/fixtures/furnishing/plan_alt_5x3.e3a.furnishing.json"
SPEC = "tests/fixtures/render/plan_alt_5x3.sample.render.json"
CAMS = ["amostra_perspectiva", "amostra_lateral"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FakeEngine:
    """Faz o papel do motor: `p2s_engine.run` grava um build; `p2s_engine.render` grava um render."""

    def __init__(self, render_mode="ok"):
        self.render_mode = render_mode
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
        self.calls.append(list(argv))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("log\n", encoding="utf-8")
        if on_start:
            on_start(4242, 1.0)
        out, rid = Path(argv[argv.index("--out-dir") + 1]), argv[argv.index("--run-id") + 1]
        if argv[2] == "p2s_engine.run":
            self._build(out / rid, Path(argv[3]))
            return EngineExit(0, 4242)
        if self.render_mode == "timeout":
            return EngineExit(None, 4242)
        self._render(out / rid, argv)
        return EngineExit(0 if self.render_mode == "ok" else 1, 4242)

    @staticmethod
    def _build(run_dir: Path, fixture: Path):
        run_dir.mkdir(parents=True)
        (run_dir / "plan_alt_5x3.skp").write_bytes(b"SKP")
        man = {"run_id": run_dir.name, "status": "OK", "finished_at": "x", "verdict": "PASS",
               "engine": {"commit": "e" * 40}, "fixture": {"sha256": sha(fixture.read_bytes())},
               "sketchup": {"version": "26.0.429"},
               "artifacts": [{"role": "skp", "path": "plan_alt_5x3.skp", "sha256": sha(b"SKP")}],
               "steps": [{"name": n, "status": "OK"} for n in
                         ("validate_fixture", "build_and_save", "reopen_and_inspect", "validate")]}
        (run_dir / "run_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (run_dir / "validation_report.json").write_text(json.dumps(
            {"verdict": "PASS", "checks": [{"id": "reopened", "pass": True}]}), encoding="utf-8")

    def _render(self, run_dir: Path, argv: list[str]):
        run_dir.mkdir(parents=True)
        (run_dir / "renders").mkdir()
        cams = argv[argv.index("--cameras") + 1: argv.index("--slot")]
        outputs = []
        for c in cams:
            data = b"" if self.render_mode == "empty_png" else b"PNG-" + c.encode()
            png = run_dir / "renders" / f"dia_{c}.png"
            png.write_bytes(data)
            outputs.append({"key": f"dia_{c}", "png": f"renders/{png.name}",
                            "png_sha256": "0" * 64 if self.render_mode == "png_hash" else sha(data)})
        man = {"run_id": run_dir.name, "status": "OK" if self.render_mode != "blocked" else "BLOCKED",
               "finished_at": "x", "verdict": "FAIL" if self.render_mode == "fail" else
               (None if self.render_mode == "blocked" else "PASS"),
               "engine": {"commit": "e" * 40},
               "input_skp": {"sha256": sha(b"SKP")}, "outputs": outputs,
               "request": {"cameras": cams, "slot": argv[argv.index("--slot") + 1]},
               "error": "VRAY_BUSY: vray.exe" if self.render_mode == "blocked" else None}
        (run_dir / "render_manifest.json").write_text(json.dumps(man), encoding="utf-8")


@pytest.fixture
def engine_root(tmp_path):
    root = tmp_path / "apps" / "sketchup-mcp-next"
    (root / "src" / "p2s_engine").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "python.exe").write_bytes(b"")
    for rel, body in (("tests/fixtures/rooms/room_001.json", {"room_id": "room_001"}),
                      ("tests/fixtures/rooms/room_alt_5x3.json", {"room_id": "room_alt_5x3"}),
                      (E3A, {"furnishing_id": "plan_alt_5x3_e3a"}),
                      (SPEC, {"render_id": "x", "cameras": [{"name": c} for c in CAMS]})):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(json.dumps(body), encoding="utf-8")
    return root


class Perm:
    def __init__(self, choice="allow_once"):
        self.choice, self.requests = choice, []

    def __call__(self, req, respond):
        self.requests.append(req)
        respond(self.choice)


def ctx(perm, turn_id="t1"):
    events = []
    c = ToolContext(session_id="ollama-1", turn=1, turn_id=turn_id, model_id="llama3.1:8b",
                    model_digest="46e0c10c" + "0" * 56, provider_version="0.40.17", workspace=None,
                    on_permission=perm, cancel=threading.Event(), emit=lambda k, d: events.append((k, dict(d))))
    c.events = events
    return c


def call(name, args, cid="c1"):
    ok, refused = normalize_tool_calls([{"id": cid, "function": {"name": name, "arguments": args}}], {name}, 1)
    assert refused == []
    return ok[0]


def tools(engine_root, tmp_path, engine, slot="slot-felipe-1", busy=False):
    build = SketchUpSampleTool(engine_root, engine_root / "runs" / "local-executor", allowed_roots=(tmp_path / "apps",),
                               runner=engine, sketchup_busy=lambda: busy, head=lambda _r: {"commit": "e" * 40},
                               alive=lambda p, c: True, studio_head=lambda: {"commit": "s" * 40},
                               python=engine_root / ".venv" / "python.exe")
    render = RenderSampleTool(build, slot=lambda: slot)
    return build, render


def built(build, turn="b1"):
    res = build.run(call(TOOL_NAME, {"fixture_ref": "plan_alt_5x3_e3a"}), ctx(Perm(), turn_id=turn))
    assert res["status"] == "completed", res
    return res


def test_render_of_an_executor_build_runs_once_and_reports_the_real_images(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    perm = Perm()
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS}), ctx(perm, turn_id="r1"))
    assert res["status"] == "completed" and res["error"] is None
    argv = engine.calls[-1]
    assert argv[1:3] == ["-m", "p2s_engine.render"]
    assert argv[3] == str((engine_root / SPEC).resolve()) and argv[4] == b["skp_abs"]
    assert argv[argv.index("--cameras") + 1: argv.index("--slot")] == CAMS
    assert argv[argv.index("--slot") + 1] == "slot-felipe-1"
    assert argv[argv.index("--parent-run-id") + 1] == b["run_id"]
    assert json.loads(argv[argv.index("--requested-by-json") + 1]) == {
        "provider": "ollama", "model_id": "llama3.1:8b", "model_digest": "46e0c10c" + "0" * 56}
    assert [Path(i["png_abs"]).name for i in res["images"]] == ["dia_amostra_perspectiva.png", "dia_amostra_lateral.png"]
    assert all(i["kind"] == "vray_render" and i["sha256"] for i in res["images"])
    raw = perm.requests[0].tool_call["rawInput"]
    assert raw["tool"] == RENDER_TOOL and raw["build_run_id"] == b["run_id"] and raw["cameras"] == CAMS
    p = res["provenance"]
    assert p["parent_run_id"] == b["run_id"] and p["roles"]["renderer"]["id"] == "sketchup-mcp-next@" + "e" * 40
    assert p["roles"]["builder"] is None                                     # este run nao construiu nada
    assert res["visual_verdict"] == "PENDING_HUMAN"


@pytest.mark.parametrize("args, code", [
    ({"run_id": "lx-nao-existe", "cameras": CAMS[:1]}, "RUN_NOT_RENDERABLE"),
    ({"run_id": "BUILD", "cameras": ["camera_inventada"]}, "CAMERA_NOT_ALLOWED"),
    ({"run_id": "BUILD", "cameras": CAMS + CAMS[:1]}, "CAMERA_NOT_ALLOWED"),
    ({"run_id": "BUILD", "cameras": []}, "CAMERA_NOT_ALLOWED"),
    ({"run_id": "BUILD", "cameras": CAMS, "skp": "C:/x.skp"}, "ARGUMENT_NOT_ALLOWED"),
    ({"run_id": "../../etc", "cameras": CAMS[:1]}, "RUN_NOT_RENDERABLE"),
])
def test_arguments_outside_the_contract_never_ask_and_never_render(engine_root, tmp_path, args, code):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    args = {k: (b["run_id"] if v == "BUILD" else v) for k, v in args.items()}
    perm = Perm()
    n = len(engine.calls)
    res = render.run(call(RENDER_TOOL, args), ctx(perm, turn_id="r1"))
    assert res["error"]["code"] == code and perm.requests == [] and len(engine.calls) == n


def test_a_room_build_without_a_render_spec_is_not_renderable(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    room = build.run(call(TOOL_NAME, {"fixture_ref": "room_001"}), ctx(Perm(), turn_id="b0"))
    n = len(engine.calls)
    res = render.run(call(RENDER_TOOL, {"run_id": room["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), turn_id="r0"))
    assert res["error"]["code"] == "RUN_NOT_RENDERABLE" and len(engine.calls) == n


def test_without_a_granted_slot_nothing_is_asked_nor_opened(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine, slot="")
    b = built(build)
    perm, n = Perm(), len(engine.calls)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(perm, turn_id="r1"))
    assert res["error"]["code"] == "SLOT_NOT_GRANTED" and perm.requests == [] and len(engine.calls) == n


def test_deny_renders_nothing(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    n = len(engine.calls)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm("reject_once"), "r1"))
    assert res["error"]["code"] == "PERMISSION_DENIED" and len(engine.calls) == n


def test_a_changed_skp_is_not_rendered(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    Path(b["skp_abs"]).write_bytes(b"OUTRO")
    n = len(engine.calls)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["error"]["code"] == "SKP_CHANGED" and len(engine.calls) == n


def test_another_sketchup_or_vray_open_blocks_the_render(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    build.sketchup_busy = lambda: True
    n = len(engine.calls)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["error"]["code"] == "SKETCHUP_BUSY" and len(engine.calls) == n


@pytest.mark.parametrize("mode, code", [("fail", "RENDER_FAILED"), ("blocked", "RENDER_BLOCKED"),
                                        ("empty_png", "IMAGE_MISSING"), ("png_hash", "IMAGE_HASH_MISMATCH")])
def test_render_problems_are_failed_never_completed(engine_root, tmp_path, mode, code):
    engine = FakeEngine(render_mode=mode)
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["status"] == "failed" and res["error"]["code"] == code
    assert res["provenance"]["roles"]["renderer"] is None or mode in ("fail", "empty_png", "png_hash")


def test_render_timeout_is_unknown_and_blocks_the_next_engine_run(engine_root, tmp_path):
    engine = FakeEngine(render_mode="timeout")
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["status"] == "unknown"
    n = len(engine.calls)
    again = build.run(call(TOOL_NAME, {"fixture_ref": "room_001"}), ctx(Perm(), turn_id="b2"))
    assert again["error"]["code"] == "BUSY" and len(engine.calls) == n     # um motor por vez, inclusive render


def test_toolbox_publishes_both_tools_and_dispatches_by_name(engine_root, tmp_path):
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    box = ToolBox([build, render])
    assert box.names() == {TOOL_NAME, RENDER_TOOL}
    assert {s["function"]["name"] for s in box.specs()} == {TOOL_NAME, RENDER_TOOL}
    enum = next(s for s in box.specs() if s["function"]["name"] == TOOL_NAME)["function"]["parameters"]
    assert "plan_alt_5x3_e3a" in enum["properties"]["fixture_ref"]["enum"]
    b = box.run(call(TOOL_NAME, {"fixture_ref": "plan_alt_5x3_e3a"}), ctx(Perm(), "b1"))
    r = box.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert b["tool"] == TOOL_NAME and r["tool"] == RENDER_TOOL
    assert "render" in box.final_text([r]).lower() and "SUCESSO" in box.final_text([b])


def test_optional_fixture_absent_from_the_engine_is_not_published(engine_root, tmp_path):
    (engine_root / E3A).unlink()
    build, render = tools(engine_root, tmp_path, FakeEngine())
    assert build.preflight() == (True, "")                                    # o nucleo continua pronto
    enum = build.specs()[0]["function"]["parameters"]["properties"]["fixture_ref"]["enum"]
    assert "plan_alt_5x3_e3a" not in enum
    assert render.preflight()[0] is False                                     # sem build renderizavel publicado


def test_the_render_card_does_not_say_the_build_text(engine_root, tmp_path):
    from agent_bar.platform.sketchup_sample import summary
    engine = FakeEngine()
    build, render = tools(engine_root, tmp_path, engine)
    b = built(build)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    text = summary(res)
    assert ".skp salvo" not in text and "render gravado e conferido" in text
    assert ".skp salvo, reaberto e inspecionado" in summary(b)                 # o do build continua
