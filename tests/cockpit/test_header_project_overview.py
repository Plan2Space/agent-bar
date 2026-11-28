"""Pedido do Felipe (2026-11-28): "de tudo q vc alterou nao mudou porra nenhuma?". A Central de acompanhamento
(open_project_overview, Frente D, #156/#219/#220) estava no main, mas nenhum botão a abria. Agora o cabeçalho da janela
tem "Visão geral", ao lado do sino, que abre a Central numa janela própria, não-modal (sem 5ª área, §6 do pedido).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")


def no_network_gh():
    from agent_bar.cockpit.github import GhClient, GhError

    class NoNetworkGh(GhClient):
        def _run(self, *a, **k):
            raise GhError("auth", "sem rede no teste")
    return NoNetworkGh(exe=["nao-usado"])


@pytest.fixture
def window(tmp_path, monkeypatch):
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(repos=["o/r"], gh=no_network_gh(), window_store=WS.WindowStateStore(tmp_path / "window.json"),
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    calls = []

    def collect():                                                          # nunca o gh de verdade no teste
        calls.append("leu")
        raise RuntimeError("sem rede no teste")
    w.overview_collect = collect
    w.collect_calls = calls
    w.show()
    yield w
    w.close()
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_header_has_a_visible_overview_button_next_to_the_bell(window):
    corner = window.tabs.cornerWidget(Qt.Corner.TopRightCorner)
    btn = window.overview_btn
    assert btn.parent() is corner and btn.isVisibleTo(window)
    assert btn.text() == "Visão geral" and btn.accessibleName() == "Visão geral do projeto"


def test_clicking_opens_the_project_overview_window_without_changing_the_page(window):
    page = window.current_page()
    window.overview_btn.click()
    dlg = window._overview
    try:
        assert dlg is not None and dlg.isVisible() and not dlg.isModal()
        assert dlg.windowTitle().startswith("Acompanhamento do projeto")
        assert window.current_page() is page                                  # não vira uma 5ª área
        end = time.monotonic() + 5
        while time.monotonic() < end and not window.collect_calls:
            _APP.processEvents()
            time.sleep(0.01)
        assert window.collect_calls == ["leu"]                                 # a leitura já foi disparada
    finally:
        dlg.close()
