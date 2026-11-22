"""F2: aba Chat do cockpit contra o agente falso (offscreen; skip sem PySide6)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.acp import AcpClient, PermissionRequest, Update  # noqa: E402
from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import Chat, ChatStore, atomic_write_json  # noqa: E402
from agent_bar.cockpit.ui import widgets as W  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402

STUB = Path(__file__).with_name("fake_acp_agent.py")


_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])     # vive ate o fim do processo, como nos outros testes de GUI


@pytest.fixture
def app():
    return _APP


def dispose(view) -> None:
    """Encerra o cliente e destroi o widget agora: widget vivo depois do QApplication derruba o processo na saida."""
    from PySide6.QtCore import QCoreApplication, QEvent
    view.shutdown()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait(app, cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class CountingFactory:
    def __init__(self):
        self.made: list[AcpClient] = []

    def __call__(self, *a, **kw):
        c = AcpClient(*a, **kw)
        self.made.append(c)
        return c


def make_view(app, tmp_path, *, ask=lambda req, parent: None, argv=None, spaces=None):
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    ws = spaces or [tmp_path / "ws1", tmp_path / "ws2"]
    for w in ws:
        w.mkdir(exist_ok=True)
    factory = CountingFactory()
    prof = AgentProfile("stub", "Stub", tuple(argv or (sys.executable, str(STUB))))
    view = ChatView(ChatStore(tmp_path / "data"), client_factory=factory, ask_permission=ask,
                    profiles_fn=lambda: [prof], workspaces_fn=lambda: ws)
    return view, factory


def turn(app, view, text):
    view.send(text)
    assert wait(app, lambda: not view.busy), "turno nao terminou"


def test_thousand_chunks_are_coalesced_and_handler_is_fast(app, tmp_path):
    view, _ = make_view(app, tmp_path)
    try:
        view.gen = 7
        worst = 0.0
        for i in range(1000):
            u = Update("agent_message_chunk", "s", {"content": {"type": "text", "text": f"{i},"}})
            t0 = time.perf_counter()
            view._on_update(7, u)
            worst = max(worst, time.perf_counter() - t0)
        assert wait(app, lambda: view.text_block is not None, 2)
        assert view.flush_count <= 25
        assert view.text_block.full_text() == "".join(f"{i}," for i in range(1000))
        assert worst < 0.010
    finally:
        dispose(view)


def test_stale_generation_updates_are_ignored(app, tmp_path):
    view, _ = make_view(app, tmp_path)
    try:
        view.gen = 3
        view._on_update(2, Update("agent_message_chunk", "s", {"content": {"type": "text", "text": "old"}}))
        view.flush()
        assert view.text_block is None
    finally:
        dispose(view)


def test_end_to_end_stream_and_history_saved(app, tmp_path):
    view, factory = make_view(app, tmp_path)
    try:
        turn(app, view, "echo 30")
        saved = ChatStore(tmp_path / "data").load(view.chat.id)
        assert saved.title == "echo 30"
        assert [m["role"] for m in saved.messages] == ["user", "agent"]
        assert saved.messages[1]["text"] == "".join(f"c{i} " for i in range(30))
        assert saved.session_id == "s-1" and saved.workspace.endswith("ws1")
        assert len(factory.made) == 1
    finally:
        dispose(view)


def test_tool_call_and_updates_make_one_card_and_plan_is_single(app, tmp_path):
    view, _ = make_view(app, tmp_path)
    try:
        turn(app, view, "tools")
        cards = view.transcript.findChildren(W.ToolCard)
        assert len(cards) == 1 and cards[0].state["status"] == "completed"
        assert len(view.transcript.findChildren(W.PlanCard)) == 1
        roles = [m["role"] for m in view.chat.messages]
        assert roles.count("tool") == 1 and "plan" in roles and "thought" in roles
    finally:
        dispose(view)


def test_plan_update_replaces_entries(app, tmp_path):
    view, _ = make_view(app, tmp_path)
    try:
        view.gen = 1
        view._on_update(1, Update("plan", "s", {"entries": [{"content": "a", "status": "pending"}]}))
        view._on_update(1, Update("plan", "s", {"entries": [{"content": "b", "status": "completed"}]}))
        plans = view.transcript.findChildren(W.PlanCard)
        assert len(plans) == 1 and plans[0].entries == [{"content": "b", "status": "completed"}]
    finally:
        dispose(view)


def test_dismissed_permission_answers_cancelled(app, tmp_path):
    asked: list[PermissionRequest] = []
    view, _ = make_view(app, tmp_path, ask=lambda req, parent: asked.append(req) or None)
    try:
        turn(app, view, "perm")
        assert asked and asked[0].tool_call["toolCallId"] == "t1"
        assert view.chat.messages[-1]["role"] in ("agent", "tool")
        agent = [m["text"] for m in view.chat.messages if m["role"] == "agent"]
        assert agent == ["outcome=cancelled"]
        card = view.transcript.findChildren(W.ToolCard)[0]
        assert card.state["status"] == "failed"
    finally:
        dispose(view)


def test_permission_selected_reaches_agent(app, tmp_path):
    view, _ = make_view(app, tmp_path, ask=lambda req, parent: "allow")
    try:
        turn(app, view, "perm")
        assert [m["text"] for m in view.chat.messages if m["role"] == "agent"] == ["outcome=selected:allow"]
    finally:
        dispose(view)


def test_permission_dialog_close_is_none_and_button_returns_option(app):
    req = PermissionRequest("s", {"title": "rm -rf build", "kind": "execute"},
                            ({"optionId": "a", "name": "Allow", "kind": "allow_once"},
                             {"optionId": "r", "name": "Reject", "kind": "reject_once"}))
    dlg = W.PermissionDialog(req)
    dlg.reject()
    assert dlg.choice is None
    dlg2 = W.PermissionDialog(req)
    dlg2.buttons["a"].click()
    assert dlg2.choice == "a"
    for d in (dlg, dlg2):                     # dialogo sem pai que sobrevive ao QApplication derruba o processo na saida
        d.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_workspace_change_restarts_client_and_keeps_transcript(app, tmp_path):
    view, factory = make_view(app, tmp_path)
    try:
        turn(app, view, "echo 2")
        first = factory.made[0]
        view.workspace_box.setCurrentIndex(1)
        view._workspace_activated(1)
        assert wait(app, lambda: view.ready)
        assert len(factory.made) == 2 and factory.made[1].cwd.endswith("ws2")
        assert wait(app, lambda: not first.alive, 10)
        assert view.transcript.findChildren(W.UserBubble)                         # historico visual mantido
        turn(app, view, "echo 1")
        assert view.chat.workspace.endswith("ws2")
    finally:
        dispose(view)


def test_missing_agent_shows_classified_banner(app, tmp_path):
    view, _ = make_view(app, tmp_path, argv=("no-such-acp-agent-xyz",))
    try:
        view.send("oi")
        assert wait(app, lambda: not view.banner.isHidden())
        assert view.banner_label.text().startswith("[not_found]")
        assert not view.busy
    finally:
        dispose(view)


def test_bypass_mode_is_never_the_default(app, tmp_path):
    view, _ = make_view(app, tmp_path)
    try:
        view._populate_modes({"currentModeId": "bypassPermissions", "availableModes": [
            {"id": "default", "name": "Ask"}, {"id": "bypassPermissions", "name": "Bypass"}]})
        assert view.mode_box.currentData() == "default"
        assert any("⚠" in view.mode_box.itemText(i) for i in range(view.mode_box.count()))
    finally:
        dispose(view)


def test_reopen_chat_reloads_session_and_renders(app, tmp_path):
    view, factory = make_view(app, tmp_path)
    try:
        turn(app, view, "tools")
        cid = view.chat.id
        view.new_chat()
        view.open_chat(cid)
        assert wait(app, lambda: view.ready)
        assert view.chat.id == cid
        assert view.transcript.findChildren(W.ToolCard)
        assert factory.made[-1].session_id == "s-1"                               # session/load do stub (loadSession=true)
    finally:
        dispose(view)


def test_store_atomic_write_and_corrupt_file_skipped(tmp_path):
    store = ChatStore(tmp_path, clock=lambda: 100.0)
    chat = store.new("C:/ws")
    chat.add("user", "  primeiro   pedido \n longo ")
    p = store.save(chat)
    assert chat.title == "primeiro pedido longo"
    assert [x.name for x in p.parent.iterdir()] == [p.name]                       # sem temporario sobrando
    (p.parent / "bad.json").write_text("{nope", encoding="utf-8")
    assert [c.id for c in store.list()] == [chat.id]
    with pytest.raises(ValueError):
        store.path("../evil")
    atomic_write_json(tmp_path / "x.json", {"b": 1, "a": 2})
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {"a": 2, "b": 1}
    assert Chat.from_dict({"id": "z", "messages": [1, {"role": "user"}], "unknown": 3}).messages == [{"role": "user"}]


def test_tool_content_is_truncated():
    big = [{"type": "content", "content": {"type": "text", "text": "x" * 9000}}]
    text = W.content_text(big)
    assert text.startswith("x" * 8000) and "truncado: 1000" in text
