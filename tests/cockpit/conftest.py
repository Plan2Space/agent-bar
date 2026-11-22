"""Guardas de vazamento do cockpit: todo teste termina sem processo de agente vivo e sem widget do cockpit vivo.

Um widget sem pai que sobrevive ao QApplication, ou um subprocesso/transporte aberto no fim do interpretador, derruba
o processo do pytest com codigo != 0 mesmo com todos os testes verdes (foi o vermelho do CI no #24 @ e8621ed).
"""
from __future__ import annotations

import gc
import time

import pytest

from agent_bar.cockpit import acp


@pytest.fixture(autouse=True)
def no_leaked_agents():
    yield
    deadline = time.monotonic() + 10
    while acp.live_clients() and time.monotonic() < deadline:
        time.sleep(0.05)
    leaked = [(c.pid, c.argv[-1]) for c in acp.live_clients()]
    assert leaked == [], f"processo de agente ACP vivo depois do teste: {leaked}"


@pytest.fixture(autouse=True)
def no_leaked_cockpit_widgets():
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    alive = [type(w).__name__ for w in app.topLevelWidgets() if type(w).__module__.startswith("agent_bar.cockpit")]
    assert alive == [], f"widget do cockpit vivo depois do teste: {alive}"
