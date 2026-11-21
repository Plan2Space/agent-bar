"""Board do rebuild (Plan2Space Project #2) por padrao; legado so por selecao explicita; falha = erro, nunca numero antigo.

Os JSON abaixo sao respostas FALSAS do GitHub, montadas no teste (Task IDs `T-*`). Nao sao o estado do board.
"""
from __future__ import annotations

import json

from agent_bar import collectors as col
from agent_bar import targets as tg

REBUILD_ID = "PVT_kwDOFV8G784BsjNn"


def item(task, status, n=1, repo="Plan2Space/agent-bar", kind="Issue"):
    content = {"__typename": kind, "number": n, "title": f"{task} titulo", "repository": {"nameWithOwner": repo}}
    if kind == "DraftIssue":
        content = {"__typename": kind, "title": f"{task} rascunho"}
    return {"status": {"name": status} if status else None, "task": {"text": task} if task else None, "content": content}


def project(nodes, total=None, more=False, cursor=None, title="Plan2Space Rebuild", pid=REBUILD_ID,
            url="https://github.com/orgs/Plan2Space/projects/2"):
    return json.dumps({"data": {"organization": {"projectV2": {"id": pid, "title": title, "url": url, "items": {
        "totalCount": len(nodes) if total is None else total, "pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}})


# ------------------------------------------------------------------------------------------------ selecao
def test_default_board_is_the_rebuild_project_and_legacy_needs_an_explicit_choice():
    assert tg.load_targets({}).board == "rebuild"
    t = tg.load_targets({"AGENT_BAR_BOARD": "legacy"})
    assert (t.org, t.project, t.board) == ("GFCDOTA", 1, "legacy") and "GPT Review" in t.columns
    bad = tg.load_targets({"AGENT_BAR_BOARD": "outro"})
    assert bad.board == "rebuild" and "AGENT_BAR_BOARD" in bad.warning


def test_board_reads_name_url_id_task_ids_and_status_of_every_item():
    nodes = [item("T-01", "Done", 4), item("T-07", "In Progress", 7), item("T-13", "Backlog", 8), item(None, None, 9),
             item("T-99", "Backlog", kind="DraftIssue")]
    b = col.collect_board(lambda a: (0, project(nodes)), tg.Targets())
    assert b.error is None and (b.title, b.url, b.node_id, b.total) == ("Plan2Space Rebuild", "https://github.com/orgs/Plan2Space/projects/2", REBUILD_ID, 5)
    assert b.counts["Backlog"] == 2 and b.counts["In Progress"] == 1 and b.counts["Done"] == 1 and b.counts["(sem status)"] == 1
    got = {(i.task_id, i.status, i.ref) for i in b.items}
    assert ("T-07", "In Progress", "agent-bar#7") in got and ("T-99", "Backlog", "rascunho") in got and ("", "(sem status)", "agent-bar#9") in got


def test_board_follows_pagination_past_100_items():
    first = [item(f"T-{i}", "Backlog", i) for i in range(100)]
    pages = [project(first, total=101, more=True, cursor="C1"), project([item("T-100", "Done", 100)], total=101)]
    calls = []

    def gh(args):
        calls.append(args)
        return 0, pages[len(calls) - 1]
    b = col.collect_board(gh, tg.Targets())
    assert b.error is None and b.total == 101 and len(b.items) == 101 and b.counts["Done"] == 1 and "after=C1" in calls[1]


# ------------------------------------------------------------------------------------------------ falhas
def test_no_access_to_the_project_is_an_error_not_an_empty_board():
    b = col.collect_board(lambda a: (0, json.dumps({"data": {"organization": {"projectV2": None}}})), tg.Targets())
    assert b.items == [] and all(v is None for v in b.counts.values()) and "sem acesso" in b.error
    b = col.collect_board(lambda a: (1, "HTTP 401: Bad credentials"), tg.Targets())
    assert "rc=1" in b.error and "Bad credentials" in b.error and b.synced is None


def test_an_incomplete_response_is_refused_instead_of_counted():
    b = col.collect_board(lambda a: (0, project([item("T-1", "Done")], total=3)), tg.Targets())
    assert "incompleta" in b.error and "1 de 3" in b.error and all(v is None for v in b.counts.values()) and b.items == []


def test_snapshot_never_falls_back_to_old_numbers_when_the_board_read_fails(tmp_path):
    cache = col.GithubCache(ttl_s=0)
    state = {"ok": True}

    def gh(args):
        joined = " ".join(args)
        if "projectV2" in joined:
            return (0, project([item("T-1", "Ready")])) if state["ok"] else (1, "GraphQL: API rate limit exceeded")
        return 1, "offline"
    s1 = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=gh, backlog_cache=cache, targets=tg.Targets())
    state["ok"] = False
    s2 = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=gh, backlog_cache=cache, targets=tg.Targets())
    assert s1.backlog["Ready"] == 1 and s1.board.error is None
    assert all(v is None for v in s2.backlog.values()) and "rate limit" in s2.board.error and s2.board.items == []


# ------------------------------------------------------------------------------------------------ nao contaminacao
def test_rebuild_board_is_never_mixed_with_legacy_data(tmp_path):
    seen = []

    def gh(args):
        joined = " ".join(args)
        seen.append(joined)
        if 'login:"GFCDOTA"' in joined:
            return 0, project([item("LEG-1", "GPT Review")] * 3, title="Legado", pid="PVT_legacy", url="https://github.com/orgs/GFCDOTA/projects/1")
        if 'login:"Plan2Space"' in joined:
            return 0, project([item("T-7", "In Progress")])
        return 1, "offline"
    s = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=gh, targets=tg.load_targets({}))
    assert not any("GFCDOTA" in c for c in seen)
    assert s.board.node_id == REBUILD_ID and "GPT Review" not in s.backlog and [i.task_id for i in s.board.items] == ["T-7"]
    legacy = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=gh, targets=tg.load_targets({"AGENT_BAR_BOARD": "legacy"}))
    assert legacy.board.board == "legacy" and legacy.backlog["GPT Review"] == 3 and legacy.board.node_id == "PVT_legacy"


# ------------------------------------------------------------------------------------------------ UI / diagnostico
def test_diagnostic_names_the_project_and_lists_its_items(tmp_path):
    s = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None,
                             gh=lambda a: (0, project([item("T-7", "In Progress", 7)])) if "projectV2" in " ".join(a) else (1, "x"),
                             targets=tg.Targets())
    d = s.diagnostic()
    assert f"Project: Plan2Space Rebuild · https://github.com/orgs/Plan2Space/projects/2 · {REBUILD_ID}" in d
    assert "T-7 In Progress agent-bar#7" in d


from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def test_panel_shows_project_identity_counts_and_items(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.board = col.collect_board(lambda a: (0, project([item("T-7", "In Progress", 7), item("T-13", "Backlog", 8)])), tg.Targets())
    bar.on_snapshot(s)
    text = bar.details.backlog.text()
    assert "Plan2Space Rebuild" in text and REBUILD_ID in text and 'href="https://github.com/orgs/Plan2Space/projects/2"' in text
    assert "In Progress <b>1</b>" in text and "T-7" in text and "T-13" in text


def test_panel_marks_the_legacy_board_and_shows_errors_as_unavailable(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.board = col.collect_board(lambda a: (0, project([item("LEG-1", "GPT Review")], title="Legado", pid="PVT_legacy",
                                                        url="https://github.com/orgs/GFCDOTA/projects/1")), tg.load_targets({"AGENT_BAR_BOARD": "legacy"}))
    bar.on_snapshot(s)
    assert "LEGADO" in bar.details.backlog.text()
    s.board = col.collect_board(lambda a: (1, "HTTP 401: Bad credentials"), tg.Targets())
    bar.on_snapshot(s)
    text = bar.details.backlog.text()
    assert "INDISPONÍVEL" in text and "Bad credentials" in text and "<b>0</b>" not in text


def test_project_link_in_the_panel_opens_in_the_browser(gui):  # noqa: F811
    bar, _, _ = gui
    assert bar.details.backlog.openExternalLinks() is True
