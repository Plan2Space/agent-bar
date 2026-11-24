"""UX-08 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9; D-UX-2 e D-VS-6): Permissões e "Precisa de você" moram DENTRO de
Notificações, a quarta área principal. Critério: pedido de permissão aberto aparece em Notificações com permitir/negar;
decisão idempotente (os testes do Inbox seguem verdes). O "Abrir origem" de um PR que precisa do Felipe abre o PR.
Agente ACP de teste e `gh` falso; sem rede."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import permission_mode as PM  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")
FAKE_GH = Path(__file__).with_name("fake_gh.py")


def pump(n=5):
    for _ in range(n):
        _APP.processEvents()


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def win(tmp_path, monkeypatch):
    from agent_bar.cockpit.github import GhClient
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv(PM.ENV, PM.ASK)                                           # o pedido fica aberto para responder
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(gh=GhClient(exe=[sys.executable, str(FAKE_GH)]), repos=["o/r"],
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    w.resize(1280, 800)
    w.show()
    pump()
    yield w
    from PySide6.QtCore import QCoreApplication, QEvent
    w.shutdown()
    w.hide()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_notifications_holds_all_needs_you_and_permissions(win):
    area = win.notifications_area
    assert win.tabs.tabText(3) == "Notificações" and win.tabs.widget(3) is area
    names = [area.sections.tabText(i).split(" (")[0] for i in range(area.sections.count())]
    assert names == ["Todas", "Precisa de você", "Permissões"]
    assert [area.sections.widget(i) for i in range(3)] == [win.notifications, win.needs, win.permissions]
    assert win.advanced.indexOf(win.permissions) < 0                              # saiu de Avançado


@pytest.mark.parametrize("name", ["notifications", "needs", "permissions"])
def test_each_section_opens_in_place_and_is_the_current_page(win, name):
    obj = getattr(win, name)
    win.show_page(win.prs)
    win.show_page(obj)
    pump()
    assert win.current_page() is obj and obj.isVisible()
    assert win.tabs.currentWidget() is win.notifications_area


def test_the_bell_and_a_permission_link_land_inside_notifications(win):
    win.show_page(win.prs)
    win.notifications.bell.click()
    pump()
    assert win.current_page() is win.notifications
    win.open_link("permissions:req-1")
    pump()
    assert win.current_page() is win.permissions and win.tabs.currentWidget() is win.notifications_area


def test_needs_you_lists_the_prs_waiting_for_felipe_and_opens_the_pr(win, monkeypatch):
    from agent_bar import controls as ctl
    opened = []
    monkeypatch.setattr(ctl, "open_url", lambda url: opened.append(url))
    win.center.publish("HUMAN_APPROVAL", "PR precisa de você: Plan2Space/agent-bar#7",
                       body="HIGH · feat: x · decisão do Felipe · HEAD aaaaaaa", state="ACTION_REQUIRED",
                       source="human-inbox", link="https://github.com/Plan2Space/agent-bar/pull/7",
                       dedupe_key="human:Plan2Space/agent-bar#7@" + "a" * 40)
    win.center.publish("AGENT", "turno terminou", body="", state="NEW", source="chat", link="chat")
    needs = win.needs
    needs.refresh()
    win.notifications_area.update_counts()                                       # o timer da área faz isso a cada 2 s
    rows = [needs.tree.topLevelItem(i).text(1) for i in range(needs.tree.topLevelItemCount())]
    assert rows == ["PR precisa de você: Plan2Space/agent-bar#7"]                 # só o que é do Felipe
    assert "Precisa de você (1)" == win.notifications_area.sections.tabText(1)
    needs.tree.setCurrentItem(needs.tree.topLevelItem(0))
    needs.open_btn.click()
    assert opened == ["https://github.com/Plan2Space/agent-bar/pull/7"]


def test_open_origin_of_a_pr_notification_opens_the_pr(win, monkeypatch):
    """Antes do UX-08, "Abrir origem" de um item com link https não fazia nada (o open_link só conhecia
    permissions:/chat/workflows)."""
    from agent_bar import controls as ctl
    opened = []
    monkeypatch.setattr(ctl, "open_url", lambda url: opened.append(url))
    win.open_link("https://github.com/Plan2Space/agent-bar/pull/7")
    assert opened == ["https://github.com/Plan2Space/agent-bar/pull/7"]


def test_an_open_permission_shows_in_notifications_with_allow_and_deny_and_the_decision_is_idempotent(win):
    win.chat.send("perm")
    assert wait(lambda: bool(win.chat._perm_cards))
    nt = win.notifications
    nt.refresh()
    nt.tree.setCurrentItem(nt.tree.topLevelItem(0))
    assert nt.allow_btn.isVisibleTo(nt) and nt.deny_btn.isVisibleTo(nt)
    assert "Permissões (1 pendente)" == win.notifications_area.sections.tabText(2)
    rid = next(iter(win.chat._perm_cards))
    assert win.sessions.answer_permission(rid, "allow_once") is True
    assert wait(lambda: not win.chat.busy)
    assert win.sessions.answer_permission(rid, "allow_once") is False             # já respondido: nada muda
    assert win.sessions.answer_permission(rid, "deny") is False                   # nem reescreve a decisão
    e = win.sessions.inbox.get(rid)
    assert (e.status, e.decision) == ("ALLOWED", "allow_once")
    nt.refresh()
    win.notifications_area.update_counts()
    assert not nt.allow_btn.isVisibleTo(nt)
    assert win.notifications_area.sections.tabText(2) == "Permissões"
