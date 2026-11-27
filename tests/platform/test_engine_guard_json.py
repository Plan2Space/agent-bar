"""Motor com guardas na borda (sketchup-mcp-next#66/#72, `--via tool-cli`): o build passa `--via tool-cli`, e uma
recusa ANTES de qualquer I/O (sem run_manifest, JSON {status: BLOCKED, error: {code}} no stdout) e registrada com o
codigo exato do motor, nao como NO_MANIFEST. O stdout do motor vai para um arquivo proprio; o stderr, para o log."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_bar.platform.render_sample import RENDER_TOOL
from agent_bar.platform.sketchup_sample import TOOL_NAME, EngineExit, run_engine, stdout_path
from tests.platform.test_render_sample import CAMS, Perm, built, call, ctx, engine_root, tools  # noqa: F401
from tests.platform.test_render_sample import FakeEngine as RenderFakeEngine


class GuardEngine(RenderFakeEngine):
    """Motor que recusa na borda o passo pedido (`block` = "build" | "render"), sem criar run_dir."""

    def __init__(self, block: str, code: str):
        super().__init__()
        self.block, self.code = block, code

    def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
        step = "build" if argv[2] == "p2s_engine.run" else "render"
        if step != self.block:
            return super().__call__(argv, cwd, env, timeout_s, log_path, on_start)
        self.calls.append(list(argv))
        stdout_path(log_path).parent.mkdir(parents=True, exist_ok=True)
        stdout_path(log_path).write_text(json.dumps({
            "status": "BLOCKED", "run_id": argv[argv.index("--run-id") + 1], "run_dir": None, "manifest_path": None,
            "verdict": None, "error": {"code": self.code, "message": f"{self.code}: recusado na borda do motor"}}),
            encoding="utf-8")
        return EngineExit(1, 4242)


def test_the_build_asks_the_engine_for_its_edge_guards(engine_root, tmp_path):
    engine = RenderFakeEngine()
    build, _ = tools(engine_root, tmp_path, engine)
    built(build)
    argv = engine.calls[-1]
    assert argv[2] == "p2s_engine.run" and argv[argv.index("--via") + 1] == "tool-cli"


@pytest.mark.parametrize("code", ["PATH_NOT_ALLOWED", "SKETCHUP_BUSY", "BUSY_UNKNOWN"])
def test_a_build_refused_at_the_engine_edge_keeps_the_engine_code(engine_root, tmp_path, code):
    build, _ = tools(engine_root, tmp_path, GuardEngine("build", code))
    res = build.run(call(TOOL_NAME, {"fixture_ref": "room_001"}), ctx(Perm(), turn_id="b1"))
    assert res["status"] == "failed" and res["error"]["code"] == code and res["error"]["engine_status"] == "BLOCKED"
    assert res["provenance"]["roles"]["builder"] is None                    # nada foi construido


@pytest.mark.parametrize("code", ["PATH_NOT_ALLOWED", "VRAY_BUSY"])
def test_a_render_refused_at_the_engine_edge_keeps_the_engine_code(engine_root, tmp_path, code):
    build, render = tools(engine_root, tmp_path, GuardEngine("render", code))
    b = built(build)
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["status"] == "failed" and res["error"]["code"] == code
    assert res["provenance"]["roles"]["renderer"] is None


def test_without_manifest_nor_json_it_is_still_no_manifest(engine_root, tmp_path):
    class Silent(RenderFakeEngine):
        def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
            self.calls.append(list(argv))
            return EngineExit(1, 4242)                                       # morreu sem dizer nada
    build, _ = tools(engine_root, tmp_path, Silent())
    res = build.run(call(TOOL_NAME, {"fixture_ref": "room_001"}), ctx(Perm(), turn_id="b1"))
    assert res["error"]["code"] == "NO_MANIFEST"


def test_run_engine_keeps_stdout_apart_from_the_log(tmp_path):
    log = tmp_path / "_studio" / "rq-x.engine.log"
    code = "import json,sys; print(json.dumps({'status': 'OK'})); print('ruido', file=sys.stderr)"
    exit_ = run_engine([sys.executable, "-c", code], tmp_path, None, 60, log)
    assert exit_.returncode == 0
    assert json.loads(stdout_path(log).read_text(encoding="utf-8")) == {"status": "OK"}
    assert "ruido" in log.read_text(encoding="utf-8") and "status" not in log.read_text(encoding="utf-8")
    assert stdout_path(log).name == "rq-x.engine.stdout.json"
    assert subprocess is not None and Path(log).parent == stdout_path(log).parent


class BusyWithManifest(RenderFakeEngine):
    """#72 @4f1a799: SketchUp/V-Ray ocupado vem DEPOIS da validacao, com run_dir e run_manifest
    (status SKETCHUP_UNAVAILABLE, error.code exato); o SketchUp nao abre."""

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def __call__(self, argv, cwd, env, timeout_s, log_path, on_start=None):
        if argv[2] != "p2s_engine.run":
            return super().__call__(argv, cwd, env, timeout_s, log_path, on_start)
        self.calls.append(list(argv))
        run_dir = Path(argv[argv.index("--out-dir") + 1]) / argv[argv.index("--run-id") + 1]
        run_dir.mkdir(parents=True)
        err = {"code": self.code, "message": f"{self.code}: SketchUp.exe ja aberto (PID 1)"}
        (run_dir / "run_manifest.json").write_text(json.dumps({
            "run_id": run_dir.name, "status": "SKETCHUP_UNAVAILABLE", "finished_at": "x", "verdict": None,
            "engine": {"commit": "e" * 40}, "error": err,
            "steps": [{"name": "validate_fixture", "status": "OK"}, {"name": "solve_layout", "status": "OK"}]}),
            encoding="utf-8")
        stdout_path(log_path).write_text(json.dumps({"status": "SKETCHUP_UNAVAILABLE", "error": err}), encoding="utf-8")
        return EngineExit(1, 4242)


@pytest.mark.parametrize("code", ["SKETCHUP_BUSY", "VRAY_BUSY", "BUSY_UNKNOWN"])
def test_a_busy_engine_with_manifest_keeps_the_exact_code(engine_root, tmp_path, code):
    build, _ = tools(engine_root, tmp_path, BusyWithManifest(code))
    res = build.run(call(TOOL_NAME, {"fixture_ref": "room_001"}), ctx(Perm(), turn_id="b1"))
    assert res["status"] == "failed" and res["error"]["code"] == code
    assert res["error"]["engine_status"] == "SKETCHUP_UNAVAILABLE"
    roles = res["provenance"]["roles"]
    assert roles["builder"] is None and roles["evaluator"] is None        # o SketchUp nao abriu
