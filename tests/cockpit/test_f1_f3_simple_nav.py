"""F1 + F3 da simplificação (pedido do Felipe, 2026-11-28, via coordenador).

"Tem muita coisa nesse app, dá pra simplificar muito. Vamos começar no básico: o chat, o design e as notificações. Na
notificação, quando eu clico não me leva pro PR que preciso olhar ou mergear."

F1: a janela abre com 3 áreas (Chat, Design, Notificações) no estilo dos tokens de tema (o da tela de Temas/Visão
geral). Todo o resto, PRs inclusive, fica no menu "Avançado", sem apagar nada.
F3: Notificações abre em "Precisa de você"; o resto fica em "Ver tudo". Um clique numa notificação de PR abre o PR
certo no navegador (a URL real do PR).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")
PR_URL = "https://github.com/o/r/pull/7"


class FakeCore:
    def __init__(self):
        self.settings = SimpleNamespace(bar_enabled=False, notify_human=True)
        self.snap = None
        self.runtime_pin = None
        self.worker = SimpleNamespace(refresh=lambda force=False: None)

    def set_bar_enabled(self, on):
        self.settings.bar_enabled = bool(on)


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    import agent_bar.cockpit.ui.window as CW
    from agent_bar import controls
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.github import GhClient
    from agent_bar.cockpit.profiles import AgentProfile
    opened: list[str] = []
    monkeypatch.setattr(controls, "open_url", lambda url, opener=None: opened.append(url))   # nada abre de verdade
    monkeypatch.setattr(CW, "screen_rects", lambda: [WS.Rect(0, 0, 2560, 1400)])
    (tmp_path / "ws").mkdir()
    gh = GhClient(exe=[sys.executable, str(Path(__file__).with_name("fake_gh.py"))])
    w = CW.CockpitWindow(gh=gh, repos=["o/r"], workspaces_fn=lambda: [tmp_path / "ws"], core=FakeCore(),
                         profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                         window_store=WS.WindowStateStore(tmp_path / "window.json"))
    w.show()
    _APP.processEvents()
    try:
        yield w, opened
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent
        w.shutdown()
        w.hide()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pump(n=5):
    for _ in range(n):
        _APP.processEvents()


# ---------------------------------------------------------------------------------------------------------- F1
def test_the_window_opens_with_three_areas_chat_design_notifications(win):
    w, _ = win
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["Chat", "Design", "Notificações"]
    assert [w.tabs.widget(i) for i in range(w.tabs.count())] == [w.sessions, w.review, w.notifications_area]


def test_everything_else_is_one_click_away_in_the_advanced_menu(win):
    w, _ = win
    menu = w.advanced_btn.menu()
    assert w.advanced_btn.text() == "Avançado" and menu is not None
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == [
        "PRs", "Fluxos", "Dev", "Biblioteca", "Actions", "Configurações"]
    for action in menu.actions():
        if action.isSeparator():
            continue
        action.trigger()
        pump()
        page = w.current_page()
        assert page.isVisible(), action.text()
        if action.text() == "PRs":
            assert page is w.prs


def test_the_areas_follow_the_theme_tokens_like_the_themes_screen(win):
    from agent_bar.cockpit.ui.theme import catalog
    w, _ = win
    other = next(t for t in catalog.THEMES if t.key != w.theme_manager.current.key and t.accent not in w.tabs.styleSheet())
    try:
        w.theme_manager.apply(other)
        pump()
        assert other.accent.lower() in w.tabs.styleSheet().lower()             # a aba ativa usa o acento do tema
    finally:
        w.theme_manager.apply(catalog.get(catalog.DEFAULT))                    # o gerenciador é do processo: devolve
        w.theme_manager.close()                                                # e tira a ponte do app (volta no próximo apply)


# ---------------------------------------------------------------------------------------------------------- F3
def test_notifications_open_on_what_needs_felipe_and_the_rest_is_in_see_all(win):
    w, _ = win
    area = w.notifications_area
    titles = [area.sections.tabText(i).split(" (")[0] for i in range(area.sections.count())]
    assert titles == ["Precisa de você", "Permissões", "Ver tudo"]
    w.notifications.bell.click()
    pump()
    assert area.sections.currentWidget() is w.needs


def test_one_click_on_a_pr_that_needs_felipe_opens_that_pr(win):
    w, opened = win
    w.center.publish("HUMAN_APPROVAL", "PR precisa de você: o/r#7", body="HIGH · feat: x", state="ACTION_REQUIRED",
                     source="human", link=PR_URL, dedupe_key="human:o/r#7@abc")
    w.show_page(w.needs)
    w.needs.refresh()
    item = w.needs.tree.topLevelItem(0)
    w.needs.tree.itemClicked.emit(item, 1)                                       # um clique, não duplo
    pump()
    assert opened == [PR_URL]


def test_one_click_on_a_pr_in_see_all_opens_the_pr_too(win):
    w, opened = win
    w.center.publish("HUMAN_APPROVAL", "PR precisa de você: o/r#7", body="HIGH", state="ACTION_REQUIRED",
                     source="human", link=PR_URL, dedupe_key="human:o/r#7@abc")
    w.show_page(w.notifications)
    w.notifications.refresh()
    item = next(w.notifications.tree.topLevelItem(i) for i in range(w.notifications.tree.topLevelItemCount())
                if "o/r#7" in w.notifications.tree.topLevelItem(i).text(3))
    w.notifications.tree.itemClicked.emit(item, 3)
    pump()
    assert opened == [PR_URL]


def test_one_click_on_a_permission_only_selects_it(win):
    """Um clique só ABRE quando a origem é um PR (URL). Uma permissão continua sendo selecionada para Permitir/Negar."""
    w, opened = win
    w.center.publish("AGENT", "turno terminou", state="NEW", link="chat")
    w.show_page(w.notifications)
    w.notifications.refresh()
    item = w.notifications.tree.topLevelItem(0)
    w.notifications.tree.itemClicked.emit(item, 3)
    pump()
    assert opened == [] and w.current_page() is w.notifications
