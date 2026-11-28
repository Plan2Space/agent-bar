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


@pytest.fixture(autouse=True)
def isolated_cockpit_data(tmp_path, monkeypatch):
    """Nenhum teste do cockpit le ou grava o %LOCALAPPDATA% real (historico, relatorios, pacotes)."""
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "cockpit-data"))      # o teste pode sobrescrever
    yield


@pytest.fixture(autouse=True)
def no_exception_in_qt_slots(monkeypatch):
    """Excecao levantada dentro de slot Qt so vai para o stderr e o teste passaria verde (foi assim que o `closed
    database` do #75 escapou). Aqui ela reprova o teste. O excepthook so recebe o que o PySide reporta de slot."""
    import sys
    caught: list[str] = []

    def hook(etype, value, tb):
        import traceback
        caught.append("".join(traceback.format_exception(etype, value, tb)))
    monkeypatch.setattr(sys, "excepthook", hook)
    yield caught
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:                                                  # o que ainda estava na fila tambem conta
            end = time.monotonic() + 0.2
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.01)
    except ImportError:
        pass
    assert caught == [], "excecao em slot Qt durante o teste:\n" + "\n".join(caught)


@pytest.fixture(autouse=True)
def permission_mode_ask_unless_the_test_says_otherwise(monkeypatch):
    """O produto abre em bypass (decisão do Felipe, 2026-11-24). Os testes do fluxo de PERGUNTAR (cartão, diálogo,
    Permissões, Notificações) continuam valendo para o modo "pedir", que segue existindo como opção; por isso fixam
    "ask" aqui. Os testes do bypass trocam o modo explicitamente (tests/cockpit/test_permission_bypass.py)."""
    from agent_bar.cockpit import permission_mode as PM
    monkeypatch.setenv(PM.ENV, PM.ASK)


@pytest.fixture(autouse=True)
def no_real_ollama_listing(monkeypatch):
    """O seletor do Chat lista os modelos instalados do Ollama (GET /api/tags). Nos testes nada sai para a rede: lista
    vazia, a menos que o teste injete a sua."""
    from agent_bar.cockpit import profiles as prof
    monkeypatch.setattr(prof, "_installed_ollama_models", lambda: [])


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Nenhum teste do cockpit lê o settings.json REAL (%APPDATA%\agent_bar). Achado de 2026-11-28: com o tema salvo do
    Felipe (github-dark) a suíte quebrava em dezenas de testes de janela que nada têm a ver com tema."""
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings" / "settings.json"))


@pytest.fixture(autouse=True)
def no_real_design_folders(tmp_path, monkeypatch):
    """F2: a página "Últimas" do Design lê as pastas do motor e da evidência. Nos testes, uma pasta vazia (o teste que
    precisa de arquivos injeta os seus)."""
    monkeypatch.setenv("AGENT_BAR_DESIGN_ROOTS", str(tmp_path / "design-roots-vazio"))
