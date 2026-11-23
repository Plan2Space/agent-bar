"""F5 (UX): abas de sessao no Chat — conversas simultaneas, limite medido por provedor, fechamento obrigatorio,
um inbox/broker para todas as abas, sempre uma aba aberta."""
from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui.chat_sessions import ChatSessions  # noqa: E402
from tests.support.process import pid_alive  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
STUB = os.path.join(os.path.dirname(__file__), "fake_acp_agent.py")


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def sessions(tmp_path):
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    s = ChatSessions(store=ChatStore(tmp_path / "data"), profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, STUB))],
                     workspaces_fn=lambda: [tmp_path / "ws"], limits={"stub": 2})
    yield s
    s.shutdown()
    s.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def agent_texts(view):
    return [m["text"] for m in view.chat.messages if m["role"] == "agent"]


def test_two_conversations_run_at_the_same_time_each_with_its_own_agent(sessions):
    a = sessions.current()
    b = sessions.add_session()
    a.send("slow")
    b.send("echo 2")
    assert wait(lambda: not b.busy), ("a 2a conversa nao terminou: status, banner, cliente, pronta, papeis, status da 1a", b.status.text(), b.banner_label.text(), b.client is not None, b.ready, [m["role"] for m in b.chat.messages], a.status.text())
    assert a.busy and agent_texts(b) == ["c0 c1 "]                          # b terminou com a ainda trabalhando
    assert a.client.pid != b.client.pid
    sessions._tick()
    assert sessions.tabs.tabText(0).startswith("●") and sessions.tabs.tabText(1).startswith("○")
    a.stop()
    assert wait(lambda: not a.busy)


def test_opening_past_the_measured_limit_is_refused_before_a_process_starts(sessions):
    first, second = sessions.current(), sessions.add_session()
    for v in (first, second):
        v.send("echo 1")
        assert wait(lambda v=v: not v.busy)
    third = sessions.add_session()
    third.input.setPlainText("terceira")
    third.send("terceira")
    assert third.client is None and not third.busy
    assert "limite medido" in third.banner_label.text() and third.input.toPlainText() == "terceira"
    sessions.close_session(sessions.tabs.indexOf(first))
    third.send(third.input.toPlainText())
    assert wait(lambda: not third.busy) and third.client is not None


def test_closing_a_tab_ends_its_agent_and_cancels_only_its_permission_requests(sessions):
    a = sessions.current()
    b = sessions.add_session()
    a.send("perm")
    b.send("perm")
    assert wait(lambda: a._perm_cards and b._perm_cards)
    pid = a.client.pid
    ea, eb = next(iter(a._perm_cards)), next(iter(b._perm_cards))
    sessions.close_session(sessions.tabs.indexOf(a))
    assert wait(lambda: not pid_alive(pid), 10)
    got = sessions.inbox.get(ea)
    assert got.status == "CANCELLED" and "aba da sessão" in got.result
    assert sessions.broker.is_open(eb) and sessions.inbox.get(eb).status == "PENDING"
    assert sessions.answer_permission(eb, "allow_once")                      # pela aba Permissoes/Notificacoes
    assert wait(lambda: not b.busy) and agent_texts(b)[-1] == "outcome=selected:allow"
    assert "permitido" in b._perm_cards[eb].result.text()


def test_there_is_always_one_conversation(sessions):
    sessions.close_session(0)
    assert sessions.tabs.count() == 1 and len(sessions.views) == 1


# ---------------------------------------------------------------- #75: sinais enfileirados depois do shutdown
def _make(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ChatSessions(store=ChatStore(tmp_path / "data"), limits={"stub": 2},
                        profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, STUB))],
                        workspaces_fn=lambda: [tmp_path / "ws"])                # dono do inbox


def test_agent_signals_queued_before_shutdown_never_touch_the_closed_inbox(tmp_path, no_exception_in_qt_slots):
    from agent_bar.cockpit.acp import PermissionRequest, Update
    s = _make(tmp_path)
    v, answered = s.current(), []
    v.bridge.update.emit(v.gen, Update("tool_call_update", "s", {"toolCallId": "t1", "status": "completed"}))
    v.bridge.permission.emit(v.gen, PermissionRequest("s", {"toolCallId": "t2", "title": "Run rm", "kind": "execute"},
                             ({"optionId": "allow", "name": "Allow", "kind": "allow_once"},)), answered.append)
    s.shutdown()
    wait(lambda: False, timeout=0.3)                                         # drena a fila
    assert no_exception_in_qt_slots == []                                    # antes: 2x closed database
    assert answered == [None]                                                # o agente recebe "cancelled", nunca fica pendurado
    s.deleteLater()


def test_a_connect_completed_before_shutdown_does_not_try_to_prompt_afterwards(tmp_path, no_exception_in_qt_slots):
    s = _make(tmp_path)
    v = s.current()
    v.pending_prompt = "texto que estava esperando a conexao"
    v.bridge.done.emit(v.gen, "connect", {"session_id": "s-1", "modes": {}, "config_options": []}, None)
    s.shutdown()
    wait(lambda: False, timeout=0.3)
    assert no_exception_in_qt_slots == []                                    # antes: AssertionError em _prompt
    s.deleteLater()


def test_closing_the_studio_with_turns_in_flight_leaves_no_exception(tmp_path, no_exception_in_qt_slots):
    """Cenario B do #75: abas com tools/perm em voo e o Studio fechando no meio."""
    for round_ in range(4):
        s = _make(tmp_path / f"r{round_}")
        views = [s.current(), s.add_session()]
        for k, v in enumerate(views):
            v.send("perm" if (round_ + k) % 2 else "tools")
        wait(lambda: False, timeout=0.02 * round_)
        s.shutdown()
        wait(lambda: False, timeout=0.3)
        s.deleteLater()
    assert no_exception_in_qt_slots == []


# ------------------------------------------------------------- orfaos no app (F5): so os do proprio Studio
def _registry_with(tmp_path, pid, created, studio_pid=999999, studio_created=1.0):
    import json
    p = tmp_path / "agent-processes.json"
    p.write_text(json.dumps({"schema": "p2s.agent-registry.v1", "studio": {"pid": studio_pid, "created": studio_created},
                             "agents": [{"pid": pid, "created": created, "profile": "claude"}]}), encoding="utf-8")
    return p


def test_agents_left_by_a_crashed_studio_show_a_banner_and_only_those_are_ended(tmp_path):
    import subprocess
    from agent_bar.cockpit.agent_registry import AgentRegistry
    from agent_bar.platform.proc import creation_time
    leftover = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        reg = AgentRegistry(_registry_with(tmp_path, leftover.pid, creation_time(leftover.pid)))
        s = ChatSessions(store=ChatStore(tmp_path / "data"), registry=reg, limits={"stub": 2},
                         profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, STUB))],
                         workspaces_fn=lambda: [tmp_path / "ws"])
        assert s.orphan_bar.isVisibleTo(s) and str(leftover.pid) in s.orphan_label.text()
        assert s.end_orphans() == [leftover.pid]
        assert wait(lambda: leftover.poll() is not None, 10) and not s.orphan_bar.isVisibleTo(s)
        s.shutdown()
        s.deleteLater()
    finally:
        if leftover.poll() is None:
            leftover.kill()


def test_a_reused_pid_or_a_live_studio_never_shows_as_an_orphan(tmp_path):
    import subprocess
    from agent_bar.cockpit.agent_registry import AgentRegistry
    from agent_bar.platform.proc import creation_time
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stale = AgentRegistry(_registry_with(tmp_path, other.pid, creation_time(other.pid) - 5))   # outro processo no PID
        assert stale.previous_orphans() == []
        alive_studio = AgentRegistry(_registry_with(tmp_path, other.pid, creation_time(other.pid),
                                                    studio_pid=other.pid, studio_created=creation_time(other.pid)))
        assert alive_studio.previous_orphans() == []                         # quem gravou ainda roda
        assert other.poll() is None
    finally:
        other.kill()
        other.wait(10)


def test_the_registry_follows_live_agents_and_is_cleared_on_a_clean_exit(tmp_path):
    import json
    from agent_bar.cockpit.agent_registry import AgentRegistry
    path = tmp_path / "agent-processes.json"
    (tmp_path / "ws").mkdir(exist_ok=True)
    s = ChatSessions(store=ChatStore(tmp_path / "data"), registry=AgentRegistry(path), limits={"stub": 2},
                     profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, STUB))],
                     workspaces_fn=lambda: [tmp_path / "ws"])
    v = s.current()
    v.send("echo 1")
    assert wait(lambda: not v.busy)
    s._tick()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [a["pid"] for a in data["agents"]] == [v.client.pid] and data["agents"][0]["created"]
    assert v.client.contained                                                # no Windows: dentro do Job Object
    s.shutdown()
    assert json.loads(path.read_text(encoding="utf-8"))["agents"] == []
    s.deleteLater()
