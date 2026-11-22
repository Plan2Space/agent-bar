"""F5: fluxos de 5 etapas — grupos paralelos, Parar, auto_approve, persistencia e prompt com SPEC + SHA."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from agent_bar.cockpit import workflows as W
from agent_bar.cockpit.acp import AsyncRuntime

STUB = Path(__file__).with_name("fake_acp_agent.py")
T = 30


@pytest.fixture
def rt():
    r = AsyncRuntime("test-wf")
    yield r
    r.stop()


def make_wf(tmp_path, instructions: dict[str, str], parallel: tuple[str, ...] = (), auto=False) -> W.Workflow:
    wf = W.Workflow.new("Teste", workspace=str(tmp_path))
    wf.spec_path, wf.spec_sha, wf.auto_approve = "agent-bar/X.md", "c" * 40, auto
    keep = [s for s in wf.stages if s.name in instructions]
    for s in keep:
        s.instructions = instructions[s.name]
        s.parallel_with_next = s.name in parallel
    wf.stages = keep
    return wf


def run(wf, rt, **kw):
    events: list[dict] = []
    lock = threading.Lock()

    def on_event(e):
        with lock:
            events.append(e)

    runner = W.WorkflowRunner(wf, runtime=rt, argv_for=lambda s: [sys.executable, str(STUB)], spec_text="# SPEC X\nR1 MUST",
                              on_event=on_event, **kw)
    return runner, events


def intervals(events, n):
    out = {}
    for i in range(n):
        start = next(e["t"] for e in events if e.get("type") == "stage" and e["stage"] == i and e["status"] == "running")
        end = next(e["t"] for e in events if e.get("type") == "stage" and e["stage"] == i and e["status"] != "running")
        out[i] = (start, end)
    return out


def test_default_flow_has_five_stages_and_pr_never_pushes():
    wf = W.Workflow.new("x")
    assert [s.name for s in wf.stages] == ["spec", "plan", "implement", "review", "pr"]
    pr = wf.stages[-1].instructions
    assert "NÃO faça push" in pr and "NÃO abra PR" in pr
    assert wf.auto_approve is False


def test_stage_groups_merge_consecutive_parallel():
    st = [W.Stage(n, parallel_with_next=p) for n, p in (("a", True), ("b", False), ("c", True), ("d", True), ("e", False))]
    assert W.stage_groups(st) == [[0, 1], [2, 3, 4]]
    assert W.stage_groups([W.Stage("a", parallel_with_next=True)]) == [[0]]


def test_prompt_has_spec_path_sha_instructions_previous_and_truncation():
    wf = W.Workflow.new("Fluxo", workspace="E:/ws")
    wf.spec_path, wf.spec_sha = "agent-bar/AGENT-BAR-COCKPIT-ACP.md", "d" * 40
    prev = W.Stage("spec", summary="lacuna em R7")
    text = W.stage_prompt(wf, wf.stages[1], "x" * 70_000, "CTX-BIBLIOTECA", [prev])
    assert "engineering-specs/agent-bar/AGENT-BAR-COCKPIT-ACP.md @ " + "d" * 40 in text
    assert "SPEC truncado: 10000 caracteres omitidos" in text
    assert wf.stages[1].instructions in text and "- spec: lacuna em R7" in text and "CTX-BIBLIOTECA" in text


def test_parallel_stages_overlap_and_sequential_do_not(tmp_path, rt):
    wf = make_wf(tmp_path, {"spec": "STUB-SLEEP", "plan": "STUB-SLEEP", "implement": "STUB-SLEEP"}, parallel=("spec",))
    runner, events = run(wf, rt)
    assert runner.start().result(T) == "done"
    iv = intervals(events, 3)
    assert iv[0][0] < iv[1][1] and iv[1][0] < iv[0][1]                    # spec || plan
    assert iv[2][0] >= max(iv[0][1], iv[1][1])                            # implement depois do grupo
    assert all(s.status == "done" and s.summary == "slept" for s in wf.stages)


def test_stop_cancels_all_running_stages_and_later_groups(tmp_path, rt):
    wf = make_wf(tmp_path, {"spec": "STUB-SLOW", "plan": "STUB-SLOW", "implement": "STUB-SLEEP"}, parallel=("spec",))
    runner, events = run(wf, rt)
    fut = runner.start()
    import time
    end = time.monotonic() + T
    while len(runner.clients) < 2 and time.monotonic() < end:
        time.sleep(0.05)
    time.sleep(0.3)
    runner.stop().result(T)
    assert fut.result(T) == "cancelled"
    assert [s.status for s in wf.stages] == ["cancelled", "cancelled", "cancelled"]
    assert runner.clients == {}


def test_error_in_a_stage_stops_later_groups(tmp_path, rt):
    wf = make_wf(tmp_path, {"spec": "STUB-FAIL", "plan": "STUB-SLEEP"})
    runner, _ = run(wf, rt)
    assert runner.start().result(T) == "error"
    assert wf.stages[0].status == "error" and "ACP_DOWN" in wf.stages[0].summary
    assert wf.stages[1].status == "pending"


def test_without_auto_approve_permission_goes_to_callback(tmp_path, rt):
    asked = []
    wf = make_wf(tmp_path, {"spec": "STUB-PERM"})
    runner, _ = run(wf, rt, on_permission=lambda i, req, respond: (asked.append((i, req.option_ids())), respond(None)))
    assert runner.start().result(T) == "done"
    assert asked == [(0, ["allow", "always", "reject"])]
    assert wf.stages[0].summary == "outcome=cancelled"


def test_auto_approve_picks_allow_once_only_and_logs(tmp_path, rt):
    wf = make_wf(tmp_path, {"spec": "STUB-PERM"}, auto=True)
    runner, _ = run(wf, rt, on_permission=lambda *a: pytest.fail("auto_approve nao deve perguntar"))
    assert runner.start().result(T) == "done"
    assert wf.stages[0].summary == "outcome=selected:allow"                 # 'allow' e o allow_once; nunca 'always'
    assert any(line.startswith("permissão automática (allow_once)") for line in wf.stages[0].log)
    assert "[failed] Run rm" not in wf.stages[0].log and "[completed] Run rm" in wf.stages[0].log   # update sem titulo usa o do tool_call


def test_store_round_trip_is_atomic(tmp_path):
    store = W.WorkflowStore(tmp_path, clock=lambda: 5.0)
    wf = W.Workflow.new("persistido", workspace="E:/ws")
    wf.stages[0].status, wf.stages[0].summary = "done", "ok"
    wf.stages[0].add_log("linha")
    store.save(wf)
    other = W.Workflow.new("outro")
    store.save(other)
    got = {w.id: w for w in W.WorkflowStore(tmp_path).load_all()}
    assert got[wf.id].stages[0].summary == "ok" and got[wf.id].stages[0].log == ["linha"]
    assert set(got) == {wf.id, other.id}
    assert [p.name for p in tmp_path.iterdir()] == ["workflows.json"]


def test_log_keeps_last_200_lines():
    st = W.Stage("x")
    for i in range(250):
        st.add_log(str(i))
    assert len(st.log) == 200 and st.log[0] == "50"


def test_spec_files_and_load_spec_refuse_escape(tmp_path):
    (tmp_path / "agent-bar").mkdir()
    (tmp_path / "agent-bar" / "A.md").write_text("# A", encoding="utf-8")
    (tmp_path / "README.md").write_text("idx", encoding="utf-8")
    assert W.spec_files(tmp_path) == ["agent-bar/A.md"]
    text, sha = W.load_spec("agent-bar/A.md", tmp_path)
    assert text == "# A" and sha == ""
    with pytest.raises(ValueError):
        W.load_spec("../fora.md", tmp_path)


# --------------------------------------------------------------------------------------------------------- GUI


def _app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    return qtw.QApplication.instance() or qtw.QApplication([])


def _wait(app, cond, timeout=T):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _tab(tmp_path, rt, ask=lambda req, parent: None):
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.workflows_tab import WorkflowsTab
    specs = tmp_path / "specs"
    (specs / "agent-bar").mkdir(parents=True)
    (specs / "agent-bar" / "X.md").write_text("# SPEC X\nR1 MUST", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    return WorkflowsTab(rt, lambda: str(ws), context_fn=lambda: "CTX", store=W.WorkflowStore(tmp_path / "data"),
                        profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                        specs_root=specs, ask_permission=ask)


def _dispose(tab):
    from PySide6.QtCore import QCoreApplication, QEvent
    tab.shutdown()
    tab.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tab_runs_parallel_group_persists_and_reloads(tmp_path, rt):
    app = _app()
    tab = _tab(tmp_path, rt)
    try:
        tab.wf.stages = tab.wf.stages[:2]
        for st in tab.wf.stages:
            st.instructions = "STUB-SLEEP"
        tab.wf.stages[0].parallel_with_next = True
        tab._render()
        assert tab.spec_box.currentData() == "agent-bar/X.md"
        tab.run()
        assert _wait(app, lambda: tab.runner is None and tab.run_btn.isEnabled())
        assert [s.status for s in tab.wf.stages] == ["done", "done"]
        assert "nada foi commitado" in tab.status.text()
        saved = {w.id: w for w in W.WorkflowStore(tmp_path / "data").load_all()}[tab.wf.id]
        assert [s.status for s in saved.stages] == ["done", "done"] and saved.spec_path == "agent-bar/X.md"
        assert saved.stages[0].parallel_with_next is True
        tab.new_flow()
        tab._flow_activated(tab.flow_box.findData(saved.id))
        assert tab.wf.id == saved.id and tab.tree.topLevelItem(0).text(2) == "concluída"
    finally:
        _dispose(tab)


def test_tab_permission_goes_to_dialog_when_auto_is_off(tmp_path, rt):
    app = _app()
    asked = []
    tab = _tab(tmp_path, rt, ask=lambda req, parent: asked.append(req) or "allow")
    try:
        tab.wf.stages = tab.wf.stages[:1]
        tab.wf.stages[0].instructions = "STUB-PERM"
        tab._render()
        assert tab.auto.isChecked() is False
        tab.run()
        assert _wait(app, lambda: tab.runner is None and tab.run_btn.isEnabled())
        assert len(asked) == 1 and tab.wf.stages[0].summary == "outcome=selected:allow"
        assert "permissão para" in tab.log.toPlainText()
    finally:
        _dispose(tab)


def test_tab_stop_cancels_everything(tmp_path, rt):
    app = _app()
    tab = _tab(tmp_path, rt)
    try:
        tab.wf.stages = tab.wf.stages[:3]
        for st in tab.wf.stages:
            st.instructions = "STUB-SLOW"
        tab.wf.stages[0].parallel_with_next = True
        tab._render()
        tab.run()
        assert _wait(app, lambda: tab.runner is not None and len(tab.runner.clients) == 2)
        tab.stop().result(T)
        assert _wait(app, lambda: tab.runner is None)
        assert [s.status for s in tab.wf.stages] == ["cancelled"] * 3
    finally:
        _dispose(tab)
