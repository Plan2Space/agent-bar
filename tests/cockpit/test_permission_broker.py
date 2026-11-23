"""UX F4b: responder permissao de qualquer superficie — uma resposta por pedido, idempotente, irreversivel so com
humano, prazo responde ao agente, encerramento cancela com motivo."""
from __future__ import annotations

import pytest

from agent_bar.cockpit.acp import PermissionRequest
from agent_bar.cockpit.permission_broker import PermissionBroker
from agent_bar.cockpit.permission_inbox import AlreadyDecided, PermissionInbox

OPTIONS = ({"optionId": "allow", "name": "Allow", "kind": "allow_once"},
           {"optionId": "always", "name": "Always", "kind": "allow_always"},
           {"optionId": "reject", "name": "Reject", "kind": "reject_once"})


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def make(tmp_path, title="Write a.py", command=None):
    clock = Clock()
    inbox = PermissionInbox(tmp_path / "p.sqlite", clock=clock, ttl_s=60)
    changes = []
    broker = PermissionBroker(inbox, on_change=changes.append)
    raw = {"command": command} if command else {"file_path": "a.py"}
    tc = {"toolCallId": "t", "title": title, "kind": "execute" if command else "edit", "rawInput": raw}
    entry = inbox.record(tc, provider="p", session_id="s1")
    sent = []
    broker.register(entry.id, PermissionRequest("s1", tc, OPTIONS), sent.append)
    return clock, inbox, broker, entry, sent, changes


def test_the_first_answer_wins_and_the_agent_hears_once(tmp_path):
    _, inbox, broker, e, sent, changes = make(tmp_path)
    assert broker.is_open(e.id)
    out = broker.answer(e.id, "allow_once")
    assert sent == ["allow"] and out.status == "ALLOWED" and out.decision == "allow_once" and not broker.is_open(e.id)
    assert broker.answer(e.id, "allow_once").status == "ALLOWED" and sent == ["allow"]   # outra superficie, mesma escolha
    with pytest.raises(AlreadyDecided):
        broker.answer(e.id, "deny")
    assert sent == ["allow"] and changes.count(e.id) == 2                     # registrou + respondeu


def test_deny_picks_the_reject_option(tmp_path):
    _, inbox, broker, e, sent, _ = make(tmp_path)
    assert broker.answer(e.id, "deny").status == "DENIED" and sent == ["reject"]


def test_an_option_the_agent_did_not_offer_is_refused(tmp_path):
    _, _, broker, e, sent, _ = make(tmp_path)
    with pytest.raises(ValueError):
        broker.answer(e.id, "allow_forever")
    assert sent == [] and broker.is_open(e.id)


def test_irreversible_needs_a_human(tmp_path):
    _, _, broker, e, sent, _ = make(tmp_path, title="Run rm", command="rm -rf build")
    with pytest.raises(PermissionError):
        broker.answer(e.id, "allow_once", by="rule:x")
    assert sent == [] and broker.is_open(e.id)
    assert broker.answer(e.id, "allow_once", by="human").status == "ALLOWED" and sent == ["allow"]


def test_an_expired_request_is_refused_to_the_agent_instead_of_hanging(tmp_path):
    clock, inbox, broker, e, sent, _ = make(tmp_path)
    clock.t += 61
    assert broker.expire() == [e.id]
    assert sent == [None] and inbox.get(e.id).status == "EXPIRED" and not broker.is_open(e.id)


def test_closing_the_studio_cancels_open_requests_with_the_reason(tmp_path):
    _, inbox, broker, e, sent, _ = make(tmp_path)
    assert broker.cancel_all("a aba do Chat foi fechada") == 1
    got = inbox.get(e.id)
    assert sent == [None] and got.status == "CANCELLED" and got.result == "a aba do Chat foi fechada"
