"""Chat do Studio com provedor nao-ACP (Ollama, F2a) pelo ProviderClient: mesmo Chat, streaming, modelo e esforco so
com o que o adaptador comprovou, cancelamento. Contra o Ollama falso por HTTP de verdade."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.provider_client import ProviderClient  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui import widgets as W  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402
from agent_bar.platform.ollama import OllamaAdapter  # noqa: E402
from tests.platform.fake_ollama import FakeOllama  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
PROFILE = AgentProfile("ollama", "Ollama (local)", ("ollama",), adapter="ollama")


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def server():
    with FakeOllama() as s:
        yield s


@pytest.fixture
def view(server, tmp_path):
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    made = []

    def factory(profile, ws, **kw):
        made.append(ProviderClient(OllamaAdapter(server.endpoint), **kw))
        return made[-1]
    v = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [PROFILE], workspaces_fn=lambda: [tmp_path / "ws"],
                 provider_factory=factory)
    v.made = made
    yield v
    from PySide6.QtCore import QCoreApplication, QEvent
    v.shutdown()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def turn(view, text):
    view.send(text)
    assert wait(lambda: not view.busy), "turno nao terminou"


def choose(view, box, value):
    box.setCurrentIndex(box.findData(value))
    box.activated.emit(box.currentIndex())
    assert wait(lambda: not view._config_pending)


def test_ollama_chats_through_the_same_chat_with_streaming(view):
    turn(view, "diga ok")
    assert [m["role"] for m in view.chat.messages] == ["user", "agent"] and view.chat.messages[1]["text"] == "ok"
    assert view.status.text().endswith("end_turn")


def test_model_box_lists_only_chat_models_and_effort_appears_only_for_thinking(view):
    turn(view, "diga ok")
    assert [view.model_box.itemData(i) for i in range(view.model_box.count())] == ["chat-a", "think-b", "vision-d"]
    assert view.effort_box.isHidden()                                        # chat-a nao tem thinking
    choose(view, view.model_box, "think-b")
    assert [view.effort_box.itemData(i) for i in range(view.effort_box.count())] == ["default", "off", "on"]
    choose(view, view.effort_box, "on")
    assert view.confirmed.text() == "confirmado pelo agente: modelo think-b · esforço on"
    assert any("sessão nova" in n.text() for n in view.transcript.findChildren(W.Notice))
    turn(view, "diga ok")
    assert view.chat.messages[-1]["text"] == "ok" and any(m["role"] == "thought" for m in view.chat.messages)


def test_a_slow_answer_is_cancelled_from_the_chat(view, server):
    turn(view, "diga ok")
    view.send("conte devagar")
    assert wait(lambda: len(view._turn_text) > 3)
    view.stop()
    assert wait(lambda: not view.busy, 5)
    assert view.status.text().endswith("cancelled")
    assert wait(lambda: server.aborted == 1, 5)


def test_provider_without_permission_modes_refuses_set_mode():
    client = ProviderClient(OllamaAdapter("http://127.0.0.1:9"))
    with pytest.raises(Exception, match="modos de permissao"):
        client.set_mode("auto").result(2)
    client.close().result(2)


def test_server_down_shows_the_error_banner(tmp_path):
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    v = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [PROFILE], workspaces_fn=lambda: [tmp_path / "ws"],
                 provider_factory=lambda p, ws, **kw: ProviderClient(OllamaAdapter("http://127.0.0.1:9", timeout=1), **kw))
    try:
        v.send("oi")
        assert wait(lambda: not v.banner.isHidden())
        assert "nao respondeu" in v.banner_label.text() and not v.busy
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent
        v.shutdown()
        v.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_no_empty_mode_box_and_one_new_session_notice_per_pause(view):
    turn(view, "diga ok")
    assert view.mode_box.isHidden() and view.mode_label.isHidden()          # Ollama nao tem modos de permissao
    choose(view, view.model_box, "think-b")
    choose(view, view.effort_box, "on")
    notes = [n.text() for n in view.transcript.findChildren(W.Notice) if "sessão nova" in n.text()]
    assert len(notes) == 1
    assert not any("ACP" in n.text() for n in view.transcript.findChildren(W.Notice))
