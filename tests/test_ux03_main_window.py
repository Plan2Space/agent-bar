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


class NoSpawnClient:
    """Cliente ACP que NÃO cria processo. Estes testes conferem a casca (janela, bandeja, barra), não o agente.

    Causa do travamento do CI 3.11 no #116: o `open_cockpit` aquece o agente (`chat.ensure_client()`), e o fixture
    criava de verdade o processo do agente de teste numa thread asyncio enquanto o teste bombeava eventos do Qt. No
    Windows/3.11, com a captura de saída do pytest, isso travou a suíte (dump do faulthandler: a thread do ACP em
    `_spawn_and_initialize`, o teste em `processEvents`). O pedido de conexão continua conferido, sem processo."""
    made: list = []

    def __init__(self, argv, cwd, **_kw):
        NoSpawnClient.made.append(list(argv))
        self.connects = 0

    def connect(self, _session_id=None):
        import concurrent.futures as cf
        self.connects += 1
        return cf.Future()                                                     # nunca conclui: nenhum agente aqui

    def close(self):
        import concurrent.futures as cf
        f = cf.Future()
        f.set_result(None)
        return f


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
        win = CockpitWindow(repos=["o/r"], workspaces_fn=lambda: [tmp_path / "ws"], client_factory=NoSpawnClient,
                            profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))])
        real = win.shutdown
        win.shutdown = lambda: (shut.append(1), real())[1]
        return win
    quits: list = []
    core = AppCore(cfg.Settings(local_interval_s=2), quit_fn=lambda: quits.append(1), tray_available=lambda: True,
                   cockpit_factory=factory)
    NoSpawnClient.made.clear()
    import threading
    threads_before = set(threading.enumerate())
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
        _assert_no_thread_left(threads_before)


def _assert_no_thread_left(threads_before, timeout=5.0):
    """Guarda: depois do `core.shutdown()`, nenhuma thread criada pelo teste continua viva (coletor, runtime do ACP)."""
    import threading
    import time
    end = time.monotonic() + timeout
    while True:
        alive = [t for t in threading.enumerate() if t not in threads_before and t.is_alive()]
        if not alive or time.monotonic() > end:
            break
        _APP.processEvents()
        time.sleep(0.05)
    assert alive == [], f"threads vivas depois do shutdown: {[t.name for t in alive]}"


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
    assert NoSpawnClient.made and core.cockpit.chat.client.connects == 1       # o agente é aquecido, sem processo aqui


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


def test_when_the_old_version_does_not_close_the_message_points_to_sair_not_to_the_bar(monkeypatch):
    """§2.4: com a barra desligada por padrão, "Feche-a pelo menu da barra" não leva a lugar nenhum."""
    import time as T
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox
    seen: list = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda _p, _t, text, *a, **k: seen.append(text)))
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(T, "monotonic", lambda: next(clock))
    monkeypatch.setattr(T, "sleep", lambda _s: None)
    inst = SimpleNamespace(running_info=lambda: {"version": "a" * 40}, newer_available=lambda: True, version="b" * 40,
                           send=lambda _m: None, acquire=lambda **_k: False)
    ok = A._replace_older_instance(SimpleNamespace(processEvents=lambda: None), inst, None, None,  # noqa: SLF001
                                   logging.getLogger("t"))
    assert ok is False and seen and "Sair do P2S Studio" in seen[0] and "menu da barra" not in seen[0]
