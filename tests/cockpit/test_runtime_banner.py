"""UX-02: a divergência do agent-runtime aparece na janela principal (sem abrir outra janela), com os SHAs e o comando."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar import runtime_pin as rp  # noqa: E402
from tests.test_runtime_pin import OLD, PINNED, direct_url, pyproject, uv_lock  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def window(tmp_path, st):
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    stub = Path(__file__).with_name("fake_acp_agent.py")
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path / "x"], runtime_pin=st)

    def close():
        win.shutdown()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return win, close


def test_a_divergent_runtime_shows_a_selectable_banner_in_the_main_window(tmp_path):
    pyproject(tmp_path)
    uv_lock(tmp_path)
    st = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(OLD))
    win, close = window(tmp_path, st)
    try:
        b = win.runtime_banner
        assert b.isVisibleTo(win) and b.text().startswith("Runtime divergente:")
        assert OLD[:12] in b.text() and PINNED[:12] in b.text() and "force-reinstall" in b.text()
        from PySide6.QtCore import Qt
        assert b.wordWrap() and b.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        assert b.parentWidget() is win.centralWidget()                           # na janela principal, não em outra
    finally:
        close()


def test_a_matching_or_unknown_runtime_shows_no_banner(tmp_path):
    pyproject(tmp_path)
    uv_lock(tmp_path)
    for st in (rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(PINNED)),
               rp.check(tmp_path / "pyproject.toml", read_direct_url=lambda: None), None):
        win, close = window(tmp_path, st)
        try:
            assert not win.runtime_banner.isVisibleTo(win) and win.runtime_banner.text() == ""
        finally:
            close()
