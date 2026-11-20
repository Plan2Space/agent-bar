"""M1 (alvos configuraveis, padrao Plan2Space Project #2) e M2 (proveniencia: "LIDO", nunca "REAL")."""
from __future__ import annotations

import json
import sqlite3

from agent_bar import collectors as col
from agent_bar import targets as tg
from agent_bar.observability import collect_observation

PLAN2SPACE_COLUMNS = ("Backlog", "Ready", "In Progress", "Review", "Blocked", "Done")


# ------------------------------------------------------------------------------------------------ M1 alvos
def test_default_targets_are_the_plan2space_org_and_project_2():
    t = tg.load_targets({})
    assert t.org == "Plan2Space" and t.project == 2 and t.columns == PLAN2SPACE_COLUMNS
    assert t.org_url == "https://github.com/Plan2Space"
    assert t.project_url == "https://github.com/orgs/Plan2Space/projects/2"


def test_targets_can_be_overridden_by_environment():
    t = tg.load_targets({"AGENT_BAR_ORG": "Acme", "AGENT_BAR_PROJECT": "7", "AGENT_BAR_COLUMNS": "Todo, Doing ,Done"})
    assert (t.org, t.project, t.columns) == ("Acme", 7, ("Todo", "Doing", "Done"))


def test_an_invalid_project_number_falls_back_to_the_default_and_says_so():
    t = tg.load_targets({"AGENT_BAR_PROJECT": "dois"})
    assert t.project == 2 and "AGENT_BAR_PROJECT" in t.warning


def test_an_org_name_with_unsafe_characters_is_refused():
    t = tg.load_targets({"AGENT_BAR_ORG": 'x"){evil}'})
    assert t.org == "Plan2Space" and "AGENT_BAR_ORG" in t.warning


def _gql_items(statuses):
    nodes = [{"fieldValueByName": {"name": s} if s else None} for s in statuses]
    return json.dumps({"data": {"organization": {"projectV2": {"items": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}})


def test_backlog_uses_the_project_2_columns_and_queries_the_configured_org():
    seen = []

    def gh(args):
        seen.append(" ".join(args))
        return 0, _gql_items(["Backlog", "Backlog", "Review", "In Progress", None])
    counts, err, _ = col.collect_backlog(gh, owner="Plan2Space", number=2, columns=PLAN2SPACE_COLUMNS)
    assert err is None and tuple(counts) == PLAN2SPACE_COLUMNS
    assert counts["Backlog"] == 2 and counts["Review"] == 1 and counts["Done"] == 0
    assert 'organization(login:\\"Plan2Space\\")' not in seen[0] and 'login:"Plan2Space"' in seen[0] and "projectV2(number:2)" in seen[0]


def test_a_status_outside_the_configured_columns_is_counted_not_hidden():
    counts, err, _ = col.collect_backlog(lambda a: (0, _gql_items(["GPT Review", "Done"])), columns=PLAN2SPACE_COLUMNS)
    assert err is None and counts["GPT Review"] == 1 and counts["Done"] == 1


def _search(nodes, more=False, cursor=None):
    return json.dumps({"data": {"search": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}})


def _org_pr(repo, n, review=None, checks="SUCCESS", head="a" * 40):
    reviews = [] if review is None else [{"state": review, "commit": {"oid": head}}]
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/{repo}/pull/{n}", "isDraft": False, "mergeable": "MERGEABLE",
            "headRefOid": head, "repository": {"nameWithOwner": repo}, "reviews": {"nodes": reviews},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]}}


def test_org_pr_queue_covers_every_repo_of_the_org_and_keeps_the_repo_name():
    seen = []

    def gh(args):
        seen.append(" ".join(args))
        return 0, _search([_org_pr("Plan2Space/agent-bar", 11, review="APPROVED"), _org_pr("Plan2Space/agent-runtime", 4), {}])
    q = col.collect_org_pr_queue(gh, owner="Plan2Space")
    assert q.error is None and [(r.repo, r.number, r.state) for r in q.rows] == [
        ("Plan2Space/agent-bar", 11, "pronto"), ("Plan2Space/agent-runtime", 4, "aguarda")]
    assert "org:Plan2Space is:pr is:open" in seen[0]


def test_org_pr_queue_failure_is_an_explicit_error():
    q = col.collect_org_pr_queue(lambda a: (1, "gh: offline"), owner="Plan2Space")
    assert q.rows == [] and "rc=1" in q.error


def test_org_pr_queue_refuses_an_incomplete_count():
    q = col.collect_org_pr_queue(lambda a: (0, _search([_org_pr("Plan2Space/x", 1)], more=True, cursor="C")), owner="Plan2Space", max_pages=2)
    assert q.rows == [] and "incompleta" in q.error


def test_snapshot_reads_backlog_and_queue_from_the_configured_targets(tmp_path):
    seen = []

    def gh(args):
        seen.append(" ".join(args))
        return 1, "offline"
    t = tg.Targets(org="Acme", project=9)
    col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=gh, targets=t)
    joined = "\n".join(seen)
    assert "projectV2(number:9)" in joined and 'login:"Acme"' in joined and "org:Acme is:pr is:open" in joined


# ------------------------------------------------------------------------------------------------ M2 proveniencia
def _runtime_db(path, last_at=1_000.0):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, job_id TEXT, status TEXT, attempt INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3, parent_event_id INTEGER, lease_until REAL DEFAULT 0, error TEXT,
            created_at REAL, updated_at REAL);
        CREATE TABLE transitions (id INTEGER PRIMARY KEY, event_id INTEGER, at REAL, to_status TEXT, detail TEXT);
        CREATE TABLE subprocess_runs (id INTEGER PRIMARY KEY, agent TEXT, pid INTEGER, started_at REAL, ended_at REAL,
            outcome TEXT, stdout_path TEXT, stderr_path TEXT);
        CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_id INTEGER, status TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('schema_version', '1');
    """)
    db.execute("INSERT INTO events VALUES (1,'JOB','job-1','DONE',1,3,NULL,0,NULL,?,?)", (last_at, last_at))
    db.execute("INSERT INTO transitions VALUES (1,1,?,'DONE','')", (last_at,))
    db.commit()
    db.close()


def test_a_readable_store_is_labelled_lido_with_schema_and_age_never_real(tmp_path):
    db = tmp_path / "store.db"
    _runtime_db(db, last_at=1_000.0)
    obs = collect_observation(tmp_path, db_path=db, now=1_090.0)
    assert obs.source == "lido" and obs.schema_version == "1" and obs.last_event_age_s == 90
    assert "real" not in obs.reason.lower()


def test_an_empty_store_has_no_age_instead_of_a_fake_zero(tmp_path):
    db = tmp_path / "store.db"
    _runtime_db(db)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM transitions")
    con.execute("DELETE FROM events")
    con.commit()
    con.close()
    obs = collect_observation(tmp_path, db_path=db, now=2_000.0)
    assert obs.source == "lido" and obs.last_event_age_s is None


def test_codex_rate_limit_shows_its_age_so_an_old_state_is_not_read_as_current(tmp_path):
    import datetime as dt
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "last_review.json").write_text(json.dumps(
        {"status": "failed", "pr": 641, "sha": "abc1234", "error": "RATE_LIMITED: usage limit", "at": "2026-11-18T10:00:00Z"}), encoding="utf-8")
    now = dt.datetime(2026, 11, 20, 10, 0, tzinfo=dt.timezone.utc)
    st = col.collect_codex([], [], tmp_path, which=lambda n: "codex", now=now)
    assert st.label == "Limite de uso" and "registrado ha 2d" in st.detail


def test_diagnostic_lists_the_org_pr_queue_or_its_error(tmp_path):
    from agent_bar.models import PrQueue, PrRow
    s = col.collect_snapshot(tmp_path, procs_fn=lambda: [], task_fn=lambda: None, gh=lambda a: (1, "offline"), targets=tg.Targets())
    assert "PRs abertos: indisponivel (erro: gh api graphql rc=1" in s.diagnostic()
    s.pr_queue = PrQueue(rows=[PrRow(11, "t", "https://github.com/Plan2Space/agent-bar/pull/11", "aguarda", "Plan2Space/agent-bar")], synced="X")
    assert "PRs abertos: Plan2Space/agent-bar#11=aguarda (sync X)" in s.diagnostic()
