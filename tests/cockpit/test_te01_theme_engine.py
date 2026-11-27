"""TE-01 (P2S-STUDIO-THEME-ENGINE-001; pedido do Felipe de 2026-11-27, §5 e §5.1): o modal de Temas.

Cada critério do §5.1 vira um teste:
- hover aplica a prévia no app inteiro e NÃO grava; tirar o mouse volta ao escolhido (o salvo, se nada foi clicado);
- clique mantém a prévia; Salvar grava; Cancelar, Esc e fechar restauram o salvo;
- teclado equivalente (setas escolhem, Enter salva);
- não reinicia nem perde a navegação: a página aberta e a conexão do Chat continuam as mesmas;
- sem flicker: a janela volta a pintar (updatesEnabled) depois da troca.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402

from agent_bar.cockpit.ui import theme_picker as TP  # noqa: E402
from agent_bar.cockpit.ui.theme import catalog as C  # noqa: E402
from agent_bar.cockpit.ui.theme import qt as Q  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")


def no_network_gh():
    from agent_bar.cockpit.github import GhClient, GhError

    class NoNetworkGh(GhClient):
        def _run(self, *a, **k):
            raise GhError("auth", "sem rede no teste")
    return NoNetworkGh(exe=["nao-usado"])


def pump(seconds: float = 0.25) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


@pytest.fixture
def mgr():
    store = {"key": C.DEFAULT, "fav": []}
    m = Q.ThemeManager(load_key=lambda: store["key"], save_key=lambda k: store.update(key=k), app=_APP)
    m.store = store
    old = (_APP.palette(), _APP.styleSheet(), _APP.font())
    yield m
    m.apply(C.get(C.DEFAULT))
    m.close()
    _APP.setPalette(old[0])
    _APP.setStyleSheet(old[1])
    _APP.setFont(old[2])


@pytest.fixture
def picker(mgr):
    p = TP.ThemePicker(mgr, favorites_load=lambda: list(mgr.store["fav"]),
                       favorites_save=lambda keys: mgr.store.update(fav=list(keys)))
    p.show()
    yield p
    p.deleteLater()


def item(picker, key):
    for i in range(picker.list.count()):
        it = picker.list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == key:
            return it
    raise AssertionError(key)


def hover(picker, key):
    picker.list.itemEntered.emit(item(picker, key))
    pump(TP.HOVER_MS / 1000 + 0.15)


def leave(picker):
    QtWidgets.QApplication.sendEvent(picker.list.viewport(), QEvent(QEvent.Type.Leave))


def test_hover_previews_everywhere_without_saving_and_leaving_restores(picker, mgr):
    hover(picker, "github-light")
    assert mgr.current.key == "github-light" and "#0969da" in picker.styleSheet()  # cada janela visível
    assert mgr.store["key"] == C.DEFAULT                                         # não gravou
    leave(picker)
    assert mgr.current.key == C.DEFAULT                                          # voltou ao salvo


def test_click_keeps_the_preview_and_save_persists(picker, mgr):
    picker.list.setCurrentItem(item(picker, "graphite"))                         # clique
    hover(picker, "github-light")
    leave(picker)
    assert mgr.current.key == "graphite"                                         # o clicado fica
    picker.save_btn.click()
    assert mgr.store["key"] == "graphite" and mgr.saved.key == "graphite"


@pytest.mark.parametrize("how", ["cancel", "esc", "close"])
def test_cancel_esc_and_close_restore_the_saved_theme(picker, mgr, how):
    picker.list.setCurrentItem(item(picker, "warm-studio"))
    assert mgr.current.key == "warm-studio"
    if how == "cancel":
        picker.cancel_btn.click()
    elif how == "esc":
        QtWidgets.QApplication.sendEvent(picker, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    else:
        picker.close()
    assert mgr.current.key == C.DEFAULT and mgr.store["key"] == C.DEFAULT


def test_keyboard_arrows_choose_and_enter_saves(picker, mgr):
    picker.list.setFocus()
    start = picker.list.currentRow()
    QtWidgets.QApplication.sendEvent(picker.list, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert picker.list.currentRow() == start + 1
    chosen = picker.theme_of(picker.list.currentItem())
    assert mgr.current.key == chosen.key                                         # seta = prévia escolhida
    QtWidgets.QApplication.sendEvent(picker, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert mgr.store["key"] == chosen.key


def test_search_category_and_favorites(picker, mgr):
    picker.search.setText("dimmed")
    assert picker.list.count() == 1
    picker.search.setText("")
    picker.category.setCurrentIndex(picker.category.findData("GitHub"))
    assert picker.list.count() == 3
    picker.list.setCurrentItem(item(picker, "github-dark"))
    picker.fav_btn.click()
    assert mgr.store["fav"] == ["github-dark"] and picker.fav_btn.text() == "Tirar dos favoritos"
    picker.category.setCurrentIndex(0)
    picker.only_fav.setChecked(True)
    assert picker.list.count() == 1 and picker.list.item(0).text().startswith("Favorito · ")   # palavra, não só cor


def test_the_window_has_a_themes_button_and_switching_keeps_the_page_and_the_chat(tmp_path, monkeypatch):
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(Q, "_MANAGER", None)
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(repos=["o/r"], gh=no_network_gh(), window_store=WS.WindowStateStore(tmp_path / "window.json"),
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    try:
        w.show()
        corner = w.tabs.cornerWidget(Qt.Corner.TopRightCorner)
        assert w.themes_btn.parent() is corner and w.themes_btn.text() == "Temas"
        w.show_page(w.prs)
        chat, gen = w.chat, w.chat.gen
        picker = w.open_themes()
        picker.list.setCurrentItem(item(picker, "github-light"))
        assert w.theme_manager.current.key == "github-light"
        assert w.updatesEnabled()                                                # pintou de novo depois da troca
        picker.save_btn.click()
        assert w.current_page() is w.prs                                         # navegação intacta
        assert w.chat is chat and w.chat.gen == gen                              # Chat não reconectou
        from agent_bar import settings as S
        assert S.load().theme == "github-light"
    finally:
        w.theme_manager.apply(C.get(C.DEFAULT))
        w.theme_manager.close()
        from PySide6.QtCore import QCoreApplication
        w.close()
        w.shutdown()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_saved_theme_is_applied_when_the_window_opens(tmp_path, monkeypatch):
    from agent_bar import settings as S
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    s = S.load()
    s.theme = "graphite"
    S.save(s)
    monkeypatch.setattr(Q, "_MANAGER", None)
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(repos=["o/r"], gh=no_network_gh(), window_store=WS.WindowStateStore(tmp_path / "window.json"),
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    try:
        w.show()
        pump()
        assert w.theme_manager.current.key == "graphite" and "#8ab4f8" in w.styleSheet()
    finally:
        w.theme_manager.apply(C.get(C.DEFAULT))
        w.theme_manager.close()
        from PySide6.QtCore import QCoreApplication
        w.close()
        w.shutdown()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
