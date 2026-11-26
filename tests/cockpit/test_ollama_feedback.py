"""Bug do Felipe (2026-11-26): "tentei usar o ollama no agent bar e ele nem responde". Medido pelo coordenador: com a GPU
ocupada, o 1º turno levou 102 s (83 s só carregando o modelo) e o Chat ficou parado, sem sinal; o modelo padrão era o
PRIMEIRO do /api/tags (deepseek-r1-tools:14b, 9 GB, thinking), não uma preferência.

Contrato: (1) estado visível desde o envio, com fase e tempo; (2) modelo padrão = preferência explícita e estável;
(3) timeout/erro do Ollama vira motivo legível; (4) modelo parcialmente na CPU (/api/ps) vira aviso. Sem rede."""
from __future__ import annotations

import concurrent.futures as cf
import io
import json
import os
import socket
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit.acp import Update  # noqa: E402
from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.platform.adapter import PromptFailed  # noqa: E402
from agent_bar.platform.ollama import OllamaAdapter  # noqa: E402

_APP = QApplication.instance() or QApplication([])


# ------------------------------------------------------------------------------------------- adaptador (sem Qt)
class Opener:
    """Servidor Ollama falso: /api/version, /api/tags (deepseek PRIMEIRO), /api/show, /api/ps e /api/chat."""

    def __init__(self, ps=None, chat=None):
        self.ps = ps or {"models": []}
        self.chat = chat

    def __call__(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/api/version"):
            return io.BytesIO(json.dumps({"version": "0.40.18"}).encode())
        if url.endswith("/api/tags"):
            return io.BytesIO(json.dumps({"models": [
                {"name": "deepseek-r1-tools:14b", "digest": "d" * 64, "size": 9_000_000_000},
                {"name": "llama3.1:8b", "digest": "l" * 64, "size": 4_900_000_000}]}).encode())
        if url.endswith("/api/show"):
            return io.BytesIO(json.dumps({"capabilities": ["completion"], "model_info": {}}).encode())
        if url.endswith("/api/ps"):
            return io.BytesIO(json.dumps(self.ps).encode())
        if url.endswith("/api/chat"):
            return self.chat()
        raise AssertionError(url)


def test_the_default_model_is_the_explicit_preference_not_the_tags_order(monkeypatch):
    from agent_bar.cockpit.ui import chat as C
    made = {}

    class Rec(OllamaAdapter):
        def __init__(self, endpoint, **kw):
            made.update(kw)
            super().__init__(endpoint, **kw)
    monkeypatch.setattr("agent_bar.platform.ollama.OllamaAdapter", Rec)
    monkeypatch.delenv("AGENT_BAR_OLLAMA_MODEL", raising=False)
    C._default_provider_client(AgentProfile("ollama", "Ollama (local)", ("ollama",), adapter="ollama"), "ws")
    assert made["preferred_model"] == "llama3.1:8b"                                   # preferência estável
    monkeypatch.setenv("AGENT_BAR_OLLAMA_MODEL", "qwen2.5:7b")
    C._default_provider_client(AgentProfile("ollama", "Ollama (local)", ("ollama",), adapter="ollama"), "ws")
    assert made["preferred_model"] == "qwen2.5:7b"                                    # configurável
    a = OllamaAdapter("http://x", opener=Opener(), preferred_model="llama3.1:8b")
    a.connect()
    assert a._default_model() == "llama3.1:8b"                                       # nunca o 1º do /api/tags


def test_a_stream_that_sends_nothing_becomes_a_readable_reason():
    class Silent:
        def __iter__(self):
            raise socket.timeout("timed out")

        def close(self):
            pass
    a = OllamaAdapter("http://x", opener=Opener(chat=Silent))
    a.connect()
    with pytest.raises(PromptFailed) as e:
        a._stream_once({"cancel": __import__("threading").Event(), "resp": None},
                       {"model": "llama3.1:8b", "messages": []}, lambda _ev: None)
    msg = str(e.value)
    assert "não mandou resposta" in msg and "carregando" in msg and "GPU" in msg       # o motivo provável, legível


def test_residency_says_how_much_of_the_model_is_on_the_cpu():
    ps = {"models": [{"name": "llama3.1:8b", "model": "llama3.1:8b", "size": 6_000_000_000, "size_vram": 3_600_000_000}]}
    a = OllamaAdapter("http://x", opener=Opener(ps=ps))
    assert a.cpu_share("llama3.1:8b") == pytest.approx(0.4)
    assert a.cpu_share("deepseek-r1-tools:14b") is None                               # não carregado: não dá para saber
    full = OllamaAdapter("http://x", opener=Opener(ps={"models": [{"name": "llama3.1:8b", "size": 5, "size_vram": 5}]}))
    assert full.cpu_share("llama3.1:8b") == 0.0


# ---------------------------------------------------------------------------------------------------- no Chat
class SlowProvider:
    """Provedor que só responde quando o teste manda: o Chat precisa dar sinal enquanto isso."""

    def __init__(self, profile, ws, on_update=None, on_exit=None, **_kw):
        self.on_update = on_update
        self.pending: cf.Future | None = None
        self.model = "llama3.1:8b"
        self.share = None

    def connect(self, _sid=None):
        f = cf.Future()
        f.set_result({"agent": {}, "session_id": "o-1", "modes": {}, "config_options": [
            {"id": "model", "name": "Modelo", "category": "model", "type": "select", "currentValue": self.model,
             "options": [{"value": self.model, "name": self.model}]}]})
        return f

    @property
    def config_options(self):
        return [{"id": "model", "name": "Modelo", "category": "model", "type": "select", "currentValue": self.model,
                 "options": [{"value": self.model, "name": self.model}]}]

    def prompt(self, _text):
        self.pending = cf.Future()
        return self.pending

    def cpu_share(self, _model):
        return self.share

    def cancel(self):
        pass

    def close(self):
        f = cf.Future()
        f.set_result(None)
        return f


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    (tmp_path / "ws").mkdir()
    made: list = []

    def factory(p, ws, **kw):
        made.append(SlowProvider(p, ws, **kw))
        return made[-1]
    v = ChatView(ChatStore(tmp_path / "data"), provider_factory=factory,
                 profiles_fn=lambda: [AgentProfile("ollama", "Ollama (local)", ("python",), adapter="ollama")],
                 workspaces_fn=lambda: [tmp_path / "ws"])
    yield v, made
    v.shutdown()
    v.deleteLater()


def pump(cond=lambda: False, secs=3.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not cond():
        _APP.processEvents()
        time.sleep(0.02)
    return cond()


def test_the_chat_shows_a_live_state_from_the_send_with_phase_and_elapsed_time(chat):
    v, made = chat
    t0 = time.monotonic()
    v.send("diga oi")
    assert pump(lambda: "carregando" in v.status.text(), 2.0), v.status.text()     # sinal em menos de 2 s
    assert time.monotonic() - t0 < 2.0
    assert pump(lambda: "llama3.1:8b" in v.status.text(), 2.0), v.status.text()      # o modelo em uso aparece
    assert pump(lambda: "1 s" in v.status.text() or "2 s" in v.status.text(), 3.5)   # o tempo corre
    sid = "o-1"
    made[-1].on_update(Update("agent_thought_chunk", sid, {"content": {"type": "text", "text": "hmm"}}))
    assert pump(lambda: "pensando" in v.status.text())
    made[-1].on_update(Update("agent_message_chunk", sid, {"content": {"type": "text", "text": "oi"}}))
    assert pump(lambda: "respondendo" in v.status.text())
    made[-1].pending.set_result("end_turn")
    assert pump(lambda: not v.busy)
    assert "carregando" not in v.status.text() and "pensando" not in v.status.text()


def test_an_ollama_failure_is_a_readable_reason_in_the_chat(chat):
    v, made = chat
    v.send("diga oi")
    assert pump(lambda: made and made[-1].pending is not None)
    made[-1].pending.set_exception(PromptFailed("o Ollama não mandou resposta em 300 s: o modelo pode estar carregando "
                                                "(GPU ocupada por outro programa?)"))
    assert pump(lambda: not v.busy)
    text = v.banner_label.text()
    assert "Ollama" in text and "carregando" in text and "[error] falha" not in text


def test_a_model_partly_on_the_cpu_is_announced_after_the_turn(chat):
    from agent_bar.cockpit.ui.widgets import Notice
    v, made = chat
    v.send("diga oi")
    assert pump(lambda: made and made[-1].pending is not None)
    made[-1].share = 0.4
    made[-1].pending.set_result("end_turn")
    assert pump(lambda: not v.busy)
    assert pump(lambda: any("40%" in n.text() and "CPU" in n.text() for n in v.transcript.findChildren(Notice)))


def test_an_http_500_from_chat_becomes_a_readable_reason_with_the_error_body():
    """Caso do auditor (25/11): o Ollama não conseguiu iniciar o modelo na GPU e devolveu HTTP 500 depois de 2 min 40 s;
    a conversa ficou sem resposta. O 500 vira motivo legível com o corpo resumido."""
    import urllib.error

    def boom():
        body = io.BytesIO(json.dumps({"error": "llama runner process has terminated: GPU discovery watchdog timed out"
                                      " " + "x" * 400}).encode())
        raise urllib.error.HTTPError("http://x/api/chat", 500, "Internal Server Error", {}, body)
    a = OllamaAdapter("http://x", opener=Opener(chat=boom))
    a.connect()
    with pytest.raises(PromptFailed) as e:
        a._stream_once({"cancel": __import__("threading").Event(), "resp": None},
                       {"model": "llama3.1:8b", "messages": []}, lambda _ev: None)
    msg = str(e.value)
    assert "o Ollama devolveu erro HTTP 500" in msg and "GPU discovery watchdog timed out" in msg
    assert "não conseguiu iniciar o modelo" in msg and len(msg) < 400                 # resumido, com o motivo provável


def test_a_failed_turn_stops_the_live_state(chat):
    v, made = chat
    v.send("diga oi")
    assert pump(lambda: "carregando" in v.status.text())
    made[-1].pending.set_exception(PromptFailed("o Ollama devolveu erro HTTP 500: GPU discovery watchdog timed out"))
    assert pump(lambda: not v.busy)
    before = v.status.text()
    pump(secs=1.6)                                                                     # o relógio não segue contando
    assert v.status.text() == before and "carregando" not in before
    assert "HTTP 500" in v.banner_label.text() and "watchdog" in v.banner_label.text()
