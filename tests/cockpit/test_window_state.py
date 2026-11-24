"""UX-04 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): a janela principal reabre onde estava, com o tamanho e o estado de antes;
monitor que sumiu → volta para um monitor visível; nunca maior que a área útil nem menor que 900×600."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_bar.cockpit import window_state as WS
from agent_bar.geometry import Rect

MAIN = Rect(0, 0, 2560, 1400)                                  # principal (área útil, sem a barra de tarefas)
LEFT = Rect(-1920, 0, 1920, 1040)                              # monitor externo à esquerda
LAPTOP = Rect(0, 0, 1280, 760)                                  # tela pequena


def test_no_saved_state_opens_centered_on_the_primary_at_the_default_size():
    st = WS.restore(None, [MAIN, LEFT])
    assert (st.w, st.h) == (WS.DEFAULT_W, WS.DEFAULT_H) and not st.maximized
    assert st.x == (MAIN.width - st.w) // 2


def test_the_saved_place_size_and_maximized_state_come_back():
    saved = WS.WindowState(300, 200, 1600, 1000, maximized=True)
    assert WS.restore(saved, [MAIN, LEFT]) == saved


def test_a_window_on_a_monitor_that_was_disconnected_comes_back_to_the_primary():
    on_left = WS.WindowState(-1700, 50, 1500, 900)
    assert WS.restore(on_left, [MAIN, LEFT]) == on_left                          # monitor ainda está lá: fica
    back = WS.restore(on_left, [MAIN])                                            # desconectado
    assert MAIN.overlap(back.x, back.y, back.w, back.h) == back.w * back.h       # inteira no principal
    assert (back.w, back.h) == (WS.DEFAULT_W, WS.DEFAULT_H)


def test_a_window_mostly_off_screen_after_a_resolution_change_is_pulled_back_in():
    st = WS.restore(WS.WindowState(2300, 1200, 1400, 900), [MAIN])
    assert MAIN.overlap(st.x, st.y, st.w, st.h + WS.FRAME_H) == st.w * (st.h + WS.FRAME_H)


def test_on_a_small_screen_the_window_fits_the_work_area_and_never_goes_below_the_minimum():
    st = WS.restore(WS.WindowState(0, 0, 2400, 1300), [LAPTOP])
    assert st.w <= LAPTOP.width and st.h + WS.FRAME_H <= LAPTOP.height               # moldura inteira visível
    assert st.y >= LAPTOP.top                                                    # barra de título acessível
    tiny = WS.restore(WS.WindowState(10, 10, 300, 200), [MAIN])
    assert (tiny.w, tiny.h) == (WS.MIN_W, WS.MIN_H)


def test_the_store_survives_a_missing_or_broken_file(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    assert store.load() is None
    (tmp_path / "window.json").write_text("{nao é json", encoding="utf-8")
    assert store.load() is None
    store.save(WS.WindowState(1, 2, 1000, 700, True))
    assert store.load() == WS.WindowState(1, 2, 1000, 700, True)


# ------------------------------------------------------------------------------------- a janela de verdade (offscreen)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def screens(monkeypatch):
    import agent_bar.cockpit.ui.window as CW
    monkeypatch.setattr(CW, "screen_rects", lambda: [MAIN])                     # o monitor da máquina do Felipe


def cockpit(tmp_path, store):
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    stub = Path(__file__).with_name("fake_acp_agent.py")
    return CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                         workspaces_fn=lambda: [tmp_path], window_store=store)


def dispose(win):
    from PySide6.QtCore import QCoreApplication, QEvent
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_close_and_reopen_keeps_size_and_place(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    win = cockpit(tmp_path, store)
    try:
        assert win.minimumWidth() == WS.MIN_W and win.minimumHeight() == WS.MIN_H
        win.show()
        win.resize(1100, 760)
        win.move(120, 90)
        _APP.processEvents()
        win.close()                                                               # fechar salva
    finally:
        dispose(win)
    saved = store.load()
    assert saved is not None and (saved.w, saved.h) == (1100, 760) and (saved.x, saved.y) == (120, 90)
    again = cockpit(tmp_path, store)
    try:
        assert (again.width(), again.height()) == (1100, 760) and (again.x(), again.y()) == (120, 90)
    finally:
        again.shutdown()
        dispose(again)


def test_a_maximized_window_reopens_maximized_with_its_normal_size_kept(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    store.save(WS.WindowState(100, 80, 1000, 700, maximized=False))
    win = cockpit(tmp_path, store)
    try:
        win.show()
        win.showMaximized()
        _APP.processEvents()
        win.close()
    finally:
        dispose(win)
    saved = store.load()
    assert saved.maximized and (saved.w, saved.h) == (1000, 700)                  # o tamanho normal não se perde
    again = cockpit(tmp_path, store)
    try:
        assert again.windowState() & again.windowState().WindowMaximized
    finally:
        again.shutdown()
        dispose(again)


def test_at_the_minimum_size_the_tabs_and_the_chat_input_stay_inside_the_window(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    win = cockpit(tmp_path, store)
    try:
        win.show()
        win.resize(WS.MIN_W, WS.MIN_H)
        for _ in range(10):
            _APP.processEvents()
        assert (win.width(), win.height()) == (WS.MIN_W, WS.MIN_H)
        inside = win.rect()
        tb = win.tabs.tabBar()                                                    # a barra de abas: sempre inteira
        assert inside.contains(tb.mapTo(win, tb.rect().topLeft())) and inside.contains(tb.mapTo(win, tb.rect().topRight()))
        # O Chat: na altura, sempre dentro. Na largura, a escolha (#103): abaixo do mínimo da página ela ROLA, não
        # espreme. No Windows o Chat pede 676 px e cabe inteiro em 900; o offscreen infla as fontes (~1154 px).
        page = win.tabs.currentWidget()
        fits = page.minimumSizeHint().width() <= win.tabs.width()
        for w in (win.chat.input, win.chat.send_btn if hasattr(win.chat, "send_btn") else win.chat.input):
            top_left = w.mapTo(win, w.rect().topLeft())
            bottom_right = w.mapTo(win, w.rect().bottomRight())
            assert 0 <= top_left.y() and bottom_right.y() < inside.height(), (type(w).__name__, top_left, bottom_right)
            if fits:
                assert inside.contains(top_left) and inside.contains(bottom_right), (type(w).__name__, bottom_right)
    finally:
        win.shutdown()
        dispose(win)


def test_the_whole_frame_including_the_title_bar_stays_above_the_taskbar():
    st = WS.restore(WS.WindowState(100, 600, 1200, 780), [MAIN])                # conteúdo cabe; com a barra de título não
    assert st.y + 40 + st.h <= MAIN.bottom                                       # 40 = barra de título (literal)


def test_below_a_tabs_content_minimum_the_window_scrolls_instead_of_squeezing(tmp_path):
    """A Revisão visual pede mais que 900×600: a janela encolhe, mas o conteúdo não é espremido (rola)."""
    store = WS.WindowStateStore(tmp_path / "window.json")
    win = cockpit(tmp_path, store)
    try:
        win.show()
        win.resize(WS.MIN_W, WS.MIN_H)
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            for _ in range(5):
                _APP.processEvents()
            page = win.tabs.currentWidget()
            assert page.height() >= page.minimumSizeHint().height(), win.tabs.tabText(i)   # nunca espremida na altura
            assert win.tabs.width() <= win.width()                                     # a barra de abas cabe na janela
        assert (win.width(), win.height()) == (WS.MIN_W, WS.MIN_H)                    # a janela encolheu de verdade
    finally:
        win.shutdown()
        dispose(win)


# ------------------------------------------------------- revisões de 8e80079 e 8220fb7 (SHOULD/NIT carregados)
SMALL = Rect(0, 0, 1093, 574)                     # 1366×768 a 125 % menos a barra de tarefas (probe do revisor)


@pytest.mark.parametrize("raw", ['{"x": Infinity, "y": 0, "w": 900, "h": 600}', '{"x": 1e400, "y": 0, "w": 900, "h": 600}',
                                 '{"x": 0, "y": 0, "w": -Infinity, "h": 600}', '{"x": NaN, "y": 0, "w": 900, "h": 600}'])
def test_a_non_finite_geometry_never_stops_the_cockpit_from_opening(tmp_path, raw):
    (tmp_path / "window.json").write_text(raw, encoding="utf-8")
    assert WS.WindowStateStore(tmp_path / "window.json").load() is None


def test_on_a_small_work_area_the_minimum_gives_way_and_the_frame_stays_above_the_taskbar():
    mw, mh = WS.min_size(SMALL)
    assert mh + 40 <= SMALL.height and mw <= SMALL.width                         # 40 = barra de título (literal)
    st = WS.restore(WS.WindowState(0, 0, 1440, 900), [SMALL])
    assert st.y >= SMALL.top and st.y + 40 + st.h <= SMALL.bottom
    assert WS.min_size(MAIN) == (WS.MIN_W, WS.MIN_H)                             # tela normal: o mínimo é 900×600


def test_the_frame_height_rule_is_locked_by_a_fixed_number():
    assert WS.FRAME_H == 40
    st = WS.restore(WS.WindowState(100, 600, 1200, 780), [MAIN])
    assert st.y + 40 + st.h <= MAIN.bottom


def test_on_a_small_screen_the_real_window_minimum_fits(tmp_path, monkeypatch):
    import agent_bar.cockpit.ui.window as CW
    monkeypatch.setattr(CW, "screen_rects", lambda: [SMALL])
    win = cockpit(tmp_path, WS.WindowStateStore(tmp_path / "window.json"))
    try:
        assert win.minimumHeight() + 40 <= SMALL.height and win.minimumWidth() <= SMALL.width
    finally:
        win.shutdown()
        dispose(win)


def test_maximize_keeps_the_saved_position_and_restore_after_reopening_comes_back(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    store.save(WS.WindowState(100, 80, 1000, 700, maximized=False))
    win = cockpit(tmp_path, store)
    try:
        win.show()
        win.showMaximized()
        _APP.processEvents()
        win.close()
    finally:
        dispose(win)
    saved = store.load()
    assert saved.maximized and (saved.x, saved.y, saved.w, saved.h) == (100, 80, 1000, 700)
    again = cockpit(tmp_path, store)
    try:
        again.show()
        _APP.processEvents()
        again.showNormal()
        for _ in range(5):
            _APP.processEvents()
        assert (again.x(), again.y(), again.width(), again.height()) == (100, 80, 1000, 700)
    finally:
        again.shutdown()
        dispose(again)


def test_a_maximize_resize_recorded_as_normal_falls_back_to_the_previous_normal(tmp_path):
    """O Windows entrega o resize do maximizar ANTES da mudança de estado: o changeEvent volta ao normal anterior."""
    from PySide6.QtCore import QEvent
    win = cockpit(tmp_path, WS.WindowStateStore(tmp_path / "window.json"))
    try:
        win.show()
        win.showMaximized()
        _APP.processEvents()
        win._prev_normal = (100, 80, 1000, 700)                                    # noqa: SLF001
        win._normal = (0, 0, win.width(), win.height())                            # noqa: SLF001 (o resize do maximizar)
        win.changeEvent(QEvent(QEvent.Type.WindowStateChange))
        assert win._normal == (100, 80, 1000, 700)                                 # noqa: SLF001
    finally:
        win.shutdown()
        dispose(win)


def test_below_a_tabs_minimum_width_the_window_scrolls_instead_of_squeezing(tmp_path):
    win = cockpit(tmp_path, WS.WindowStateStore(tmp_path / "window.json"))
    try:
        win.show()
        win.resize(WS.MIN_W, WS.MIN_H)
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            for _ in range(5):
                _APP.processEvents()
            page = win.tabs.currentWidget()
            assert page.width() >= page.minimumSizeHint().width(), win.tabs.tabText(i)   # nunca espremida na largura
    finally:
        win.shutdown()
        dispose(win)


def test_when_the_current_page_grows_the_window_refits_without_a_resize(tmp_path):
    from PySide6.QtWidgets import QWidget
    win = cockpit(tmp_path, WS.WindowStateStore(tmp_path / "window.json"))
    try:
        win.show()
        win.resize(WS.MIN_W, WS.MIN_H)
        win.tabs.setCurrentWidget(win.review)
        for _ in range(5):
            _APP.processEvents()
        tall = QWidget()
        tall.setMinimumHeight(win.review.minimumSizeHint().height() + 600)       # a galeria terminou de carregar
        win.review.layout().addWidget(tall)
        for _ in range(10):
            _APP.processEvents()
        page = win.tabs.currentWidget()
        assert page.height() >= page.minimumSizeHint().height()
    finally:
        win.shutdown()
        dispose(win)


def test_quitting_through_the_core_saves_the_window_too(tmp_path):
    store = WS.WindowStateStore(tmp_path / "window.json")
    win = cockpit(tmp_path, store)
    try:
        win.show()
        win.resize(1100, 760)
        win.move(130, 95)
        _APP.processEvents()
        win.shutdown()                                                             # "Encerrar" / troca de versão (#98)
        saved = store.load()
        assert saved is not None and (saved.x, saved.y, saved.w, saved.h) == (130, 95, 1100, 760)
    finally:
        dispose(win)
