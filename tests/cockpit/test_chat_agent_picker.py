"""Seletor de agente do Chat: só agentes reais e utilizáveis, com nome simples (pedido do Felipe, 2026-11-28).

"Por que não coloca só o Claude, o Ollama e o outro que tiver?" — a lista padrão é "Claude", "Ollama · <modelo>" (um por
modelo instalado) e "Codex" se estiver configurado. Executor de amostra, demonstração sem LLM e os rótulos de papel
("Executor", "Conversa", "sem ferramenta", "amostra") saem da lista padrão; ficam só no modo avançado
(AGENT_BAR_COCKPIT_ADVANCED_AGENTS=1). A capacidade de ferramenta do Ollama é decidida por baixo, não pelo usuário.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import profiles as prof  # noqa: E402

TECHNICAL = ("executor", "conversa", "ferramenta", "amostra", "demonstra", "sem llm", "acp", "(local)")
MODELS = ["llama3.1:8b", "qwen2.5-coder:7b"]


@pytest.fixture
def machine(monkeypatch):
    """A máquina do Felipe: Claude instalado, Ollama com dois modelos, sem Codex; nada sai para a rede."""
    monkeypatch.delenv("AGENT_BAR_COCKPIT_ADVANCED_AGENTS", raising=False)
    monkeypatch.delenv("AGENT_BAR_COCKPIT_AGENT", raising=False)
    monkeypatch.setattr(prof, "_installed_ollama_models", lambda: list(MODELS))
    monkeypatch.setattr(prof.shutil, "which", lambda exe: f"C:/bin/{exe}.cmd" if exe == "claude-agent-acp" else None)
    return monkeypatch


def test_the_default_list_is_claude_and_one_ollama_entry_per_installed_model(machine):
    labels = [p.label for p in prof.profiles()]
    assert labels == ["Claude", "Ollama · llama3.1:8b", "Ollama · qwen2.5-coder:7b"]
    assert all(not any(t in lbl.lower() for t in TECHNICAL) for lbl in labels), labels
    keys = [p.key for p in prof.profiles()]
    assert len(set(keys)) == len(keys)                                        # sem duplicata


def test_codex_appears_only_when_it_is_installed(machine):
    machine.setattr(prof.shutil, "which", lambda exe: f"C:/bin/{exe}.cmd" if exe in ("claude-agent-acp", "codex-acp") else None)
    assert [p.label for p in prof.profiles()][-1] == "Codex"


def test_ollama_off_means_no_ollama_entry_and_claude_stays_even_when_missing(machine):
    machine.setattr(prof, "_installed_ollama_models", lambda: [])
    machine.setattr(prof.shutil, "which", lambda exe: None)
    ps = prof.profiles()
    assert [p.label for p in ps] == ["Claude"] and ps[0].available()[0] is False


def test_sample_executor_and_demo_only_in_advanced_mode(machine):
    assert not [p for p in prof.profiles() if p.adapter == "ollama-executor" or p.demo]
    machine.setenv("AGENT_BAR_COCKPIT_ADVANCED_AGENTS", "1")
    extra = [p for p in prof.profiles() if p.advanced]
    assert {p.key for p in extra} == {"ollama-executor", "demo"}


def test_the_chat_picker_shows_the_simple_names_and_nothing_else(machine, tmp_path):
    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    app = QApplication.instance() or QApplication([])
    v = ChatView(ChatStore(tmp_path / "data"), workspaces_fn=lambda: [tmp_path])
    try:
        shown = [v.agent_box.itemText(i) for i in range(v.agent_box.count())]
        assert shown == ["Claude", "Ollama · llama3.1:8b", "Ollama · qwen2.5-coder:7b"]
        from PySide6.QtCore import Qt
        assert "ferramentas do Claude Code" in v.agent_box.itemData(0, Qt.ItemDataRole.ToolTipRole)  # detalhe: na dica
        app.processEvents()
    finally:
        v.shutdown()
        v.deleteLater()


class _Adapter:
    """Adaptador falso: `tools` diz se o modelo anuncia ferramentas."""
    adapter_id = "ollama"

    def __init__(self, tools: bool, tool_host=None):
        self.tools, self.tool_host, self.requests = tools, tool_host, []

    def connect(self):
        pass

    def create_session(self, request):
        from agent_bar.platform.adapter import CapabilityUnsupported
        self.requests.append(request.model_id)
        if self.tool_host is not None and not self.tools:
            raise CapabilityUnsupported("tool_calls", f"{request.model_id} nao anuncia tools")
        return {"session_id": "s-1", "model_accepted": request.model_id}

    def list_models(self):
        return []

    def close(self):
        pass


def test_an_ollama_entry_uses_tools_when_the_model_has_them_and_falls_back_to_plain_chat_otherwise():
    from agent_bar.cockpit.provider_client import ProviderClient
    with_tools = ProviderClient(_Adapter(True, tool_host=object()), model_id="llama3.1:8b",
                                fallback=lambda: _Adapter(True))
    with_tools.connect().result(timeout=5)
    assert with_tools.adapter.tool_host is not None and with_tools.adapter.requests == ["llama3.1:8b"]
    plain = _Adapter(False)
    no_tools = ProviderClient(_Adapter(False, tool_host=object()), model_id="qwen2.5-coder:7b", fallback=lambda: plain)
    no_tools.connect().result(timeout=5)
    assert no_tools.adapter is plain and plain.requests == ["qwen2.5-coder:7b"]  # mesmo modelo, sem ferramenta
    for c in (with_tools, no_tools):
        c._pool.shutdown(wait=True)
