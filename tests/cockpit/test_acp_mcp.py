"""UX-12 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9), parte técnica: o Chat declara ao agente ACP os servidores MCP do
`acp_mcp.json` do app no `session/new`; sem o arquivo nada é declarado e a UI diz "MCP indisponível". Sem rede."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import acp_mcp as M
from agent_bar.cockpit.acp import AcpClient

STUB = Path(__file__).with_name("fake_acp_agent.py")
ENGINE = {"name": "p2s-engine", "command": "C:/py/python.exe", "args": ["-m", "p2s_engine.mcp.server"],
          "env": [{"name": "PYTHONPATH", "value": "E:/engine/src"}]}
T = 15


def write(tmp_path, data) -> Path:
    p = tmp_path / M.CONFIG_NAME
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


def test_without_the_file_nothing_is_declared_and_the_ui_says_unavailable(tmp_path):
    cfg = M.load(tmp_path / M.CONFIG_NAME)
    assert cfg.servers == [] and cfg.status_text() == f"MCP indisponível (sem {M.CONFIG_NAME})"


def test_a_valid_file_declares_the_servers_in_acp_format(tmp_path):
    cfg = M.load(write(tmp_path, [ENGINE]))
    assert cfg.servers == [ENGINE] and cfg.status_text() == "MCP declarado: p2s-engine"


@pytest.mark.parametrize("bad, why", [
    ("{nao é json", ""), ({"name": "x"}, "lista"), ([{"command": "x"}], "sem 'name'"), ([{"name": "x"}], "sem 'command'"),
    ([{**ENGINE, "args": "-m x"}], "'args'"), ([ENGINE, ENGINE], "repetidos"),
    ([{**ENGINE, "env": [{"name": "GH_TOKEN", "value": "s"}]}], "recusada"),
    ([{**ENGINE, "env": [{"name": "MY_API_KEY", "value": "s"}]}], "recusada"),
])
def test_a_broken_or_unsafe_file_declares_nothing_and_says_why(tmp_path, bad, why):
    cfg = M.load(write(tmp_path, bad))
    assert cfg.servers == [] and cfg.error and why in cfg.error
    assert cfg.status_text().startswith("MCP indisponível: ")
    assert "s\"" not in cfg.status_text()                                          # o valor do segredo não aparece


def test_a_claude_code_entry_converts_and_the_dropped_cwd_is_reported():
    entry = {"command": "C:/py/python.exe", "args": ["-m", "p2s_engine.mcp.server"], "cwd": "E:/engine",
             "env": {"PYTHONPATH": "E:/engine/src"}}
    server, notes = M.from_claude_entry("p2s-engine", entry)
    assert server == ENGINE and len(notes) == 1 and "cwd" in notes[0]


def test_the_acp_client_sends_the_declared_servers_in_session_new(tmp_path):
    chunks: list[str] = []
    c = AcpClient([sys.executable, str(STUB)], tmp_path, mcp_servers=[ENGINE],
                  on_update=lambda u: chunks.append(u.text) if u.kind == "agent_message_chunk" else None)
    try:
        c.connect(None).result(T)
        c.prompt("mcp").result(T)
        assert json.loads("".join(chunks)) == [ENGINE]
    finally:
        c.close().result(T)


def test_by_default_the_client_still_declares_no_server(tmp_path):
    chunks: list[str] = []
    c = AcpClient([sys.executable, str(STUB)], tmp_path,
                  on_update=lambda u: chunks.append(u.text) if u.kind == "agent_message_chunk" else None)
    try:
        c.connect(None).result(T)
        c.prompt("mcp").result(T)
        assert json.loads("".join(chunks)) == []
    finally:
        c.close().result(T)


# ------------------------------------------------------------------------------------------------- Chat (offscreen)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(cond, timeout=T):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def chat(tmp_path, monkeypatch, with_file: bool):
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(data))
    if with_file:
        write(data, [ENGINE])
    (tmp_path / "ws").mkdir()
    return ChatView(ChatStore(data), profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                    workspaces_fn=lambda: [tmp_path / "ws"])


def dispose(v):
    from PySide6.QtCore import QCoreApplication, QEvent
    v.shutdown()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("with_file", [True, False], ids=["com-acp_mcp", "sem-arquivo"])
def test_the_chat_declares_the_apps_config_and_says_so(tmp_path, monkeypatch, with_file):
    v = chat(tmp_path, monkeypatch, with_file)
    try:
        v.send("mcp")
        assert wait(lambda: not v.busy and any(m["role"] == "agent" for m in v.chat.messages))
        got = json.loads(next(m["text"] for m in v.chat.messages if m["role"] == "agent"))
        if with_file:
            assert got == [ENGINE] and v.mcp_label.text() == "MCP declarado: p2s-engine"
        else:
            assert got == [] and v.mcp_label.text() == f"MCP indisponível (sem {M.CONFIG_NAME})"
    finally:
        dispose(v)
