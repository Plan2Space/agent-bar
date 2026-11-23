"""F3 da SPEC de UX: Notification Center persistente — idempotente, acao pendente nunca vira lida, nada some,
espelho do Permission Inbox, filtros e retencao."""
from __future__ import annotations

import pytest

from agent_bar.cockpit.notifications import NotificationCenter
from agent_bar.cockpit.permission_inbox import PermissionInbox


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    path = tmp_path / "permissions.sqlite"
    inbox = PermissionInbox(path, clock=clock, ttl_s=60)
    nc = NotificationCenter(path, clock=clock)
    yield clock, inbox, nc, path
    nc.close()
    inbox.close()


def tc(title="Write a.py"):
    return {"toolCallId": "t", "title": title, "kind": "edit", "rawInput": {"file_path": "a.py"}}


def test_publishing_the_same_event_twice_updates_instead_of_duplicating(env):
    _, _, nc, _ = env
    a = nc.publish("TASK", "Fluxo concluído", dedupe_key="wf:1:done")
    b = nc.publish("TASK", "Fluxo concluído", dedupe_key="wf:1:done")
    assert a.id == b.id and len(nc.list()) == 1
    with pytest.raises(ValueError):
        nc.publish("SPAM", "x")


def test_a_permission_request_shows_as_action_required_and_follows_the_decision(env):
    _, inbox, nc, _ = env
    e = inbox.record(tc(), provider="claude-acp", session_id="s1")
    nc.sync_permissions(inbox)
    (n,) = nc.list()
    assert (n.category, n.state, n.link) == ("PERMISSION", "ACTION_REQUIRED", f"permissions:{e.id}")
    assert nc.badge() == 1
    nc.mark_read(n.id)
    assert nc.get(n.id).state == "ACTION_REQUIRED" and nc.badge() == 1   # ler nao e decidir
    inbox.decide(e.id, "allow_once")
    nc.sync_permissions(inbox)
    n = nc.get(n.id)
    assert n.state == "RESOLVED" and "allowed (allow_once) por human" in n.body and nc.badge() == 0
    assert nc.sync_permissions(inbox) == 0 and len(nc.list()) == 1          # idempotente


def test_unanswered_and_cancelled_permissions_end_expired_with_the_reason(env):
    clock, inbox, nc, _ = env
    a = inbox.record(tc("Write a.py"), provider="p", session_id="s1")
    b = inbox.record(tc("Write b.py"), provider="p", session_id="s2")
    inbox.cancel_open("o agente encerrou", session_id="s2")
    clock.t += 61
    inbox.expire_due()
    nc.sync_permissions(inbox)
    states = {n.source: (n.state, n.body) for n in nc.list()}
    assert states[f"permission:{a.id}"][0] == "EXPIRED" and "prazo vencido" in states[f"permission:{a.id}"][1]
    assert states[f"permission:{b.id}"][0] == "EXPIRED" and "o agente encerrou" in states[f"permission:{b.id}"][1]


def test_notifications_survive_a_restart_and_retention_never_drops_pending_actions(env):
    clock, inbox, nc, path = env
    inbox.record(tc(), provider="p", session_id="s1")
    nc.sync_permissions(inbox)
    nc.publish("AGENT", "Claude terminou o turno")
    old = nc.publish("ERROR", "Falha antiga")
    nc.resolve(old.id)
    clock.t += 200 * 86400
    assert nc.apply_retention(days=90) == 1                                  # so o resolvido antigo sai
    again = NotificationCenter(path, clock=clock)
    assert {n.category for n in again.list()} == {"PERMISSION", "AGENT"}
    assert again.list()[0].state == "ACTION_REQUIRED"                        # acao pendente primeiro
    again.close()


def test_filters_by_category_state_text_session_and_date(env):
    clock, _, nc, _ = env
    nc.publish("AGENT", "Claude terminou", session_id="s1")
    clock.t += 10
    nc.publish("ERROR", "Ollama não respondeu", session_id="s2")
    assert [n.title for n in nc.list(categories=["ERROR"])] == ["Ollama não respondeu"]
    assert [n.title for n in nc.list(text="Claude")] == ["Claude terminou"]
    assert [n.title for n in nc.list(session_id="s2")] == ["Ollama não respondeu"]
    assert [n.title for n in nc.list(since=clock.t)] == ["Ollama não respondeu"]
    assert nc.mark_all_read() == 2 and nc.badge() == 0
