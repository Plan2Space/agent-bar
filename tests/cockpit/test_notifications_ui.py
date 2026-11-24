"""F3 na janela: sino com contador, painel com acao pendente primeiro, abrir a origem leva ao pedido na aba
Permissoes, turno terminado fora da vista e erros viram notificacao."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.notifications import NotificationCenter  # noqa: E402
from agent_bar.cockpit.permission_inbox import PermissionInbox  # noqa: E402
from agent_bar.cockpit.ui.notifications_tab import NotificationsTab  # noqa: E402
from agent_bar.cockpit.ui.permissions_tab import PermissionsTab  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def tc(title="Write a.py"):
    return {"toolCallId": "t", "title": title, "kind": "edit", "rawInput": {"file_path": "a.py"}}


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "permissions.sqlite"
    inbox = PermissionInbox(path)
    nc = NotificationCenter(path)
    native = []
    tab = NotificationsTab(nc, inbox=inbox, refresh_ms=60_000, native_notify=lambda t, b: native.append(t))
    yield inbox, nc, tab, native
    tab.shutdown()
    tab.deleteLater()
    nc.close()
    inbox.close()


def titles(tab):
    return [tab.tree.topLevelItem(i).text(3) for i in range(tab.tree.topLevelItemCount())]


def test_the_bell_counts_pending_actions_and_new_items(env):
    inbox, nc, tab, native = env
    inbox.record(tc(), provider="claude-acp", session_id="s1")
    nc.publish("AGENT", "Claude terminou (end_turn)")
    tab.refresh()
    assert tab.bell.text() == "🔔 2" and "1 pedem ação" in tab.bell.toolTip()
    assert titles(tab)[0].startswith("Permissão pedida")                    # acao pendente primeiro
    assert native == ["Permissão pedida: Write a.py"]                       # nativo DEPOIS da linha existir, uma vez
    tab.refresh()
    assert native == ["Permissão pedida: Write a.py"]


def test_marking_read_never_hides_a_pending_action(env):
    inbox, nc, tab, _ = env
    inbox.record(tc(), provider="p", session_id="s1")
    nc.publish("AGENT", "terminou")
    tab.refresh()
    tab.all_read_btn.click()
    assert tab.bell.text() == "🔔 1" and titles(tab) == ["Permissão pedida: Write a.py"]


def test_the_decision_resolves_the_notification(env):
    inbox, nc, tab, _ = env
    e = inbox.record(tc(), provider="p", session_id="s1")
    tab.refresh()
    inbox.decide(e.id, "allow_once")
    tab.refresh()
    assert tab.bell.text() == "🔔" and titles(tab) == []                    # filtro "Abertas"
    tab.state_box.setCurrentIndex(tab.state_box.findData("RESOLVED"))
    assert titles(tab) == ["Permissão decidida: Write a.py"]


def test_open_origin_jumps_to_the_request_in_the_permissions_tab(env):
    inbox, nc, tab, _ = env
    e = inbox.record(tc(), provider="p", session_id="s1")
    perms = PermissionsTab(inbox, refresh_ms=60_000)
    links = []
    tab.open_link.connect(links.append)
    tab.refresh()
    tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
    tab.open_btn.click()
    assert links == [f"permissions:{e.id}"]
    perms.search.setText("nao bate")
    perms.select(e.id)
    assert perms.tree.currentItem().text(2) == "Write a.py" and perms.search.text() == ""
    perms.shutdown()
    perms.deleteLater()


def test_group_by_session(env):
    inbox, nc, tab, _ = env
    nc.publish("AGENT", "a", session_id="s1")
    nc.publish("AGENT", "b", session_id="s2")
    tab.group_box.setChecked(True)
    assert sorted(tab.tree.topLevelItem(i).text(0) for i in range(tab.tree.topLevelItemCount())) == ["s1", "s2"]


def test_window_wires_bell_chat_and_links(tmp_path):
    import sys

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    stub = os.path.join(os.path.dirname(__file__), "fake_acp_agent.py")
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, stub))],
                        workspaces_fn=lambda: [tmp_path / "ws"], ask_permission=lambda req, parent: "allow")
    try:
        assert win.notifications.bell.parent() is win.tabs.cornerWidget()                  # UX-07: sino + engrenagem
        win.chat.send("echo 2")                                              # janela nao ativa: fim de turno notifica
        end = time.monotonic() + 15
        while time.monotonic() < end and win.chat.busy:
            _APP.processEvents()
            time.sleep(0.01)
        assert any(n.category == "AGENT" and "terminou" in n.title for n in win.center.list())
        win.notifications.bell.click()
        assert win.tabs.currentWidget() is win.notifications
        win.open_link("chat")
        assert win.tabs.currentWidget() is win.sessions
    finally:
        win.shutdown()
        win.deleteLater()


def test_a_pending_permission_is_answered_from_the_notifications_panel(tmp_path):
    import sys

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    os.environ["AGENT_BAR_COCKPIT_DATA"] = str(tmp_path / "data")
    (tmp_path / "ws").mkdir()
    stub = os.path.join(os.path.dirname(__file__), "fake_acp_agent.py")
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, stub))],
                        workspaces_fn=lambda: [tmp_path / "ws"])                # sem ask_permission: cartao nao modal
    try:
        win.chat.send("perm")
        end = time.monotonic() + 15
        while time.monotonic() < end and not win.chat._perm_cards:
            _APP.processEvents()
            time.sleep(0.01)
        nt = win.notifications
        nt.refresh()
        nt.tree.setCurrentItem(nt.tree.topLevelItem(0))
        assert nt.allow_btn.isVisibleTo(nt) and nt.tree.topLevelItem(0).text(2) == "pede ação"
        nt.allow_btn.click()
        while time.monotonic() < end and win.chat.busy:
            _APP.processEvents()
            time.sleep(0.01)
        assert not win.chat.busy
        assert any(m["text"] == "outcome=selected:allow" for m in win.chat.chat.messages)
        assert nt.bell.text() in ("🔔", "🔔 1")                               # a acao pendente sumiu do contador
        card = next(iter(win.chat._perm_cards.values()))
        assert "permitido" in card.result.text()
    finally:
        win.shutdown()
        win.deleteLater()
