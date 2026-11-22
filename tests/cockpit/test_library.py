"""F4: biblioteca injetavel (descoberta, frontmatter, contexto truncado, preview = texto enviado)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import library as lib

STUB = Path(__file__).with_name("fake_acp_agent.py")


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def tree(tmp_path):
    hub, ws, root = tmp_path / "hub", tmp_path / "ws", tmp_path / "root"
    _w(hub / "skills" / "spec-author" / "SKILL.md",
       "---\nname: spec-author\ndescription: >-\n  Escreve SPEC\n  testavel.\n---\n\n# corpo da skill\n")
    _w(hub / "skills" / "broken" / "SKILL.md", "sem frontmatter aqui\n")
    _w(ws / ".claude" / "agents" / "revisor.md", "---\nname: revisor\ndescription: \"revisa PR\"\n---\ncorpo agente\n")
    _w(ws / ".claude" / "rules" / "testing.md", "# Testing\n- piramide\n")
    _w(ws / ".claude" / "skills" / "local" / "SKILL.md", "---\ndescription: falta o name\n---\n")
    _w(root / ".claude" / "skills" / "handoff" / "SKILL.md", "---\nname: handoff\ndescription: passa o bastao\n---\nx\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "x"]):
        subprocess.run(cmd, cwd=hub, env=env, check=True, capture_output=True)
    return hub, ws, root


def test_frontmatter_folded_and_quoted():
    meta, body = lib.parse_frontmatter("---\nname: a\ndescription: >-\n  linha um\n  linha dois\nx: \"y\"\n---\ncorpo")
    assert meta == {"name": "a", "description": "linha um linha dois", "x": "y"} and body == "corpo"
    with pytest.raises(ValueError):
        lib.parse_frontmatter("sem")
    with pytest.raises(ValueError):
        lib.parse_frontmatter("---\ndescription: x\n---\n")


def test_discover_sources_kinds_sha_and_warnings(tree):
    hub, ws, root = tree
    found = lib.discover(ws, hub=hub, root=root)
    got = sorted((i.source, i.kind, i.name) for i in found.items)
    assert got == [("hub", "skill", "spec-author"), ("raiz", "skill", "handoff"),
                   ("workspace", "agent", "revisor"), ("workspace", "rule", "testing")]
    hub_item = next(i for i in found.items if i.source == "hub")
    assert len(hub_item.sha) == 40 and hub_item.description == "Escreve SPEC testavel."
    assert any("broken" in w for w in found.warnings) and any("local" in w for w in found.warnings)


def test_missing_hub_is_a_warning_not_a_crash(tmp_path):
    found = lib.discover(None, hub=tmp_path / "nope", root=tmp_path / "nope2")
    assert found.items == [] and "hub de skills indisponível" in found.warnings[0]


def test_context_has_labeled_blocks_and_is_truncated_with_marker(tree, tmp_path):
    hub, ws, root = tree
    found = lib.discover(ws, hub=hub, root=root)
    ctx = lib.build_context(found.items)
    assert "### [skill] spec-author (origem: hub@" in ctx and "# corpo da skill" in ctx
    big = _w(tmp_path / "big" / ".claude" / "rules" / "big.md", "x" * 30_000)
    item = lib.LibraryItem("rule", "big", "", big, "workspace")
    t = lib.build_context([item])
    assert len(t) <= lib.CONTEXT_LIMIT + 80 and t.rstrip().endswith("caracteres omitidos]")
    assert lib.build_context([]) == ""
    assert lib.compose_prompt("", "oi") == "oi"
    assert lib.compose_prompt("CTX", "oi") == "CTX" + lib.SEPARATOR + "oi"


# --------------------------------------------------------------------------------------------------------- GUI


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    return qtw.QApplication.instance() or qtw.QApplication([])


def wait(app, cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def dispose(*ws):
    from PySide6.QtCore import QCoreApplication, QEvent
    for w in ws:
        w.shutdown()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tab_preview_is_exactly_the_text_sent_to_the_agent(tree, tmp_path, monkeypatch):
    app = _app()
    hub, ws, root = tree
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.store import ChatStore
    from agent_bar.cockpit.ui.chat import ChatView
    from agent_bar.cockpit.ui.library_tab import LibraryTab
    holder: dict = {}
    view = ChatView(ChatStore(tmp_path / "data"), profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                    workspaces_fn=lambda: [ws], context_provider=lambda: holder["tab"].context())
    tab = LibraryTab(view.workspace, discover_fn=lambda w: lib.discover(w, hub=hub, root=root))
    holder["tab"] = tab
    try:
        tab.reload()
        assert wait(app, lambda: tab.loaded_for == str(ws))
        keys = [i.key for i in tab.library.items if i.name in ("spec-author", "testing")]
        tab.select_keys(keys)
        preview = tab.preview.toPlainText()
        assert "spec-author" in preview and "piramide" in preview and "revisor" not in preview
        assert "2 item(ns)" in tab.count.text()
        view.send("ECHO-PROMPT revise isto")
        assert wait(app, lambda: not view.busy)
        echoed = [m["text"] for m in view.chat.messages if m["role"] == "agent"][0]
        assert echoed == lib.compose_prompt(preview, "ECHO-PROMPT revise isto")
        user = [m for m in view.chat.messages if m["role"] == "user"][0]
        assert user["text"] == "ECHO-PROMPT revise isto" and user["context_chars"] == len(preview)
        tab.clear()
        view.send("ECHO-PROMPT sem contexto")
        assert wait(app, lambda: not view.busy)
        assert [m["text"] for m in view.chat.messages if m["role"] == "agent"][-1] == "ECHO-PROMPT sem contexto"
    finally:
        dispose(view, tab)


def test_selection_survives_reload_and_truncation_is_flagged(tree, tmp_path):
    app = _app()
    hub, ws, root = tree
    _w(ws / ".claude" / "rules" / "huge.md", "y" * 25_000)
    from agent_bar.cockpit.ui.library_tab import LibraryTab
    tab = LibraryTab(lambda: str(ws), discover_fn=lambda w: lib.discover(w, hub=hub, root=root))
    try:
        tab.reload()
        assert wait(app, lambda: tab.loaded_for == str(ws))
        huge = next(i.key for i in tab.library.items if i.name == "huge")
        tab.select_keys([huge])
        assert "TRUNCADO" in tab.count.text()
        tab.reload()
        assert wait(app, lambda: tab.req and tab.loaded_for == str(ws) and tab.selected == [huge], 10)
        assert "aviso:" in tab.warn.text()
    finally:
        dispose(tab)
