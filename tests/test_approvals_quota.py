"""C2 do relatório da cota REST (F3 + F4): o app aberto gastava ~38 REST/min porque a coleta de aprovações refazia 4
chamadas REST por PR aberto a cada 60 s, mais `gh api user`. Agora:
- um PR só é relido quando o HEAD, o `updatedAt` (já vêm na fila GraphQL) ou a evidência do motor mudam;
- o login é lido uma vez;
- no 403 de cota, o REST para até o reset (lido de `gh api rate_limit`, que não gasta cota) e a linha diz isso.
A regra de aprovação não muda: é a mesma `_one` quando o PR é lido. Contra um `gh` falso que conta as chamadas."""
from __future__ import annotations

import json

from agent_bar import approvals as ap
from agent_bar import collectors as col

HEAD_A, HEAD_B = "a" * 40, "b" * 40
RESET = 1_800_000_000


class FakeGh:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.head = {7: HEAD_A, 8: HEAD_A}
        self.rate_limited = False

    def rest(self) -> list[list[str]]:
        return [a for a in self.calls if a[:2] != ["api", "graphql"] and a[:2] != ["api", "rate_limit"]]

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["api", "rate_limit"]:
            return 0, json.dumps({"resources": {"core": {"remaining": 0, "reset": RESET}}})
        if args[:2] == ["api", "graphql"]:
            n = int(next(a.split("=", 1)[1] for a in args if a.startswith("number=")))
            return 0, json.dumps({"data": {"repository": {"pullRequest": {"headRefOid": self.head[n], "commits": {"nodes": [
                {"commit": {"committedDate": "2026-11-24T00:00:00Z", "statusCheckRollup": {"state": "SUCCESS"}}}]}}}}})
        if self.rate_limited:
            return 1, "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
        if args[:2] == ["api", "user"]:
            return 0, "fmodesto30"
        path = args[1]
        if path.endswith("/files"):
            return 0, json.dumps({"filename": "docs/x.md", "status": "modified", "patch": "+x", "previous_filename": None})
        if path.endswith("/comments") or path.endswith("/reviews"):
            return 0, ""
        n = int(path.rsplit("/", 1)[-1])
        return 0, json.dumps({"html_url": f"https://github.com/o/r/pull/{n}", "title": f"PR {n}", "labels": [],
                              "user": {"login": "fmodesto30"}})


REFS = [("Plan2Space/agent-bar", 7), ("Plan2Space/agent-bar", 8)]


def versions(gh, updated="2026-11-24T00:00:00Z"):
    return {ref: (gh.head[ref[1]], updated) for ref in REFS}


def collect(gh, memo, vers, now=1_700_000_000.0):
    return ap.collect_approvals(gh, REFS, approvers=["fmmodesto"], memo=memo, versions=vers, clock=lambda: now)


def test_an_unchanged_pr_is_not_read_again():
    gh, memo = FakeGh(), ap.ApprovalMemo()
    first = collect(gh, memo, versions(gh))
    n_first = len(gh.rest())
    assert n_first == 1 + 4 * 2                                                    # user + 4 por PR
    second = collect(gh, memo, versions(gh))
    assert len(gh.rest()) == n_first                                               # nenhuma chamada REST nova
    assert [r.head_sha for r in second.rows] == [r.head_sha for r in first.rows]
    assert [r.state for r in second.rows] == [r.state for r in first.rows]


def test_only_the_pr_whose_head_or_updated_at_changed_is_read_again():
    gh, memo = FakeGh(), ap.ApprovalMemo()
    collect(gh, memo, versions(gh))
    before = len(gh.rest())
    gh.head[8] = HEAD_B
    board = collect(gh, memo, versions(gh))
    assert len(gh.rest()) - before == 4                                            # só o PR 8, 4 REST
    assert board.rows[1].head_sha == HEAD_B
    before = len(gh.rest())
    vers = versions(gh)
    vers[REFS[0]] = (HEAD_A, "2026-11-24T01:00:00Z")                               # comentário novo no PR 7
    collect(gh, memo, vers)
    assert len(gh.rest()) - before == 4


def test_the_login_is_read_once_per_memo():
    gh, memo = FakeGh(), ap.ApprovalMemo()
    for _ in range(3):
        collect(gh, memo, versions(gh))
    assert sum(1 for a in gh.calls if a[:2] == ["api", "user"]) == 1


def test_without_a_memo_the_behaviour_is_the_old_one():
    gh = FakeGh()
    ap.collect_approvals(gh, REFS, approvers=["fmmodesto"])
    ap.collect_approvals(gh, REFS, approvers=["fmmodesto"])
    assert len(gh.rest()) == 2 * (1 + 4 * 2)


def test_rate_limit_stops_rest_until_the_reset_and_says_so():
    gh, memo = FakeGh(), ap.ApprovalMemo()
    gh.rate_limited = True
    board = collect(gh, memo, versions(gh))
    assert memo.rest_blocked_until == RESET
    assert all("cota REST do GitHub esgotada" in r.blocked_reason for r in board.rows)
    assert not any(r.can_approve for r in board.rows)
    calls = len(gh.rest())
    board = collect(gh, memo, versions(gh), now=RESET - 60)                       # ainda antes do reset
    assert len(gh.rest()) == calls                                                 # nenhuma chamada REST
    assert all("até" in r.blocked_reason for r in board.rows)
    gh.rate_limited = False
    board = collect(gh, memo, versions(gh), now=RESET + 1)                        # depois do reset: volta a ler
    assert len(gh.rest()) > calls and all(not r.blocked_reason or "cota" not in r.blocked_reason for r in board.rows)


def test_engine_evidence_change_reads_the_pr_again():
    class Runs:
        def __init__(self, runs):
            self.runs = runs
    gh, memo = FakeGh(), ap.ApprovalMemo()
    refs = [(ap.ENGINE_REPO, 7)]
    vers = {refs[0]: (HEAD_A, "u1")}
    ap.collect_approvals(gh, refs, approvers=["fmmodesto"], memo=memo, versions=vers, runs=Runs([]), clock=lambda: 1.0)
    before = len(gh.rest())
    run = type("R", (), {"engine_commit": HEAD_A, "verdict": "PASS", "problems": [], "run_id": "r1"})()
    ap.collect_approvals(gh, refs, approvers=["fmmodesto"], memo=memo, versions=vers, runs=Runs([run]),
                         clock=lambda: 1.0)
    assert len(gh.rest()) - before == 4                                            # evidência nova: relê


def test_the_pr_queue_carries_head_and_updated_at():
    node = {"number": 7, "title": "t", "url": "u", "isDraft": False, "mergeable": "MERGEABLE", "headRefOid": HEAD_A,
            "updatedAt": "2026-11-24T00:00:00Z", "repository": {"nameWithOwner": "Plan2Space/agent-bar"},
            "reviews": {"nodes": []}, "commits": {"nodes": []}}
    out = json.dumps({"data": {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}}})
    q = col.collect_org_pr_queue(gh=lambda args: (0, out))
    assert "updatedAt" in col.ORG_PR_QUERY
    assert (q.rows[0].head, q.rows[0].updated_at) == (HEAD_A, "2026-11-24T00:00:00Z")


def test_the_worker_keeps_one_memo_and_passes_the_queue_versions(monkeypatch):
    from agent_bar import settings as cfg
    from agent_bar.core import Worker
    from agent_bar.models import PrQueue, PrRow
    seen = {}

    def fake_collect(gh, refs, **kw):
        seen.update(kw)
        return ap.ApprovalBoard()
    monkeypatch.setattr(ap, "collect_approvals", fake_collect)
    w = Worker(cfg.Settings())
    assert isinstance(w._approval_memo, ap.ApprovalMemo)                          # noqa: SLF001
    q = PrQueue(rows=[PrRow(7, "t", "u", "aguarda", "Plan2Space/agent-bar", head=HEAD_A, updated_at="u1")])
    col._approvals_for(q, None, None, memo=w._approval_memo, gh=lambda a: (0, ""))   # noqa: SLF001
    assert seen["memo"] is w._approval_memo and seen["versions"] == {("Plan2Space/agent-bar", 7): (HEAD_A, "u1")}


def test_a_rate_limit_on_a_pr_read_also_suspends_rest():
    gh, memo = FakeGh(), ap.ApprovalMemo(login="fmodesto30")                     # login já lido
    gh.rate_limited = True
    board = collect(gh, memo, versions(gh))
    assert memo.rest_blocked_until == RESET
    assert all("cota REST do GitHub esgotada" in r.blocked_reason for r in board.rows)
    assert len(gh.rest()) == 1                                                     # parou no primeiro PR


def test_the_worker_loop_passes_its_memo_to_the_collection(monkeypatch):
    from agent_bar import settings as cfg
    from agent_bar.core import Worker
    w = Worker(cfg.Settings())
    seen = {}

    def fake(**kw):
        seen.update(kw)
        w._stop.set()                                                              # noqa: SLF001 - uma volta só
        return object()
    monkeypatch.setattr(col, "collect_snapshot", fake)
    w._loop()                                                                      # noqa: SLF001
    assert seen["approval_memo"] is w._approval_memo                              # noqa: SLF001
