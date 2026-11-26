"""Chat do Studio no perfil "Executor local (amostra)" (P2S-LOCAL-EXECUTOR-SKETCHUP-POC §7.1, §7.2, §7.5): o
`tool_call` do modelo vira o MESMO cartao de permissao do ACP (Permission Inbox + broker); permitir uma vez roda o motor
uma vez e o ToolCard mostra o run/SKP/inspecao gravados pelo motor; negar = zero motor; falha nunca aparece como pronto.
Ollama falso por HTTP de verdade; motor falso (o real e provado na demonstracao com SketchUp 2026)."""
from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.provider_client import ProviderClient  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui import widgets as W  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402
from agent_bar.platform.ollama import OllamaAdapter  # noqa: E402
from agent_bar.platform.sketchup_sample import ALLOWED_FIXTURES, TOOL_NAME, SketchUpSampleTool  # noqa: E402
from tests.platform.fake_ollama import FakeOllama  # noqa: E402
from tests.platform.test_sketchup_sample import FIXTURE, FakeEngine  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
PROFILE = AgentProfile("ollama-executor", "Executor local (amostra)", ("ollama",), adapter="ollama-executor")
CALL = {"id": "call_1", "function": {"index": 0, "name": TOOL_NAME, "arguments": {"fixture_ref": "room_001"}}}


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def engine_root(tmp_path):
    root = tmp_path / "apps" / "sketchup-mcp-next"
    (root / "src" / "p2s_engine").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='p2s-engine'\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "python.exe").write_bytes(b"")
    for rel in ALLOWED_FIXTURES.values():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(json.dumps(FIXTURE), encoding="utf-8")
    return root


@pytest.fixture
def server():
    with FakeOllama() as s:
        s.tool_script = {"calls": [CALL]}
        yield s


def build_view(server, tmp_path, monkeypatch, mode="ok"):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))   # nao vaza para outros testes
    (tmp_path / "ws").mkdir()
    root = engine_root(tmp_path)
    engine = FakeEngine(mode)
    tool = SketchUpSampleTool(root, root / "runs" / "local-executor", allowed_roots=(tmp_path / "apps",), runner=engine,
                              sketchup_busy=lambda: False, head=lambda _r: None, python=root / ".venv" / "python.exe")

    def factory(profile, ws, **kw):
        return ProviderClient(OllamaAdapter(server.endpoint, tool_host=tool), **kw)
    v = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [PROFILE], workspaces_fn=lambda: [tmp_path / "ws"],
                 provider_factory=factory)
    v.engine = engine
    return v


@pytest.fixture
def cleanup():
    views = []
    yield views
    from PySide6.QtCore import QCoreApplication, QEvent
    for v in views:
        v.shutdown()
        v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def ask_and_answer(view, choice):
    view.send("Crie uma amostra simples de cômodo no SketchUp com o modelo local")
    assert wait(lambda: bool(view.broker.open_ids())), "o cartao de permissao nao apareceu"
    eid = view.broker.open_ids()[0]
    entry = view.inbox.get(eid)
    assert entry.kind == "execute" and entry.provider == "ollama-executor"
    assert '"fixture_ref": "room_001"' in entry.operation and "expected_fixture_version" in entry.operation
    view.answer_permission(eid, choice)
    assert wait(lambda: not view.busy, 20), "turno nao terminou"
    return eid


def tool_card(view):
    cards = view.findChildren(W.ToolCard)                                     # o Chat solta `cards` no fim do turno
    assert len(cards) == 1
    return cards[0]


def test_allow_once_runs_the_engine_once_and_the_chat_shows_the_engine_result(server, tmp_path, cleanup, monkeypatch):
    view = build_view(server, tmp_path, monkeypatch)
    cleanup.append(view)
    eid = ask_and_answer(view, "allow_once")
    assert len(view.engine.calls) == 1
    card = tool_card(view)
    assert card.state["status"] == "completed"
    body = card.body.toPlainText()
    assert "run_id: lx-" in body and "skp_sha256:" in body and "inspecao: PASS" in body
    assert "veredito visual: PENDENTE" in body
    entry = view.inbox.get(eid)
    assert entry.status == "ALLOWED" and entry.decision == "allow_once" and entry.result == "completed"
    assert len([b for p, b in server.requests if p == "/api/chat"]) == 1         # desfecho escrito pelo Studio
    agent = [m for m in view.chat.messages if m["role"] == "agent"][-1]["text"]
    assert agent.startswith("Resultado do motor (escrito pelo Studio") and "SUCESSO" in agent and "run_id: lx-" in agent


def test_deny_runs_nothing_and_the_card_is_failed(server, tmp_path, cleanup, monkeypatch):
    view = build_view(server, tmp_path, monkeypatch)
    cleanup.append(view)
    eid = ask_and_answer(view, "deny")
    assert view.engine.calls == []
    assert tool_card(view).state["status"] == "failed"
    assert "PERMISSION_DENIED" in tool_card(view).body.toPlainText()
    assert view.inbox.get(eid).status == "DENIED"


def test_an_engine_failure_is_never_shown_as_ready(server, tmp_path, cleanup, monkeypatch):
    view = build_view(server, tmp_path, monkeypatch, mode="inspection_fail")
    cleanup.append(view)
    ask_and_answer(view, "allow_once")
    card = tool_card(view)
    assert card.state["status"] == "failed"
    assert "pronto" not in card.body.toPlainText() and "INSPECTION_FAILED" in card.body.toPlainText()
    agent = [m for m in view.chat.messages if m["role"] == "agent"][-1]["text"]
    assert "FALHOU" in agent and "SUCESSO" not in agent and "INSPECTION_FAILED" in agent


def test_health_does_not_say_the_local_executor_has_no_tools(server, tmp_path, cleanup, monkeypatch):
    from agent_bar.cockpit.health import NOT_EXECUTOR, executor_row
    view = build_view(server, tmp_path, monkeypatch)
    cleanup.append(view)
    ask_and_answer(view, "allow_once")
    row = executor_row(view)
    assert row.state == NOT_EXECUTOR                                         # nao e o Executor Claude
    text = " ".join(row.lines)
    assert "sem ferramentas" not in text and "build_room_sample" in text


def test_health_lists_the_tools_the_executor_actually_publishes():
    from types import SimpleNamespace

    from agent_bar.cockpit.health import NOT_EXECUTOR, executor_row
    host = SimpleNamespace(names=lambda: {"build_room_sample", "render_room_sample"})
    chat = SimpleNamespace(client=SimpleNamespace(adapter=SimpleNamespace(tool_host=host)), profile=lambda: PROFILE,
                           status=SimpleNamespace(text=lambda: "pronto"), tools_observed=set())
    row = executor_row(chat)
    text = " ".join(row.lines)
    assert row.state == NOT_EXECUTOR and "build_room_sample, render_room_sample" in text and "só build" not in text
    chat.client = None                                                       # antes de conectar: nada inventado
    assert "conferidas ao conectar" in " ".join(executor_row(chat).lines) or executor_row(chat).state != NOT_EXECUTOR
