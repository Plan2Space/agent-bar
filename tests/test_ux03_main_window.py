"""UX-03 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9, decisão D-UX-1 aprovada pelo Felipe: "Approve everything").

O atalho abre o Cockpit, que é a janela principal; a barra flutuante é opcional e começa desligada; a Central legada não
abre; fechar o Cockpit manda para a bandeja (os agentes e a coleta seguem) e só "Sair" encerra; a segunda abertura traz
o Cockpit existente para a frente; com a barra ligada, ela não fica por cima do Cockpit aberto.
"""
from __future__ import annotations

import gc
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar import app as A  # noqa: E402
from agent_bar import settings as cfg  # noqa: E402
from agent_bar.core import AppCore  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).parent / "cockpit" / "fake_acp_agent.py"


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Núcleo de verdade (sem rede: coleta trocada), Cockpit de verdade com o agente de teste, bandeja disponível."""
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    import agent_bar.core as C
    monkeypatch.setattr(C.col, "collect_snapshot", lambda **_kw: None)
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    (tmp_path / "ws").mkdir()
    shut: list = []

    def factory():
        win = CockpitWindow(repos=["o/r"], workspaces_fn=lambda: [tmp_path / "ws"],
                            profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))])
        real = win.shutdown
        win.shutdown = lambda: (shut.append(1), real())[1]
        return win
    quits: list = []
    core = AppCore(cfg.Settings(local_interval_s=2), quit_fn=lambda: quits.append(1), tray_available=lambda: True,
                   cockpit_factory=factory)
    core.start()
    before ={w for w in QApplication.topLevelWidgets() if w.isVisible()}
    try:
        yield core, quits, shut, before
    finally:
        core.shutdown()
        for w in QApplication.topLevelWidgets():
            if w not in before:
                w.hide()
                w.deleteLater()
        _APP.processEvents()


def new_windows(before) -> list:
    return [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.isWindow() and w not in before]


def test_the_floating_bar_is_off_by_default():
    assert cfg.Settings().bar_enabled is False


def test_the_shortcut_opens_only_the_main_window_the_cockpit(studio):
    core, _q, _s, before = studio
    bar = A.start_main(core)
    _APP.processEvents()
    assert bar is None and core.bar is None                                    # sem barra: nenhuma janela dela
    assert new_windows(before) == [core.cockpit]                               # uma janela principal só


def test_a_second_open_brings_the_same_cockpit_forward_and_never_the_central(studio):
    core, _q, _s, before = studio
    A.start_main(core)
    first = core.cockpit
    first.hide()                                                               # estava na bandeja
    A._bring_forward(None, logging.getLogger("t"), core)                       # noqa: SLF001
    _APP.processEvents()
    assert core.cockpit is first and first.isVisible() and new_windows(before) == [first]


def test_closing_the_cockpit_sends_it_to_the_tray_and_nothing_stops(studio):
    core, quits, shut, _b = studio
    A.start_main(core)
    win = core.cockpit
    win.close()
    _APP.processEvents()
    assert not win.isVisible() and shut == [] and quits == []                  # fechar ≠ encerrar
    assert core.worker.alive() and not core._closed                            # noqa: SLF001 - a coleta segue
    core.open_cockpit()
    assert core.cockpit is win and win.isVisible()                             # volta a mesma janela, com as sessões


def test_quit_from_the_tray_shuts_everything_down(studio):
    core, quits, shut, _b = studio
    A.start_main(core)
    acts = {a.text(): a for a in core.tray_menu.actions() if a.text()}
    assert set(acts) >= {"Abrir P2S Studio", "Barra flutuante (opcional)", "Sair do P2S Studio"}
    acts["Sair do P2S Studio"].trigger()
    assert shut == [1] and quits == [1] and not core.worker.alive()
    assert not core.cockpit.isVisible()                                        # nada fica na tela depois do "Sair"


def test_a_bar_that_exists_but_is_turned_off_stays_hidden_on_start(studio):
    core, _q, _s, before = studio
    core.bar = _bar(core)                                                      # criada antes (evidência), desligada
    A.start_main(core)
    _APP.processEvents()
    assert not core.bar.isVisible() and new_windows(before) == [core.cockpit]


def test_without_a_tray_closing_the_cockpit_quits_instead_of_hiding_it_forever(studio):
    core, quits, shut, _b = studio
    core.tray = None                                                           # sem bandeja não haveria caminho de volta
    A.start_main(core)
    core.cockpit.close()
    _APP.processEvents()
    assert shut == [1] and quits == [1]


def test_the_tray_opens_the_studio_and_turns_the_optional_bar_on_and_off(studio, tmp_path):
    core, quits, _s, _b = studio
    core.bar_factory = lambda: _bar(core)
    acts = {a.text(): a for a in core.tray_menu.actions() if a.text()}
    acts["Abrir P2S Studio"].trigger()
    assert core.cockpit.isVisible()
    acts["Barra flutuante (opcional)"].trigger()
    assert core.bar is not None and core.bar.isVisible() and core.settings.bar_enabled is True
    assert cfg.load(tmp_path / "settings.json").bar_enabled is True            # a escolha persiste
    acts["Barra flutuante (opcional)"].trigger()
    assert not core.bar.isVisible() and cfg.load(tmp_path / "settings.json").bar_enabled is False
    assert core.worker.alive() and quits == []                                 # ocultar a barra não para nada


def test_with_the_bar_on_it_never_sits_over_the_open_cockpit(studio, monkeypatch):
    core, _q, _s, _b = studio
    core.bar_factory = lambda: _bar(core)
    core.set_bar_enabled(True)
    A.start_main(core)
    bar, win = core.bar, core.cockpit
    win.setGeometry(bar.frameGeometry().adjusted(-200, -100, 200, 400))        # o Cockpit cobre o lugar da barra
    monkeypatch.setattr(type(win), "isActiveWindow", lambda self: True)
    bar._keep_on_top()                                                         # noqa: SLF001
    assert not bar.isVisible()                                                 # não se sobrepõe ao Cockpit aberto
    monkeypatch.setattr(type(win), "isActiveWindow", lambda self: False)       # o Felipe foi para outra janela
    bar._keep_on_top()                                                         # noqa: SLF001
    assert bar.isVisible()


def test_the_tray_menu_survives_garbage_collection(studio):
    core, _q, _s, _b = studio
    gc.collect()
    _APP.processEvents()
    assert "Abrir P2S Studio" in [a.text() for a in core.tray_menu.actions()]


def _bar(core):
    from agent_bar.window import Bar
    return Bar(core.settings, core=core)
