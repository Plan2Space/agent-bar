"""FR-03 (FRIENDLY-AUDIT): uma só verdade sobre permissões no Chat.

Antes, a mesma tela dizia quatro coisas: o texto de abertura ("Toda ferramenta que pedir permissão abre um diálogo"),
o aviso amarelo ("o Studio trocou para o modo que pede permissão"), o seletor Modo e o rodapé ("permissões: bypass
(sempre)", que era a verdade). Agora a frase sai de `permission_mode` e aparece uma vez só.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import permission_mode as PM  # noqa: E402
from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402

STUB = Path(__file__).with_name("fake_acp_agent.py")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
OTHER = {PM.BYPASS: PM.ASK, PM.ASK: PM.BYPASS}
NEVER_IN_BYPASS = ("abre um diálogo", "trocou para o modo que pede permissão")


def visible_labels(view) -> list[str]:
    return [w.text() for w in view.findChildren(QtWidgets.QLabel) if w.isVisibleTo(view) and w.text().strip()]


def about_permission(texts: list[str]) -> list[str]:
    keys = ("permiss", "aprova", "diálogo")
    return [t for t in texts if any(k in t.lower() for k in keys)]


@pytest.fixture(params=[PM.BYPASS, PM.ASK])
def view(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()
    stub = AgentProfile("stub", "Stub", (sys.executable, str(STUB)))
    v = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [stub], workspaces_fn=lambda: [tmp_path / "ws"],
                 permission_mode_fn=lambda: request.param)
    v.mode = request.param
    v.resize(1100, 640)
    v.show()
    yield v
    from PySide6.QtCore import QCoreApplication, QEvent
    v.shutdown()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def connected(v, **info) -> None:
    """O que o Chat faz quando o agente responde ao connect (sem subir agente)."""
    v._on_done(v.gen, "connect", {"session_id": "s1", **info}, None)              # noqa: SLF001
    _APP.processEvents()


def test_the_chat_says_one_permission_sentence_and_it_matches_the_mode(view):
    connected(view)
    said = about_permission(visible_labels(view))
    assert said == [PM.sentence(view.mode)], said
    other = PM.sentence(OTHER[view.mode])
    assert all(other not in t for t in visible_labels(view))


def test_a_forced_mode_never_contradicts_bypass(view):
    connected(view, mode_forced_from="bypassPermissions")
    text = "\n".join(visible_labels(view))
    if view.mode == PM.BYPASS:
        assert not any(s in text for s in NEVER_IN_BYPASS), text
        assert about_permission(visible_labels(view)) == [PM.sentence(PM.BYPASS)]
    else:
        assert PM.sentence(PM.ASK) in text


def test_the_sentences_are_plain_language():
    assert PM.sentence(PM.BYPASS) == ("O Studio aprova as ações dos agentes sozinho e registra tudo em Notificações. "
                                      "Mudar em Configurações → Geral.")
    assert PM.sentence(PM.ASK) == "Cada ação do agente pede sua permissão."
    assert "bypass" not in PM.sentence(PM.BYPASS).lower()
