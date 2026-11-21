"""Aprovadores por nivel (agent-runtime 0de08b9, PR #64): o revisor `fmmodesto` aprova MEDIUM, nunca HIGH.

GitHub FALSO; repos `Plan2Space/teste-*` ficticios. Nenhum teste posta nada.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json

from agent_bar import approvals as ap
from agent_bar import inbox as ib

SHA = "a" * 40
REPO = "Plan2Space/teste-niveis"
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)
HIGH = [{"filename": "schemas/x.json", "status": "modified", "patch": "x"}]
MEDIUM = [{"filename": "agent_bar/x.py", "status": "modified", "patch": "x"}]
LEVELS = {"MEDIUM": ("fmmodesto", "fmodesto30"), "HIGH": ("fmodesto30",)}


def fake_gh(prs, login="fmodesto30"):
    def gh(args):
        if args[:2] == ["api", "user"]:
            return 0, login
        if args[:2] == ["api", "graphql"]:
            f = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("query="))
            st = prs[(f"{f['owner']}/{f['name']}", int(f["number"]))]
            node = {"headRefOid": SHA, "commits": {"nodes": [{"commit": {"committedDate": "2026-11-21T10:00:00Z",
                                                                          "statusCheckRollup": {"state": "SUCCESS"}}}]}}
            return 0, json.dumps({"data": {"repository": {"pullRequest": node}}})
        parts = args[1].split("/")
        st = prs[(f"{parts[1]}/{parts[2]}", int(parts[4]))]
        tail = parts[5] if len(parts) > 5 else ""
        if tail == "":
            return 0, json.dumps({"title": "x", "html_url": f"https://github.com/{REPO}/pull/{parts[4]}", "head": {"sha": SHA},
                                  "labels": [], "user": {"login": st.get("author", "fmodesto30")}})
        rows = {"files": st["files"], "comments": st.get("comments", []), "reviews": st.get("reviews", [])}[tail]
        return 0, "\n".join(json.dumps(r) for r in rows)
    return gh


def review(login, state="APPROVED"):
    return {"user": {"login": login, "type": "User"}, "state": state, "commit_id": SHA, "submitted_at": "2026-11-21T11:00:00Z",
            "html_url": f"https://github.com/{REPO}/pull/7#pullrequestreview-1", "body": "ok"}


def comment(body, login):
    return {"user": {"login": login, "type": "User"}, "body": body, "created_at": "2026-11-21T11:00:00Z",
            "updated_at": "2026-11-21T11:00:00Z", "html_url": f"https://github.com/{REPO}/pull/7#issuecomment-1"}


def row(files, login="fmodesto30", approvers=LEVELS, **st):
    prs = {(REPO, 7): {"files": files, **st}}
    return ap.collect_approvals(fake_gh(prs, login), list(prs), approvers=approvers, now=NOW).rows[0]


# ------------------------------------------------------------------------------------------------ configuracao
def test_runtime_pin_requires_approvers_per_level():
    from agent_runtime.approval import from_github_review
    assert "approvers" in inspect.signature(from_github_review).parameters


def test_approvers_are_read_per_level_from_the_environment():
    m = ap.approvers_from_env({"AGENT_BAR_APPROVERS_MEDIUM": "fmmodesto, fmodesto30", "AGENT_BAR_APPROVERS_HIGH": "fmodesto30"})
    assert m == {"MEDIUM": ("fmmodesto", "fmodesto30"), "HIGH": ("fmodesto30",)}


def test_the_old_flat_variable_still_means_both_levels():
    assert ap.approvers_from_env({"AGENT_BAR_APPROVERS": "fmodesto30"}) == {"MEDIUM": ("fmodesto30",), "HIGH": ("fmodesto30",)}


def test_the_reviewer_account_is_never_a_high_approver_even_if_configured():
    m = ap.approvers_from_env({"AGENT_BAR_APPROVERS": "fmodesto30,fmmodesto", "AGENT_BAR_REVIEWER": "fmmodesto"})
    assert m == {"MEDIUM": ("fmodesto30", "fmmodesto"), "HIGH": ("fmodesto30",)}
    assert ap.approvers_from_env({"AGENT_BAR_APPROVERS_HIGH": "FMModesto"})["HIGH"] == ()


# ------------------------------------------------------------------------------------------------ decisao
def test_reviewer_approved_review_on_high_does_not_take_it_out_of_felipes_list():
    r = row(HIGH, reviews=[review("fmmodesto")])
    assert r.risk == "HIGH" and r.state == "aguardando" and r.approval is None
    board = ap.ApprovalBoard(rows=[r], reviewer="fmmodesto")
    assert [i.pr_ref for i in ib.human_inbox(board, NOW).items] == [f"{REPO}#7"]


def test_reviewer_approved_review_on_medium_approves_it():
    r = row(MEDIUM, reviews=[review("fmmodesto")])
    assert r.risk == "MEDIUM" and r.state == "aprovado" and r.approval.approver == "fmmodesto"


def test_reviewer_comment_claiming_high_never_approves_a_high_pr():
    r = row(HIGH, comments=[comment(f"APROVADO HIGH {SHA}", "fmmodesto")])
    assert r.state == "aguardando" and r.approval is None


def test_felipes_comment_still_approves_high():
    r = row(HIGH, comments=[comment(f"APROVADO HIGH {SHA}", "fmodesto30")])
    assert r.state == "aprovado" and r.approval.approver == "fmodesto30"


def test_the_pr_author_review_never_counts():
    r = row(MEDIUM, author="fmodesto30", reviews=[review("fmodesto30")])
    assert r.state == "aguardando"


def test_approve_button_needs_the_login_in_the_list_of_the_pr_level():
    assert row(HIGH, login="fmodesto30").can_approve is True
    blocked = row(HIGH, login="fmmodesto")
    assert blocked.can_approve is False and "HIGH" in blocked.blocked_reason
    assert row(MEDIUM, login="fmmodesto").can_approve is True


def test_no_high_approver_configured_says_so():
    r = row(HIGH, approvers={"MEDIUM": ("fmmodesto",), "HIGH": ()})
    assert r.can_approve is False and "AGENT_BAR_APPROVERS_HIGH" in r.blocked_reason
