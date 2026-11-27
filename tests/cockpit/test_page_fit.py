"""UX-16 run 6: no Windows real, com 1100 px a linha Agente/Workspace/Modo/Histórico do Chat cortava à direita e a aba
ganhava barra horizontal. Causa: os QComboBox usam a política padrão do Qt (ajustar ao conteúdo), então o rótulo comprido
do agente ("Executor Claude · Claude Code (ACP) — ferramentas do Claude Code · MCP não conferido") e o caminho do
workspace viravam a largura MÍNIMA da página. A página tem de caber na largura mínima suportada da janela (900) e em 1100.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from agent_bar.cockpit import window_state as WS  # noqa: E402

_APP = QApplication.instance() or QApplication([])
LONG_AGENT = "Claude Code (ACP) com um nome de agente bem comprido, como os que o app monta com papel e capacidades"


class NoSpawnClient:
    """Sem processo de agente: aqui só a casca é medida."""

    def __init__(self, *_a, **_kw):
        pass

    def connect(self, _session_id=None):
        return cf.Future()

    def close(self):
        f = cf.Future()
        f.set_result(None)
        return f


FONT_FILES = (r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")


@pytest.fixture
def real_font():
    """Offscreen no Windows não tem NENHUMA fonte (cada glifo vira uma caixa de 11 px): largura medida ali é ficção.
    Carrega uma fonte de sistema de verdade (a do Windows; DejaVu/Liberation no CI) e a devolve ao fim."""
    from PySide6.QtGui import QFont, QFontDatabase
    for path in FONT_FILES:
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                break
    else:
        pytest.skip("nenhuma fonte de sistema conhecida para medir largura de texto")
    old = _APP.font()
    _APP.setFont(QFont(QFontDatabase.applicationFontFamilies(fid)[0], 9))
    try:
        yield
    finally:
        _APP.setFont(old)
        QFontDatabase.removeApplicationFont(fid)


@pytest.fixture
def window(real_font, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    ws = tmp_path / "um-workspace-com-um-nome-bem-comprido-que-nao-pode-alargar-a-pagina-do-chat"
    ws.mkdir()
    win = CockpitWindow(repos=["o/r"], workspaces_fn=lambda: [ws], client_factory=NoSpawnClient,
                        window_store=WS.WindowStateStore(tmp_path / "window.json"),
                        profiles_fn=lambda: [AgentProfile("claude", LONG_AGENT, (sys.executable,))])
    win.show()
    try:
        yield win
    finally:
        win.close()
        win.shutdown()
        win.deleteLater()


def pump(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize("width", [WS.MIN_W, 1100])
def test_the_chat_page_fits_the_window_without_a_horizontal_scrollbar(window, width):
    window.resize(width, 640)
    window.tabs.setCurrentWidget(window.sessions)
    pump()
    page = window.sessions
    scroll = page.parentWidget().parentWidget()
    assert isinstance(scroll, QScrollArea)
    assert LONG_AGENT in window.chat.agent_box.currentText()
    assert page.minimumSizeHint().width() <= scroll.viewport().width(), (
        f"a página pede {page.minimumSizeHint().width()} px e a janela dá {scroll.viewport().width()} px")
    assert not scroll.horizontalScrollBar().isVisible()


def test_the_full_agent_and_workspace_names_stay_readable_in_the_tooltip(window):
    pump()
    assert LONG_AGENT in window.chat.agent_box.toolTip()
    assert "um-workspace-com-um-nome-bem-comprido" in window.chat.workspace_box.toolTip()
