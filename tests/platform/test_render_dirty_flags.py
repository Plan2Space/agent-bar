"""LAB-16, lacuna do auditor (2026-11-27): o registro do build grava `engine_dirty`/`studio_dirty`, o do render nao
gravava. Um render feito com motor ou Studio com mudancas locais precisa dizer isso no resultado, no registro SQLite e
no `_studio/<request_id>.json`; desconhecido fica `None`, nunca `False` inventado."""
from __future__ import annotations

import json

import pytest

from agent_bar.platform.render_sample import RENDER_TOOL, RenderSampleTool
from agent_bar.platform.sketchup_sample import SketchUpSampleTool
from tests.platform.test_render_sample import CAMS, FakeEngine, Perm, built, call, ctx, engine_root  # noqa: F401


def _tools(engine_root, tmp_path, head, studio):
    build = SketchUpSampleTool(engine_root, engine_root / "runs" / "local-executor", allowed_roots=(tmp_path / "apps",),
                               runner=FakeEngine(), sketchup_busy=lambda: False, head=lambda _r: head,
                               alive=lambda p, c: True, studio_head=lambda: studio,
                               python=engine_root / ".venv" / "python.exe")
    return build, RenderSampleTool(build, slot=lambda: "slot-1")


@pytest.mark.parametrize("engine_dirty, studio_dirty", [(True, False), (False, True), (False, False)])
def test_the_render_record_says_whether_engine_and_studio_were_dirty(engine_root, tmp_path, engine_dirty, studio_dirty):
    build, render = _tools(engine_root, tmp_path, {"commit": "e" * 40, "dirty": engine_dirty},
                           {"commit": "s" * 40, "dirty": studio_dirty})
    b = built(build)
    assert (b["engine_dirty"], b["studio_dirty"]) == (engine_dirty, studio_dirty)   # o build ja gravava
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["status"] == "completed"
    assert (res["engine_dirty"], res["studio_dirty"]) == (engine_dirty, studio_dirty)
    stored = json.loads(build.registry.get(res["request_id"])["result"])
    assert (stored["engine_dirty"], stored["studio_dirty"]) == (engine_dirty, studio_dirty)
    on_disk = json.loads((build.out_root / "_studio" / f"{res['request_id']}.json").read_text(encoding="utf-8"))
    assert (on_disk["engine_dirty"], on_disk["studio_dirty"]) == (engine_dirty, studio_dirty)


def test_unknown_checkouts_are_recorded_as_unknown_not_clean(engine_root, tmp_path):
    build, render = _tools(engine_root, tmp_path, {"commit": "e" * 40, "dirty": False}, {"commit": "s" * 40})
    b = built(build)
    build.head = lambda _r: None                                             # motor fora de um checkout git na hora
    build.studio_head = lambda: None
    res = render.run(call(RENDER_TOOL, {"run_id": b["run_id"], "cameras": CAMS[:1]}), ctx(Perm(), "r1"))
    assert res["status"] == "completed"
    assert "engine_dirty" in res and res["engine_dirty"] is None
    assert "studio_dirty" in res and res["studio_dirty"] is None
