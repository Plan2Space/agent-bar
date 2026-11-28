"""FR-02 (FRIENDLY-AUDIT): sem pasta de projeto, o Chat orienta em vez de dar erro.

Antes: numa máquina sem `E:\\Claude\\apps` (ou com `AGENT_BAR_COCKPIT_WORKSPACES` apontando para pasta que não existe), o
Chat abria com a faixa vermelha "[error] falha. escolha um workspace", o botão "Reconectar" (que não resolve), o status
"erro: error" e a aba terminando em "· ?". O caminho certo ficava escondido no fim do combo. Agora: um estado vazio com
"Escolha a pasta do seu projeto para começar" e o botão "Escolher pasta…", que abre o seletor e lembra a escolha.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import profiles as prof  # noqa: E402
from agent_bar.cockpit.acp import AcpClient  # noqa: E402
from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402
from agent_bar.cockpit.ui.chat_sessions import ChatSessions  # noqa: E402

STUB = Path(__file__).with_name("fake_acp_agent.py")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def dispose(view) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    view.shutdown()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def no_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("AGENT_BAR_COCKPIT_WORKSPACES", str(tmp_path / "nao-existe"))
    return tmp_path


def make(tmp_path, chooser=lambda parent: ""):
    made: list[AcpClient] = []

    def factory(*a, **kw):
        made.append(AcpClient(*a, **kw))
        return made[-1]
    stub = AgentProfile("stub", "Stub", (sys.executable, str(STUB)))
    view = ChatView(ChatStore(tmp_path / "data"), client_factory=factory, profiles_fn=lambda: [stub],
                    workspaces_fn=prof.workspaces, choose_folder=chooser)
    return view, made


def test_without_a_project_folder_the_chat_asks_for_one_instead_of_showing_an_error(no_folder):
    assert prof.workspaces() == []                                           # a pasta da variável não existe
    view, made = make(no_folder)
    try:
        view.ensure_client()                                                 # o que o envio e a "Nova conversa" fazem
        assert view.banner.isHidden(), view.banner_label.text()
        assert not view.folder_empty.isHidden() and not view.choose_folder_btn.isHidden()
        assert view.choose_folder_btn.text() == "Escolher pasta…"
        assert "Escolha a pasta do seu projeto para começar" in view.folder_empty_label.text()
        assert "erro" not in view.status.text().lower()
        assert not ChatSessions._title(view).endswith("· ?")                 # noqa: SLF001
        assert made == []                                                    # nada é conectado sem pasta
    finally:
        dispose(view)


def test_the_chosen_folder_is_used_and_remembered_by_a_new_chat_view(no_folder):
    folder = no_folder / "meu-projeto"
    folder.mkdir()
    view, made = make(no_folder, chooser=lambda parent: str(folder))
    try:
        view.choose_folder_btn.click()
        assert view.workspace() == str(folder.resolve())
        assert view.folder_empty.isHidden()
        assert len(made) == 1                                                # conectou na pasta escolhida
    finally:
        dispose(view)
    again, _ = make(no_folder)
    try:
        assert again.workspace() == str(folder.resolve())                    # reaparece selecionada num ChatView novo
        assert again.folder_empty.isHidden()
    finally:
        dispose(again)


def test_cancelling_the_chooser_keeps_the_empty_state(no_folder):
    view, made = make(no_folder, chooser=lambda parent: "")
    try:
        view.choose_folder_btn.click()
        assert view.workspace() == "" and not view.folder_empty.isHidden() and made == []
    finally:
        dispose(view)


def test_a_remembered_folder_that_was_deleted_is_not_offered(no_folder):
    from agent_bar.cockpit import project_folders as PF
    gone = no_folder / "apagada"
    gone.mkdir()
    PF.remember(no_folder / "data" / "preferences.json", gone)
    gone.rmdir()
    view, _ = make(no_folder)
    try:
        assert view.workspace() == "" and not view.folder_empty.isHidden()
    finally:
        dispose(view)


def test_the_default_apps_folder_is_only_a_suggestion_when_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BAR_COCKPIT_WORKSPACES", raising=False)
    assert prof.workspaces(tmp_path / "sem-apps") == []
    (tmp_path / "apps" / "repo" / ".git").mkdir(parents=True)
    assert prof.workspaces(tmp_path / "apps") == [(tmp_path / "apps" / "repo").resolve()]
