"""Reabrir a CockpitWindow no mesmo processo com um tema não-padrão salvo não pode levantar.

SHOULD do revisor no #229 (2026-11-28): com o `settings.json` do Felipe em `github-dark`, a 2ª janela do processo
levantava `RuntimeError: Internal C++ object ... already deleted` em `ThemeManager.apply` — o gerenciador de tema é
um só por processo e ainda guardava os widgets (e os ouvintes) da 1ª janela, já destruída. Era a causa das ~1.000
quebras da suíte com o tema escuro salvo. No app, o mesmo acontece ao fechar e reabrir o Cockpit pela bandeja.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")


def _open(tmp_path: Path, n: int):
    import agent_bar.cockpit.ui.window as CW
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.github import GhClient
    from agent_bar.cockpit.profiles import AgentProfile
    core = SimpleNamespace(settings=SimpleNamespace(bar_enabled=False, notify_human=True), snap=None, runtime_pin=None,
                           worker=SimpleNamespace(refresh=lambda force=False: None), set_bar_enabled=lambda on: None)
    gh = GhClient(exe=[sys.executable, str(Path(__file__).with_name("fake_gh.py"))])
    w = CW.CockpitWindow(gh=gh, repos=["o/r"], workspaces_fn=lambda: [tmp_path / "ws"], core=core,
                         profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                         window_store=WS.WindowStateStore(tmp_path / f"window{n}.json"))
    w.show()
    _APP.processEvents()
    return w


def _close(w) -> None:
    w.shutdown()
    w.hide()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


@pytest.mark.parametrize("theme", ["github-dark", "dracula"])
def test_reopening_the_window_in_the_same_process_with_a_dark_theme_saved_does_not_raise(tmp_path, monkeypatch, theme):
    from agent_bar.cockpit.ui.theme import qt as Q
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": theme}), encoding="utf-8")
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(settings))
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(Q, "_MANAGER", None, raising=False)                  # um gerenciador novo, como num processo novo
    (tmp_path / "ws").mkdir()
    from agent_bar.cockpit.ui.theme import catalog as C
    first = _open(tmp_path, 1)
    assert first.theme_manager.current.key == theme
    _close(first)                                                             # 1ª janela destruída (bandeja: fechar)
    second = _open(tmp_path, 2)                                               # antes: RuntimeError em ThemeManager.apply
    try:
        assert second.theme_manager.current.key == theme
        other = next(k for k in ("github-light", "nord") if k != theme)
        second.theme_manager.apply(C.get(other))                               # trocar o tema com a 2ª janela aberta
        assert second.theme_manager.current.key == other
    finally:
        mgr = second.theme_manager
        _close(second)
        mgr.apply(C.get(C.DEFAULT))                                            # paleta/fonte do app voltam ao padrão
        mgr.close()
