"""UX-07 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9, decisão D-UX-2 aprovada pelo Felipe: "Approve everything").

A navegação principal tem exatamente Chat, Design, PRs e Notificações, nessa ordem. Fluxos, Dev, Permissões, Biblioteca e
Actions vão para Configurações → Avançado. Nenhuma função some (VS-I1, teste por objeto), e cada diálogo do §2.4 cujo
lugar mudou dispara do lugar novo com o mesmo título, a mesma opção padrão e o mesmo resultado ao fechar.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")
MAIN = ["Chat", "Design", "PRs", "Notificações"]
ADVANCED = ["Fluxos", "Dev", "Permissões", "Biblioteca", "Actions"]


class FakeCore:
    def __init__(self):
        self.settings = SimpleNamespace(bar_enabled=False, notify_human=True)
        self.bar_calls: list = []
        self.snap = None
        self.runtime_pin = None
        self.worker = SimpleNamespace(refresh=lambda force=False: None)

    def set_bar_enabled(self, on):
        self.bar_calls.append(bool(on))
        self.settings.bar_enabled = bool(on)


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    import agent_bar.cockpit.ui.window as CW
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.profiles import AgentProfile
    monkeypatch.setattr(CW, "screen_rects", lambda: [WS.Rect(0, 0, 2560, 1400)])
    (tmp_path / "ws").mkdir()
    core = FakeCore()
    w = CW.CockpitWindow(repos=["o/r"], workspaces_fn=lambda: [tmp_path / "ws"], core=core,
                         profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                         window_store=WS.WindowStateStore(tmp_path / "window.json"))
    w.show()
    _APP.processEvents()
    try:
        yield w, core
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent
        w.shutdown()
        w.hide()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pump(n=5):
    for _ in range(n):
        _APP.processEvents()


def test_the_main_navigation_is_exactly_chat_design_prs_notifications(win):
    w, _ = win
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == MAIN
    assert [w.tabs.widget(i) for i in range(4)] == [w.sessions, w.review, w.prs, w.notifications]


def test_advanced_holds_flows_dev_permissions_library_and_actions(win):
    w, _ = win
    assert [w.advanced.tabText(i) for i in range(w.advanced.count())] == ADVANCED
    assert [w.advanced.widget(i) for i in range(5)] == [w.workflows, w.dev, w.permissions, w.library, w.actions]
    names = [w.settings.sections.item(i).text() for i in range(w.settings.sections.count())]
    assert "Avançado" in names and "Geral" in names


@pytest.mark.parametrize("name", ["sessions", "review", "prs", "notifications", "settings", "workflows", "dev",
                                  "permissions", "library", "actions"])
def test_every_old_object_is_still_reachable_and_visible(win, name):
    """VS-I1: cada objeto da navegação antiga tem um destino que abre e mostra o próprio widget."""
    w, _ = win
    obj = getattr(w, name)
    w.show_page(obj)
    pump()
    assert w.current_page() is obj and obj.isVisible(), name


def test_the_gear_opens_settings_and_back_returns_to_the_area_you_were_in(win):
    w, _ = win
    w.show_page(w.prs)
    w.gear.click()
    pump()
    assert w.current_page() is w.settings and w.settings.isVisible() and not w.tabs.isVisible()
    w.settings_back.click()
    pump()
    assert w.current_page() is w.prs and w.tabs.isVisible()
    w.show_page(w.workflows)
    w.notifications.bell.click()                                                 # o sino leva às Notificações
    pump()
    assert w.current_page() is w.notifications


@pytest.mark.parametrize("link, target", [("chat", "sessions"), ("workflows", "workflows"),
                                          ("permissions:req-1", "permissions")])
def test_notification_links_open_the_object_in_its_new_place(win, link, target):
    w, _ = win
    w.open_link(link)
    pump()
    assert w.current_page() is getattr(w, target)


def test_actions_and_library_load_on_their_first_visit_in_advanced(win, monkeypatch):
    w, _ = win
    calls = []
    monkeypatch.setattr(w.actions, "refresh", lambda: calls.append("actions"))
    monkeypatch.setattr(w.library, "ensure_loaded", lambda: calls.append("library"))
    w.show_page(w.actions)
    w.show_page(w.library)
    pump()
    assert calls == ["actions", "library"]


def test_general_settings_turn_the_optional_floating_bar_on_and_off(win):
    w, core = win
    box = w.settings.bar_toggle
    assert box.text() == "Barra flutuante (opcional)" and not box.isChecked()
    box.click()
    assert core.bar_calls == [True]
    box.click()
    assert core.bar_calls == [True, False]


# --------------------------------------------------------------------- §2.4: diálogos disparados do lugar novo
def _capture_box(monkeypatch, answer):
    seen = {}

    def exec_(box):
        seen.update(title=box.windowTitle(), default=box.defaultButton() and box.standardButton(box.defaultButton()))
        return answer
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return seen


def test_risky_stage_modes_dialog_fires_from_advanced_flows_with_no_as_default(win, monkeypatch):
    from agent_bar.cockpit.ui.workflows_tab import confirm_risky_stage_modes
    w, _ = win
    w.show_page(w.workflows)
    seen = _capture_box(monkeypatch, QMessageBox.StandardButton.No)
    assert confirm_risky_stage_modes([("spec", "bypassPermissions")], w.workflows) is False   # fechar = recusar
    assert seen == {"title": "Etapas sem pedir permissão", "default": QMessageBox.StandardButton.No}


def test_permission_dialog_of_a_flow_fires_from_advanced_flows_and_closing_refuses(win, monkeypatch):
    from agent_bar.cockpit.acp import PermissionRequest
    from agent_bar.cockpit.ui import widgets as Wd
    w, _ = win
    w.show_page(w.workflows)
    seen = {}

    def exec_(dlg):
        seen.update(title=dlg.windowTitle(), modal=dlg.isModal(), parent=dlg.parent())
        return 0                                                                  # fechou sem escolher
    monkeypatch.setattr(Wd.PermissionDialog, "exec", exec_)
    req = PermissionRequest("s-1", {"toolCallId": "t", "title": "Run ls", "kind": "execute"},
                            ({"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                             {"optionId": "reject", "name": "Reject", "kind": "reject_once"}))
    assert w.workflows.ask_permission(req, w.workflows) is None
    assert seen == {"title": "Permissão pedida pelo agente", "modal": True, "parent": w.workflows}


@pytest.mark.parametrize("title", ["Aprovar promoção (uso único)", "Promover agora"])
def test_dev_promotion_dialogs_fire_from_advanced_dev_and_no_refuses(win, monkeypatch, title):
    w, _ = win
    w.show_page(w.dev)
    seen = {}

    def question(parent, t, _text, *a, **k):
        seen.update(title=t, parent=parent)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    assert w.dev.confirm(w.dev, title, "texto") is False
    assert seen == {"title": title, "parent": w.dev}


def test_irreversible_dialog_still_needs_the_code_when_answered_from_advanced_permissions(win, monkeypatch):
    from agent_bar.cockpit.acp import PermissionRequest
    from agent_bar.cockpit.ui import widgets as Wd
    w, _ = win
    w.show_page(w.permissions)
    seen = {}

    def exec_(dlg):
        seen.update(title=dlg.windowTitle(), parent=dlg.parent())
        return 0                                                                  # fechou sem digitar o código
    monkeypatch.setattr(Wd.IrreversibleDialog, "exec", exec_)
    req = PermissionRequest("s-1", {"toolCallId": "t", "title": "rm", "kind": "execute",
                                    "rawInput": {"command": "rm -rf build"}},
                            ({"optionId": "allow", "name": "Allow", "kind": "allow_once"},))
    assert Wd.ask_permission_dialog(req, w.permissions) is None                  # sem o código, nada é permitido
    assert seen["parent"] is w.permissions and seen["title"]


def test_no_extra_window_opens_while_navigating(win):
    w, _ = win
    before = {x for x in QApplication.topLevelWidgets() if x.isVisible()}
    for obj in (w.sessions, w.review, w.prs, w.notifications, w.settings, w.workflows, w.dev, w.permissions,
                w.library, w.actions):
        w.show_page(obj)
        pump(2)
    time.sleep(0.05)
    assert {x for x in QApplication.topLevelWidgets() if x.isVisible()} - before == set()
