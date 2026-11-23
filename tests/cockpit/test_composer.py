"""F2 da SPEC P2S-STUDIO-MULTI-AGENT-WORKSPACE-UX-v1: compositor. Enter envia, Shift+Enter quebra linha, Ctrl+Enter
envia, IME e sugestao nao enviam, vazio nao envia, sem envio duplicado, rascunho volta se falhar, preferencia salva."""
from __future__ import annotations

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QInputMethodEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402
from agent_bar.cockpit.ui.composer import Composer, load_enter_sends, save_enter_sends  # noqa: E402

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
def box():
    c = Composer()
    c.sent = []
    c.submit.connect(lambda: c.sent.append(c.toPlainText()))
    yield c
    c.deleteLater()


def test_enter_submits_and_shift_enter_inserts_a_plain_newline(box):
    QTest.keyClicks(box, "linha 1")
    QTest.keyClick(box, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(box, "linha 2")
    assert box.sent == [] and box.toPlainText() == "linha 1\nlinha 2"       # "\n", nao U+2028
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.sent == ["linha 1\nlinha 2"]


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])      # Enter principal e do teclado numerico
def test_ctrl_enter_always_submits(box, key):
    box.set_enter_sends(False)
    QTest.keyClicks(box, "oi")
    QTest.keyClick(box, key, Qt.KeyboardModifier.ControlModifier)
    assert box.sent == ["oi"]


def test_with_the_preference_off_enter_inserts_a_newline(box):
    box.set_enter_sends(False)
    QTest.keyClicks(box, "a")
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.sent == [] and box.toPlainText() == "a\n"
    assert "Ctrl+Enter envia" in box.placeholderText()


def test_enter_during_ime_composition_does_not_submit(box):
    box.inputMethodEvent(QInputMethodEvent("かな", []))                      # composicao em andamento
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.sent == []
    done = QInputMethodEvent("", [])
    done.setCommitString("かな")
    box.inputMethodEvent(done)                                               # composicao confirmada
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.sent == ["かな"]


def test_enter_with_a_suggestion_open_does_not_submit():
    c = Composer(suggestion_open=lambda: True)
    sent = []
    c.submit.connect(lambda: sent.append(1))
    QTest.keyClick(c, Qt.Key.Key_Return)
    assert sent == []
    c.deleteLater()


def test_preference_survives_a_restart(tmp_path):
    p = tmp_path / "preferences.json"
    assert load_enter_sends(p) is True                                       # padrao: Enter envia
    save_enter_sends(p, False)
    assert load_enter_sends(p) is False
    p.write_text("{nao e json", encoding="utf-8")
    assert load_enter_sends(p) is True                                       # arquivo ruim: volta ao padrao


# ---------------------------------------------------------------------------------------------------- no Chat
@pytest.fixture
def view(tmp_path):
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    v = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, STUB))],
                 workspaces_fn=lambda: [tmp_path / "ws"])
    yield v
    from PySide6.QtCore import QCoreApplication, QEvent
    v.shutdown()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_enter_in_the_chat_sends_once_and_empty_is_ignored(view):
    QTest.keyClick(view.input, Qt.Key.Key_Return)                            # vazio
    assert view.chat.messages == [] and not view.busy
    QTest.keyClicks(view.input, "echo 3")
    QTest.keyClick(view.input, Qt.Key.Key_Return)
    assert view.busy
    QTest.keyClicks(view.input, "proxima")
    QTest.keyClick(view.input, Qt.Key.Key_Return)                            # turno em curso: nem 2o envio, nem Parar
    assert [m["text"] for m in view.chat.messages if m["role"] == "user"] == ["echo 3"]
    assert view.input.toPlainText() == "proxima" and "rascunho" in view.status.text()
    assert wait(lambda: not view.busy)
    assert view.status.text().endswith("end_turn")                           # o turno nao foi cancelado pelo Enter


def test_a_failed_send_puts_the_draft_back(view, tmp_path):
    view.profiles = [AgentProfile("stub", "Stub", (str(tmp_path / "nao-existe.exe"),))]
    view.agent_box.clear()
    view.agent_box.addItem("Stub", "stub")
    QTest.keyClicks(view.input, "mensagem importante")
    QTest.keyClick(view.input, Qt.Key.Key_Return)
    assert wait(lambda: not view.banner.isHidden())
    assert view.input.toPlainText() == "mensagem importante" and not view.busy


def test_the_enter_checkbox_changes_and_saves_the_behaviour(view, tmp_path):
    view.enter_box.setChecked(False)
    assert view.input.enter_sends is False
    assert load_enter_sends(tmp_path / "data" / "preferences.json") is False
