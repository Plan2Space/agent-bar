"""UX-06 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): Configurações → Saúde do sistema e Diagnóstico dentro da janela principal.

"Claude conectado" e "Executor pronto" são duas linhas (conectividade não é capacidade); alerta de 5 000 caracteres não
aumenta a janela; o diagnóstico se consulta e se copia sem abrir outra janela."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bar.cockpit import health as H
from agent_bar.models import AgentStatus, Level, PrQueue, Snapshot
from agent_bar.runtime_pin import PinStatus


def snap(claude=Level.GREEN, gh=Level.GREEN, queue=None):
    return Snapshot(at="2026-11-23T20:00:00Z", claude=AgentStatus("Claude", claude, "logado", "claude auth status"),
                    github=AgentStatus("GitHub", gh, "autenticado", "gh auth status"),
                    pr_queue=queue or PrQueue(rows=[], synced="2026-11-23T20:00:00Z"))


def core(s=None, pin=None):
    return SimpleNamespace(snap=s, runtime_pin=pin or PinStatus(True, "a" * 40, "a" * 40, "agent-runtime aaaaaaaaaaaa confere"),
                           worker=SimpleNamespace(refresh=lambda force=False: None))


def by_name(rows):
    return {r.name: r for r in rows}


def test_claude_connected_and_executor_ready_are_two_separate_rows():
    rows = by_name(H.health_rows(core(snap()), chat=SimpleNamespace(client=None)))
    assert rows["Claude conectado"].state == H.OK and "Claude: logado" in rows["Claude conectado"].lines[0]
    ex = rows["Executor pronto"]
    assert ex.state == H.WARN and ex.lines == ["não: nenhuma sessão do Chat aberta"]          # conectado ≠ pronto
    chat = SimpleNamespace(client=object(), status=SimpleNamespace(text=lambda: "conectado · Claude · sessão s-1"))
    ex = by_name(H.health_rows(core(snap()), chat=chat))["Executor pronto"]
    assert ex.state == H.OK and "ferramentas MCP: não conferidas aqui" in ex.lines                # nada afirmado sem prova


def test_each_row_carries_its_observed_state_never_a_default_ok():
    rows = by_name(H.health_rows(core(None, PinStatus(False, "b" * 40, "a" * 40, "agent-runtime desatualizado …"))))
    assert rows["agent-runtime"].state == H.BAD and "desatualizado" in rows["agent-runtime"].lines[0]
    for name in ("Claude conectado", "GitHub", "EventStore"):
        assert rows[name].state == H.UNKNOWN and "ainda não chegou" in rows[name].lines[0]
    rows = by_name(H.health_rows(core(snap(claude=Level.RED))))
    assert rows["Claude conectado"].state == H.BAD
    q = PrQueue(rows=[], error="gh: timeout")
    assert by_name(H.health_rows(core(snap(queue=q))))["GitHub"].lines[1] == "fila de PRs: leitura falhou (gh: timeout)"
    assert by_name(H.health_rows(core(snap(queue=q))))["GitHub"].state == H.WARN


def test_the_diagnostic_text_has_every_row_and_the_collection_snapshot():
    c = core(snap())
    text = H.diagnostic_text(c, H.health_rows(c))
    for name in ("agent-runtime", "P2S Studio", "Claude conectado", "Executor pronto", "GitHub", "EventStore"):
        assert f"] {name}" in text
    assert "P2S Studio snapshot 2026-11-23T20:00:00Z" in text


# --------------------------------------------------------------------------------------- na janela (offscreen)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    stub = Path(__file__).with_name("fake_acp_agent.py")
    refreshed = []
    c = core(snap())
    c.worker = SimpleNamespace(refresh=lambda force=False: refreshed.append(force))
    win = CockpitWindow(repos=["o/r"], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path], core=c)
    try:
        yield win, c, refreshed
    finally:
        win.shutdown()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def row_names(tab):
    return [w.property("health_name") for w in tab.health_box.findChildren(QtWidgets.QWidget, "health_row")]


def test_settings_is_a_tab_of_the_main_window_with_health_and_diagnostic(window):
    win, c, _ = window
    tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert tabs[-1] == "Configurações" and win.settings.core is c
    before = set(QtWidgets.QApplication.topLevelWidgets())
    win.tabs.setCurrentWidget(win.settings)
    win.settings.sections.setCurrentRow(1)                                          # Diagnóstico
    _APP.processEvents()
    assert set(QtWidgets.QApplication.topLevelWidgets()) - before == set()           # nenhuma janela nova
    assert row_names(win.settings)[:4] == ["agent-runtime", "P2S Studio", "Claude conectado", "Executor pronto"]
    assert "] Executor pronto" in win.settings.diag_text.toPlainText()


def test_a_5000_character_alert_does_not_grow_the_window(window):
    win, c, _ = window
    win.resize(1000, 700)
    win.show()
    win.tabs.setCurrentWidget(win.settings)
    _APP.processEvents()
    size = win.size()
    c.runtime_pin = PinStatus(False, "b" * 40, "a" * 40, "agent-runtime desatualizado: " + "x" * 5000)
    win.settings.render()
    for _ in range(10):
        _APP.processEvents()
    assert win.size() == size                                                        # a janela não cresceu
    label = next(lb for lb in win.settings.health_box.findChildren(QtWidgets.QLabel) if "xxxx" in lb.text())
    assert label.wordWrap() and label.width() <= win.settings.pages.width()


def test_copy_and_refresh_buttons(window):
    win, _c, refreshed = window
    QtWidgets.QApplication.clipboard().setText("")
    win.settings.copy_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == win.settings.diag_text.toPlainText()
    assert "copiado" in win.settings.diag_status.text()
    win.settings.refresh_btn.click()
    assert refreshed == [True]


def test_the_core_hands_itself_to_the_cockpit_it_opens(monkeypatch):
    import agent_bar.cockpit.ui.window as CW
    from agent_bar import core as C
    from agent_bar import settings as cfg
    got = {}

    class Fake:
        _closed = False

        def __init__(self, **kw):
            got.update(kw)
            self.chat = SimpleNamespace(ensure_client=lambda: None)

        def show(self): pass                                                       # noqa: E704

        def raise_(self): pass                                                     # noqa: E704

        def activateWindow(self): pass                                             # noqa: E704,N802
    monkeypatch.setattr(CW, "CockpitWindow", Fake)
    c = C.AppCore(cfg.Settings(), quit_fn=lambda: None, tray_available=lambda: False)
    c.open_cockpit()
    assert got["core"] is c
