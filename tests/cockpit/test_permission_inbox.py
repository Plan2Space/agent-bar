"""F4 da SPEC de UX: Permission Inbox persistente — nada some, decisao idempotente, politica distinguida, repeticao
explicada, irreversivel so com humano, segredo redigido."""
from __future__ import annotations

import pytest

from agent_bar.cockpit.permission_inbox import AlreadyDecided, PermissionInbox, classify, redact


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def tc(title="Write a.py", kind="edit", **raw):
    return {"toolCallId": "t1", "title": title, "kind": kind, "rawInput": raw or {"file_path": "C:/ws/a.py"}}


@pytest.fixture
def inbox(tmp_path):
    clock = Clock()
    ib = PermissionInbox(tmp_path / "permissions.sqlite", clock=clock, ttl_s=60)
    ib.clock_obj = clock
    yield ib
    ib.close()


def test_a_request_is_recorded_before_any_decision_with_who_what_where_and_why(inbox):
    e = inbox.record(tc(file_path="C:/ws/a.py", description="salvar o ajuste"), provider="claude-acp", session_id="s1",
                     workspace="C:/ws")
    assert e.status == "PENDING" and e.policy == "NORMAL" and e.risk == "MEDIUM"
    assert (e.provider, e.session_id, e.title, e.kind) == ("claude-acp", "s1", "Write a.py", "edit")
    assert e.resource == "C:/ws/a.py" and e.reason == "salvar o ajuste" and e.expires_at == e.created_at + 60
    assert [p.id for p in inbox.pending()] == [e.id]


def test_decisions_are_idempotent_and_never_rewritten(inbox):
    e = inbox.record(tc(), provider="p", session_id="s1")
    assert inbox.decide(e.id, "allow_once").status == "ALLOWED"
    assert inbox.decide(e.id, "allow_once").status == "ALLOWED"           # mesma decisao: nada muda
    with pytest.raises(AlreadyDecided):
        inbox.decide(e.id, "deny")
    closed = inbox.record(tc(title="Run ls", kind="execute", command="ls"), provider="p", session_id="s1")
    assert inbox.decide(closed.id, None).status == "DENIED"                # fechar o dialogo = recusar


def test_nothing_disappears_after_a_restart(tmp_path):
    clock = Clock()
    first = PermissionInbox(tmp_path / "p.sqlite", clock=clock)
    e = first.record(tc(), provider="p", session_id="s1")
    first.close()
    again = PermissionInbox(tmp_path / "p.sqlite", clock=clock)
    assert again.get(e.id).status == "PENDING"
    assert again.cancel_open("o Studio foi reiniciado e o agente nao espera mais a resposta") == 1
    gone = again.get(e.id)
    assert gone.status == "CANCELLED" and "reiniciado" in gone.result      # continua no historico com o motivo
    assert [h.id for h in again.history()] == [e.id]
    again.close()


def test_unanswered_requests_expire_explicitly(inbox):
    e = inbox.record(tc(), provider="p", session_id="s1")
    inbox.clock_obj.t += 61
    assert inbox.pending() == [] and inbox.get(e.id).status == "EXPIRED"


@pytest.mark.parametrize("call,risk,policy", [
    (tc(title="Read a.py", kind="read"), "LOW", "NORMAL"),
    (tc(title="Run tests", kind="execute", command="pytest"), "HIGH", "NORMAL"),
    (tc(title="Run rm", kind="execute", command="rm -rf build"), "IRREVERSIBLE", "IRREVERSIBLE"),
    (tc(title="Push", kind="execute", command="git push origin main --force"), "IRREVERSIBLE", "FORBIDDEN"),
    (tc(title="Merge", kind="execute", command="gh pr merge 3 --admin"), "IRREVERSIBLE", "FORBIDDEN"),
])
def test_risk_and_policy_are_classified(call, risk, policy):
    assert classify(call) == (risk, policy)


def test_a_mode_that_decides_alone_is_recorded_as_bypass(inbox):
    e = inbox.record(tc(), provider="p", session_id="s1", mode_decides_alone=True)
    assert e.policy == "BYPASS_MODE"


def test_forbidden_is_denied_by_policy_without_asking(inbox):
    e = inbox.record(tc(title="Push", kind="execute", command="git push -f origin main"), provider="p", session_id="s1")
    assert (e.status, e.decision, e.decided_by) == ("DENIED", "deny", "policy") and inbox.pending() == []


def test_irreversible_is_never_allowed_by_rule_or_policy(inbox):
    inbox.add_rule("execute", "Run", "C:/ws")
    e = inbox.record(tc(title="Run rm", kind="execute", command="rm -rf build"), provider="p", session_id="s1",
                     workspace="C:/ws")
    assert e.status == "PENDING" and e.policy == "IRREVERSIBLE"           # a regra nao cobre irreversivel
    with pytest.raises(PermissionError):
        inbox.decide(e.id, "allow_once", by="rule:x")
    assert inbox.decide(e.id, "allow_once", by="human").status == "ALLOWED"


def test_a_persistent_rule_only_comes_from_a_human_and_is_recorded_as_previously_granted(inbox):
    with pytest.raises(PermissionError):
        inbox.add_rule("edit", "Write", "C:/ws", created_by="agent")
    rule = inbox.add_rule("edit", "Write", "C:/ws")
    e = inbox.record(tc(), provider="p", session_id="s1", workspace="C:/ws")
    assert (e.policy, e.status, e.decided_by) == ("PREVIOUSLY_GRANTED", "ALLOWED", f"rule:{rule}")
    other_ws = inbox.record(tc(), provider="p", session_id="s1", workspace="C:/outro")
    assert other_ws.status == "PENDING"                                    # regra vale para o workspace dela
    inbox.disable_rule(rule)
    assert inbox.record(tc(), provider="p", session_id="s1", workspace="C:/ws").status == "PENDING"


@pytest.mark.parametrize("first_decision,same_session,expected", [
    ("allow_once", True, "permitir uma vez"),
    ("deny", True, "negado"),
    ("allow_once", False, "outra sessão"),
])
def test_repeated_requests_are_explained(inbox, first_decision, same_session, expected):
    a = inbox.record(tc(), provider="p", session_id="s1")
    inbox.decide(a.id, first_decision)
    b = inbox.record(tc(), provider="p", session_id="s1" if same_session else "s2")
    assert b.repeat_of == a.id and expected in b.repeat_note


def test_execution_result_is_attached_to_the_request(inbox):
    e = inbox.record(tc(), provider="p", session_id="s1")
    inbox.decide(e.id, "allow_once")
    inbox.finish_tool_call("s1", "t1", "completed")
    assert inbox.get(e.id).result == "completed"


def test_secrets_never_reach_the_database(inbox, tmp_path):
    e = inbox.record(tc(title="Run curl", kind="execute", command="curl -H token=ghp_" + "a" * 30 + " x"),
                     provider="p", session_id="s1")
    assert "ghp_" not in e.operation and "«redigido»" in e.operation
    raw = (tmp_path / "permissions.sqlite").read_bytes()
    assert b"ghp_aaaa" not in raw
    assert redact("password: hunter2") == "password=«redigido»"


def test_history_filters_by_status_session_and_text(inbox):
    a = inbox.record(tc(title="Write a.py"), provider="p", session_id="s1")
    inbox.record(tc(title="Read b.py", kind="read"), provider="p", session_id="s2")
    inbox.decide(a.id, "allow_once")
    assert [h.title for h in inbox.history(status=["ALLOWED"])] == ["Write a.py"]
    assert [h.title for h in inbox.history(session_id="s2")] == ["Read b.py"]
    assert [h.title for h in inbox.history(text="b.py")] == ["Read b.py"]
