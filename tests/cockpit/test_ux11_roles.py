"""UX-11 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9; D-UX-4 emendada pela SPEC da PoC, es#81 §11): o Chat escolhe o agente
pelo PAPEL, com as capacidades verificadas à vista: **Conversa** (Ollama genérico, sem ferramentas), **Executor local
(amostra)** (uma ferramenta só: build_room_sample) e **Executor Claude** (Claude Code via ACP).

Critério: Ollama aparece com "sem ferramentas"; pedir SketchUp nele → recusa com o motivo e a oferta do Executor, sem
envio ao modelo como se pudesse. Nenhum texto afirma capacidade que o agente não tem (regra do coordenador: "Não resolva
com system prompt que afirme capacidade inexistente")."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import roles as R
from agent_bar.cockpit.profiles import AgentProfile

STUB = Path(__file__).with_name("fake_acp_agent.py")
CLAUDE = AgentProfile("claude", "Claude Code (ACP)", ("claude-agent-acp",))
OLLAMA = AgentProfile("ollama", "Ollama (local)", ("ollama",), adapter="ollama")
LOCAL = AgentProfile("ollama-executor", "Executor local (amostra)", ("ollama",), adapter="ollama-executor")
DEMO = AgentProfile("demo", "Demonstração (sem LLM)", ("python",), demo=True)
OTHER = AgentProfile("stub", "Stub", ("python",))


def test_each_profile_has_one_role():
    assert [R.role_of(p) for p in (CLAUDE, OLLAMA, LOCAL, DEMO)] == [R.EXECUTOR_CLAUDE, R.CONVERSA, R.EXECUTOR_LOCAL,
                                                                       R.CONVERSA]
    assert R.role_of(OTHER) == R.ACP_OTHER                                         # agente ACP sem capacidade conferida


def test_capabilities_say_only_what_was_verified():
    assert R.capabilities(OLLAMA) == "sem ferramentas"
    assert R.capabilities(DEMO) == "sem ferramentas (demonstração, sem LLM)"
    assert R.capabilities(LOCAL) == "ferramentas estreitas de amostra no SketchUp (conferidas ao conectar)"
    assert R.capabilities(CLAUDE) == "ferramentas do Claude Code · MCP não conferido"
    assert R.capabilities(CLAUDE, tools=["Read", "build_room"], mcp="p2s-engine conferido") == (
        "ferramentas do Claude Code · usadas nesta sessão: 2 · MCP: p2s-engine conferido")
    assert R.capabilities(OTHER) == "capacidades não conferidas"


def test_the_selector_label_shows_role_name_and_capabilities():
    assert R.label(OLLAMA) == "Conversa · Ollama (local) — sem ferramentas"
    assert R.label(LOCAL).startswith("Executor local (amostra) · ")
    assert R.label(CLAUDE).startswith("Executor Claude · Claude Code (ACP) — ")


@pytest.mark.parametrize("text", ["abre o SketchUp e monta a suíte 01", "renderiza a cama de novo",
                                  "gera o .skp da planta_74", "roda o build_room da suíte",
                                  "executa o render_room com a câmera r2_canto", "constrói o cômodo no SketchUp"])
def test_execution_requests_are_recognised(text):
    assert R.wants_execution(text)


@pytest.mark.parametrize("text", ["o que é o SketchUp?", "explica a diferença entre V-Ray e Enscape",
                                  "resume o último PR", "qual a regra de folga ergonômica?",
                                  "dá para abrir o SketchUp daqui?", "como eu renderizo a suíte 01?"])
def test_questions_about_the_tools_are_conversation(text):
    assert not R.wants_execution(text)


def test_the_refusal_names_the_reason_and_offers_the_available_executors():
    text = R.refusal(OLLAMA, [CLAUDE, LOCAL])
    assert "Ollama (local)" in text and "sem ferramentas" in text and "não executa" in text
    assert "Executor Claude" in text and "Executor local (amostra)" in text
    assert "Nada foi enviado" in text


# ---------------------------------------------------------------------------------------------- no Chat
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def chat(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()
    sent: list = []

    class Provider:                                                               # o "Ollama": registra o que receberia
        def __init__(self, p, ws, **_kw):
            self.p = p

        def connect(self, _sid=None):
            import concurrent.futures as cf
            f = cf.Future()
            f.set_result({"agent": {}, "session_id": "o-1", "modes": {}, "config_options": []})
            return f

        def prompt(self, text):
            import concurrent.futures as cf
            sent.append(text)
            f = cf.Future()
            f.set_result({"stopReason": "end_turn"})
            return f

        def close(self):
            import concurrent.futures as cf
            f = cf.Future()
            f.set_result(None)
            return f

    stub = AgentProfile("claude", "Claude Code (ACP)", (sys.executable, str(STUB)))
    ollama = AgentProfile("ollama", "Ollama (local)", ("python",), adapter="ollama")
    v = ChatView(ChatStore(tmp_path / "data"), provider_factory=Provider,
                 profiles_fn=lambda: [ollama, stub], workspaces_fn=lambda: [tmp_path / "ws"])
    yield v, sent, app
    v.shutdown()
    v.deleteLater()


def _pump(app, cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_the_agent_selector_lists_roles_with_capabilities(chat):
    v, _sent, _app = chat
    from PySide6.QtCore import Qt
    labels = [v.agent_box.itemText(i) for i in range(v.agent_box.count())]
    assert labels == ["Ollama (local)", "Claude Code (ACP)"]                     # seletor: só o nome (pedido do Felipe)
    tips = [v.agent_box.itemData(i, Qt.ItemDataRole.ToolTipRole) for i in range(v.agent_box.count())]
    assert tips[0] == "Conversa · Ollama (local) — sem ferramentas"              # papel e capacidade: na dica
    assert tips[1].startswith("Executor Claude · Claude Code (ACP)")


def test_asking_sketchup_in_conversation_is_refused_and_never_sent_to_the_model(chat):
    from agent_bar.cockpit.ui import widgets as W
    v, sent, app = chat
    v.agent_box.setCurrentIndex(0)                                                # Conversa (Ollama)
    v.send("abre o SketchUp e monta a suíte 01")
    _pump(app, lambda: False, timeout=0.3)
    assert sent == []                                                             # nada foi para o modelo
    assert not v.busy and v.input.toPlainText() == "abre o SketchUp e monta a suíte 01"   # o texto volta para a caixa
    offer = v.transcript.findChildren(W.ExecutorOffer)
    assert offer and "sem ferramentas" in offer[-1].text.text()
    assert [b.text() for b in offer[-1].buttons] == ["Usar Claude Code (ACP)", "Enviar só como conversa"]  # nome do agente


def test_the_offer_switches_to_the_executor_without_sending(chat):
    from agent_bar.cockpit.ui import widgets as W
    v, sent, app = chat
    v.agent_box.setCurrentIndex(0)
    v.send("renderiza a cama de novo")
    offer = v.transcript.findChildren(W.ExecutorOffer)[-1]
    offer.buttons[0].click()
    assert v.profile().key == "claude" and sent == []
    assert v.input.toPlainText() == "renderiza a cama de novo"                    # envio continua manual


def test_sending_as_conversation_is_an_explicit_choice(chat):
    from agent_bar.cockpit.ui import widgets as W
    v, sent, app = chat
    v.agent_box.setCurrentIndex(0)
    v.send("renderiza a cama de novo")
    offer = v.transcript.findChildren(W.ExecutorOffer)[-1]
    offer.buttons[-1].click()
    assert _pump(app, lambda: bool(sent))
    assert sent == ["renderiza a cama de novo"]                                   # o texto exato, sem promessa nenhuma


def test_a_question_goes_to_the_conversation_model(chat):
    v, sent, app = chat
    v.agent_box.setCurrentIndex(0)
    v.send("o que é o SketchUp?")
    assert _pump(app, lambda: bool(sent))
    assert sent == ["o que é o SketchUp?"]


def test_the_local_executor_lists_exactly_what_its_adapter_published():
    """Achado da PoC (LAB-16, agent-bar#142): com a fixture E3a + render_spec, o executor publica DUAS ferramentas; o texto
    fixo "uma ferramenta: build_room_sample" mentia. O seletor diz o que o adaptador publicou, e nada antes de conferir."""
    two = R.capabilities(LOCAL, published=["render_room_sample", "build_room_sample"])
    assert two == "ferramentas de amostra: build_room_sample, render_room_sample"
    assert R.capabilities(LOCAL, published=["build_room_sample"]) == "ferramentas de amostra: build_room_sample"
    assert R.capabilities(LOCAL, published=[]) == "nenhuma ferramenta publicada agora"
    assert "build_room_sample" in R.label(LOCAL, published=["build_room_sample"])


def test_the_selector_shows_the_published_tools_after_connecting(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    import concurrent.futures as cf

    from PySide6.QtWidgets import QApplication

    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()

    class Exec:
        def __init__(self, *_a, **_kw):
            pass

        def connect(self, _sid=None):
            f = cf.Future()
            f.set_result({"agent": {}, "session_id": "x-1", "modes": {}, "config_options": []})
            return f

        def published_tools(self):
            return ["build_room_sample", "render_room_sample"]

        def close(self):
            f = cf.Future()
            f.set_result(None)
            return f
    local = AgentProfile("ollama-executor", "Executor local (amostra)", ("python",), adapter="ollama-executor",
                         advanced=True)                                          # só existe no modo avançado
    monkeypatch.setattr(AgentProfile, "available", lambda self: (True, ""))
    v = ChatView(ChatStore(tmp_path / "data"), provider_factory=Exec, profiles_fn=lambda: [local],
                 workspaces_fn=lambda: [tmp_path / "ws"])
    try:
        assert "conferidas ao conectar" in v.agent_box.itemText(0)                   # antes: nada prometido
        v.ensure_client()
        assert _pump(app, lambda: "render_room_sample" in v.agent_box.itemText(0))
        assert v.agent_box.itemText(0) == ("Executor local (amostra) · Executor local (amostra) — "
                                           "ferramentas de amostra: build_room_sample, render_room_sample")
    finally:
        v.shutdown()
        v.deleteLater()
