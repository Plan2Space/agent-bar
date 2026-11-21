""""Precisa de você": PRs com decisao humana (HIGH aguardando no HEAD atual), recomendacao do revisor, notificacao por PR@HEAD.

GitHub FALSO; repos `Plan2Space/teste-*` ficticios. Nenhum teste posta nada nem abre notificacao real.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from agent_bar import approvals as ap
from agent_bar import inbox as ib

SHA1, SHA2 = "a" * 40, "b" * 40
REPO = "Plan2Space/teste-inbox"
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.timezone.utc)
HIGH_FILE = [{"filename": "schemas/x.json", "status": "modified", "patch": "x"}]


def fake_gh(prs, login="fmodesto30"):
    def gh(args):
        if args[:2] == ["api", "user"]:
            return 0, login
        if args[:2] == ["api", "graphql"]:
            f = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("query="))
            st = prs[(f"{f['owner']}/{f['name']}", int(f["number"]))]
            node = {"headRefOid": st["head"], "commits": {"nodes": [{"commit": {
                "committedDate": st.get("committed", "2026-11-21T10:00:00Z"), "statusCheckRollup": {"state": st.get("rollup", "SUCCESS")}}}]}}
            return 0, json.dumps({"data": {"repository": {"pullRequest": node}}})
        parts = args[1].split("/")
        st = prs[(f"{parts[1]}/{parts[2]}", int(parts[4]))]
        tail = parts[5] if len(parts) > 5 else ""
        if tail == "":
            return 0, json.dumps({"title": st.get("title", "feat: x"), "html_url": f"https://github.com/{parts[1]}/{parts[2]}/pull/{parts[4]}",
                                  "head": {"sha": st["head"]}, "labels": []})
        rows = {"files": st.get("files", HIGH_FILE), "comments": st.get("comments", []), "reviews": st.get("reviews", [])}[tail]
        return 0, "\n".join(json.dumps(r) for r in rows)
    return gh


def board(prs, **kw):
    return ap.collect_approvals(fake_gh(prs), list(prs), approvers=("fmodesto30",), reviewer=kw.get("reviewer", "fmmodesto"),
                                now=kw.get("now", NOW))


def review(state, commit, login="fmmodesto", body="", n=1):
    return {"user": {"login": login, "type": "User"}, "state": state, "commit_id": commit, "submitted_at": "2026-11-21T11:00:00Z",
            "html_url": f"https://github.com/{REPO}/pull/7#pullrequestreview-{n}", "body": body}


def comment(body, login="fmmodesto", created="2026-11-21T11:00:00Z", n=1):
    return {"user": {"login": login, "type": "User"}, "body": body, "created_at": created, "updated_at": created,
            "html_url": f"https://github.com/{REPO}/pull/7#issuecomment-{n}"}


# ------------------------------------------------------------------------------------------------ lista
def test_high_waiting_at_head_enters_the_list_with_wait_time_and_reason():
    inbox = ib.human_inbox(board({(REPO, 7): {"head": SHA1, "committed": "2026-11-21T09:30:00Z"}}), NOW)
    assert inbox.state == "lido" and len(inbox.items) == 1
    it = inbox.items[0]
    assert (it.pr_ref, it.risk, it.head_sha, it.ci_state) == (f"{REPO}#7", "HIGH", SHA1, "success")
    assert it.waiting_s == 9000 and "schemas/x.json" in it.reason


def test_medium_low_approved_or_blocked_prs_are_not_waiting_for_felipe():
    prs = {(REPO, 1): {"head": SHA1, "files": [{"filename": "agent_bar/x.py", "status": "modified", "patch": "x"}]},   # MEDIUM: revisor
           (REPO, 2): {"head": SHA1, "files": [{"filename": "docs/a.md", "status": "modified", "patch": "x"}]},        # LOW
           (REPO, 3): {"head": SHA1, "comments": [comment(f"APROVADO HIGH {SHA1}", login="fmodesto30")]},              # aprovado no HEAD
           (REPO, 4): {"head": SHA1, "rollup": "FAILURE"}}                                                              # CI vermelho
    assert ib.human_inbox(board(prs), NOW).items == []


def test_a_new_commit_after_approval_puts_the_pr_back_in_the_list():
    prs = {(REPO, 7): {"head": SHA2, "comments": [comment(f"APROVADO HIGH {SHA1}", login="fmodesto30")]}}
    assert [i.head_sha for i in ib.human_inbox(board(prs), NOW).items] == [SHA2]


def test_list_is_sorted_oldest_first():
    prs = {(REPO, 1): {"head": SHA1, "committed": "2026-11-21T11:00:00Z"}, (REPO, 2): {"head": SHA2, "committed": "2026-11-20T08:00:00Z"}}
    assert [i.pr_ref for i in ib.human_inbox(board(prs), NOW).items] == [f"{REPO}#2", f"{REPO}#1"]


def test_api_failure_is_unavailable_never_an_old_or_empty_list():
    inbox = ib.human_inbox(ap.ApprovalBoard(error="coleta de aprovações falhou: HTTP 502"), NOW)
    assert inbox.state == "indisponivel" and inbox.items == [] and "502" in inbox.reason


def test_unreadable_prs_are_counted_not_hidden():
    def gh(args):
        return (0, "fmodesto30") if args[:2] == ["api", "user"] else (1, "HTTP 502")
    inbox = ib.human_inbox(ap.collect_approvals(gh, [(REPO, 7)], approvers=("fmodesto30",), now=NOW), NOW)
    assert inbox.unreadable == 1 and inbox.items == []


# ------------------------------------------------------------------------------------------------ revisor
def test_reviewer_review_at_head_is_the_recommendation():
    prs = {(REPO, 7): {"head": SHA1, "reviews": [review("CHANGES_REQUESTED", SHA1, body="faltou teste\nmais"), review("APPROVED", "c" * 40, n=2)]}}
    it = ib.human_inbox(board(prs), NOW).items[0]
    assert it.reviewer_verdict == "CHANGES_REQUESTED" and it.reviewer_note == "faltou teste" and it.reviewer_url.endswith("review-1")


def test_reviewer_comment_naming_the_head_counts_and_old_ones_do_not():
    prs = {(REPO, 7): {"head": SHA1, "committed": "2026-11-21T10:00:00Z",
                       "comments": [comment("recomendo NAO aprovar (HEAD antigo)", created="2026-11-21T08:00:00Z", n=1),
                                    comment(f"Recomendo aprovar {SHA1[:7]}: CI verde, risco ok", created="2026-11-21T09:00:00Z", n=2)]}}
    it = ib.human_inbox(board(prs), NOW).items[0]
    assert it.reviewer_verdict == "comentário" and it.reviewer_note.startswith("Recomendo aprovar") and it.reviewer_url.endswith("comment-2")


def test_without_reviewer_opinion_it_says_so():
    it = ib.human_inbox(board({(REPO, 7): {"head": SHA1, "reviews": [review("COMMENTED", SHA1, login="outra-pessoa")]}}), NOW).items[0]
    assert it.reviewer_verdict is None


# ------------------------------------------------------------------------------------------------ notificacao
def _inbox(*items):
    return ib.Inbox("lido", "x", list(items))


def _item(n, head=SHA1):
    return ib.HumanItem(pr_ref=f"{REPO}#{n}", url=f"https://github.com/{REPO}/pull/{n}", title=f"PR {n}", risk="HIGH", reason="r",
                        head_sha=head, ci_state="success", waiting_s=60)


def test_notifies_once_per_pr_at_head_and_again_after_a_new_commit():
    shown = []
    n = ib.InboxNotifier(show=lambda title, text, url: shown.append((title, text, url)))
    n.update(_inbox(_item(7)))
    n.update(_inbox(_item(7)))                                                     # novo polling, mesmo HEAD: silencio
    assert len(shown) == 1 and shown[0][2].endswith("/pull/7") and "teste-inbox#7" in shown[0][1]
    n.update(_inbox(_item(7, head=SHA2)))                                          # commit novo: avisa de novo
    assert len(shown) == 2


def test_several_new_prs_become_one_notification():
    shown = []
    ib.InboxNotifier(show=lambda *a: shown.append(a)).update(_inbox(_item(1), _item(2), _item(3)))
    assert len(shown) == 1 and "3 PRs" in shown[0][0]


def test_a_failing_notification_is_logged_and_never_breaks_the_list(caplog):
    def boom(*a):
        raise RuntimeError("bandeja indisponivel")
    n = ib.InboxNotifier(show=boom)
    with caplog.at_level("WARNING", logger="agent_bar"):
        assert [i.pr_ref for i in n.update(_inbox(_item(7)))] == [f"{REPO}#7"]
    assert "bandeja indisponivel" in caplog.text
    assert n.update(_inbox(_item(7))) == []                                         # nao tenta de novo a cada polling


def test_unavailable_inbox_never_notifies_nor_forgets():
    shown = []
    n = ib.InboxNotifier(show=lambda *a: shown.append(a))
    n.update(_inbox(_item(7)))
    n.update(ib.Inbox("indisponivel", "HTTP 502", []))
    n.update(_inbox(_item(7)))
    assert len(shown) == 1


def test_disabled_notifications_show_nothing():
    shown = []
    n = ib.InboxNotifier(show=lambda *a: shown.append(a), enabled=lambda: False)
    n.update(_inbox(_item(7)))
    assert shown == []


# ------------------------------------------------------------------------------------------------ UI
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def test_panel_top_section_and_bar_counter_show_what_waits_for_felipe(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = board({(REPO, 7): {"head": SHA1, "reviews": [review("COMMENTED", SHA1, body="recomendo aprovar")]}})
    bar.on_snapshot(s)
    text = bar.details.human.text()
    assert "Precisa de você" in text and "teste-inbox#7" in text and "HIGH" in text and SHA1[:7] in text and "CI success" in text
    assert "fmmodesto" in text and "COMMENTED" in text and "recomendo aprovar" in text and "review-1" in text
    assert bar.human_badge.isVisibleTo(bar) and "1" in bar.human_badge.text()


def test_empty_list_says_nothing_waits_and_hides_the_counter(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = board({(REPO, 2): {"head": SHA1, "files": [{"filename": "docs/a.md", "status": "modified", "patch": "x"}]}})
    bar.on_snapshot(s)
    assert "nada esperando você" in bar.details.human.text() and not bar.human_badge.isVisibleTo(bar)


def test_unavailable_shows_reason_and_a_question_mark_counter(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = ap.ApprovalBoard(error="coleta de aprovações falhou: HTTP 502")
    bar.on_snapshot(s)
    assert "INDISPONÍVEL" in bar.details.human.text() and "502" in bar.details.human.text()
    assert bar.human_badge.isVisibleTo(bar) and "?" in bar.human_badge.text()


def test_section_stays_visible_in_the_full_view(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = board({(REPO, 7): {"head": SHA1}})
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    assert bar.details.human.isVisibleTo(bar.details)


def test_setting_to_turn_notifications_off_is_persisted(tmp_path):
    from agent_bar import settings as cfg
    s = cfg.Settings()
    assert s.notify_human is True
    s.notify_human = False
    cfg.save(s, tmp_path / "s.json")
    assert cfg.load(tmp_path / "s.json").notify_human is False


def test_waiting_text_is_human_readable():
    assert ib.waited(90) == "1 min" and ib.waited(5400) == "1 h 30 min" and ib.waited(200000) == "2 d 7 h" and ib.waited(None) == "?"


@pytest.mark.parametrize("bad", [None, "", "nao-e-data"])
def test_unknown_head_time_never_invents_a_wait(bad):
    prs = {(REPO, 7): {"head": SHA1, "committed": bad}}
    it = ib.human_inbox(board(prs), NOW).items[0]
    assert it.waiting_s is None
