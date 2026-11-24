"""Permissões dos agentes em bypass por padrão e sempre (decisão do Felipe, 2026-11-24, literal: "CARA E ESSE NEGOCIO
DE PERMITIR SO UMA VEZ NAO EXISTE DEIXA AS PERMISSOES DOS AGENTS BYPASS SEMPRE").

- sessão nova do Claude ACP abre em bypassPermissions, sem o diálogo de modo arriscado;
- pedido de permissão que ainda chegar é aprovado sem cartão nem diálogo, e REGISTRADO (Inbox: decidido por "bypass";
  Notificações: item informativo, não ACTION_REQUIRED);
- o Chat e a Saúde dizem "permissões: bypass (sempre)";
- Configurações → Geral volta a pedir (desligado por padrão);
- operação irreversível (código digitado) continua pedindo até o Felipe decidir sobre ela.
Agente ACP de teste, sem rede."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import permission_mode as PM  # noqa: E402
from agent_bar.cockpit.profiles import AgentProfile  # noqa: E402
from agent_bar.cockpit.store import ChatStore  # noqa: E402
from agent_bar.cockpit.ui import widgets as W  # noqa: E402
from agent_bar.cockpit.ui.chat import ChatView  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")


def test_the_product_default_is_bypass_and_it_is_reversible(tmp_path, monkeypatch):
    monkeypatch.delenv(PM.ENV, raising=False)
    p = tmp_path / "permission_mode.json"
    assert PM.current(p) == PM.BYPASS                                              # sem arquivo: bypass
    PM.set_mode(PM.ASK, p)
    assert PM.current(p) == PM.ASK
    PM.set_mode(PM.BYPASS, p)
    assert PM.current(p) == PM.BYPASS and PM.label(PM.BYPASS) == "permissões: bypass (sempre)"
    p.write_text("{quebrado", encoding="utf-8")
    assert PM.current(p) == PM.BYPASS


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def view(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv(PM.ENV, PM.BYPASS)
    asked, modes = [], []
    (tmp_path / "ws").mkdir()
    v = ChatView(ChatStore(tmp_path / "data"), ask_permission=None,
                 confirm_mode=lambda name, parent: modes.append(name) or False,
                 profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                 workspaces_fn=lambda: [tmp_path / "ws"])
    yield v, asked, modes
    from PySide6.QtCore import QCoreApplication, QEvent
    v.shutdown()
    v.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def agent_texts(v):
    return [m["text"] for m in v.chat.messages if m["role"] == "agent"]


def test_a_permission_request_is_allowed_without_card_or_dialog_and_recorded(view):
    v, _asked, _ = view
    v.send("perm")
    assert wait(lambda: not v.busy and agent_texts(v))
    assert agent_texts(v) == ["outcome=selected:allow"]                          # o agente recebeu o allow
    assert v.transcript.findChildren(W.PermissionCard) == []                     # nenhum cartão para clicar
    entries = v.inbox.history()
    assert len(entries) == 1 and entries[0].status == "ALLOWED" and entries[0].decided_by == "bypass"
    assert any("aprovada automaticamente (bypass)" in n.text() for n in v.transcript.findChildren(W.Notice))


def test_in_bypass_an_irreversible_command_runs_without_the_code_and_is_recorded_as_irreversible(view):
    """Felipe, sobre o código do irreversível: "Bypass that too"."""
    v, _asked, _ = view
    v.send("permrm")
    assert wait(lambda: not v.busy and agent_texts(v))
    assert agent_texts(v) == ["outcome=selected:allow"]
    assert v.transcript.findChildren(W.PermissionCard) == []
    e = v.inbox.history()[0]
    assert (e.policy, e.status, e.decided_by) == ("IRREVERSIBLE", "ALLOWED", "bypass")
    assert any("IRREVERSÍVEL executada sem confirmação" in n.text() for n in v.transcript.findChildren(W.Notice))
    from agent_bar.cockpit.notifications import NotificationCenter
    center = NotificationCenter(":memory:")
    try:
        center.sync_permissions(v.inbox)
        titles = [n.title for n in center.list()]
        assert any(t.startswith("Irreversível executado sem confirmação") for t in titles), titles
    finally:
        center.close()


def test_ask_mode_brings_back_the_irreversible_code(view, monkeypatch):
    v, _asked, _ = view
    monkeypatch.setenv(PM.ENV, PM.ASK)
    v.send("permrm")
    assert wait(lambda: bool(v.transcript.findChildren(W.PermissionCard)))
    e = v.inbox.history()[0]
    assert (e.policy, e.status) == ("IRREVERSIBLE", "PENDING")
    assert not any("selected:allow" in t for t in agent_texts(v))
    v.stop()
    wait(lambda: not v.busy)


def test_only_human_or_the_bypass_may_allow_an_irreversible_request(tmp_path):
    from agent_bar.cockpit.permission_inbox import PermissionInbox
    inbox = PermissionInbox(tmp_path / "p.sqlite")
    try:
        tc = {"toolCallId": "t1", "title": "Run rm", "kind": "execute", "rawInput": {"command": "rm -rf build"}}
        e = inbox.record(tc, provider="stub", session_id="s", workspace=str(tmp_path))
        assert e.policy == "IRREVERSIBLE"
        with pytest.raises(PermissionError):
            inbox.decide(e.id, "allow_once", by="policy")                          # nenhuma outra automação
        assert inbox.decide(e.id, "allow_once", by="bypass").status == "ALLOWED"
    finally:
        inbox.close()


def test_a_new_acp_session_opens_in_bypass_without_the_risky_mode_dialog(view):
    v, _asked, modes = view
    v.send("echo 1")
    assert wait(lambda: not v.busy and agent_texts(v))
    assert v.client.modes.get("currentModeId") == "bypassPermissions"
    assert modes == []                                                            # nenhum diálogo de modo arriscado
    assert PM.label(PM.BYPASS) in v.status.text() or PM.label(PM.BYPASS) in v.permission_label.text()


def test_ask_mode_brings_back_the_card(view, monkeypatch):
    v, _asked, _ = view
    monkeypatch.setenv(PM.ENV, PM.ASK)
    v.send("perm")
    assert wait(lambda: bool(v.transcript.findChildren(W.PermissionCard)))
    assert v.inbox.history()[0].status == "PENDING"
    assert v.permission_label.text() == PM.label(PM.ASK)
    v.stop()
    wait(lambda: not v.busy)


def test_settings_general_turns_asking_back_on_off_by_default(tmp_path, monkeypatch):
    from agent_bar.cockpit.ui.settings_tab import SettingsTab
    monkeypatch.delenv(PM.ENV, raising=False)
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    tab = SettingsTab()
    try:
        box = tab.ask_toggle
        assert box.text().startswith("Pedir permissão") and not box.isChecked()
        box.click()
        assert PM.current() == PM.ASK
        box.click()
        assert PM.current() == PM.BYPASS
    finally:
        tab.deleteLater()


def test_health_says_the_permission_mode(monkeypatch):
    from agent_bar.cockpit import health as H
    monkeypatch.setenv(PM.ENV, PM.BYPASS)
    rows = {r.name: r for r in H.health_rows(None)}
    assert rows["Permissões dos agentes"].lines[0] == "permissões: bypass (sempre)"


def test_if_the_audit_record_fails_the_request_goes_to_felipe_instead_of_being_allowed(view, monkeypatch):
    v, _asked, _ = view

    def broken(*_a, **_k):
        raise RuntimeError("disco cheio")
    monkeypatch.setattr(v.inbox, "decide", broken)
    v.send("perm")
    assert wait(lambda: bool(v.transcript.findChildren(W.PermissionCard)))         # sem registro, sem bypass
    assert not any("selected:allow" in t for t in agent_texts(v))
    v.stop()
    wait(lambda: not v.busy)
