"""Timeline consolidada (§7.5): só eventos com fonte (gh e index.json do evidence), sem duplicar, com filtros por
repositório e período. Fixtures sintéticos (o agent-bar é público); sem rede."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bar.cockpit import project_timeline as tl
from agent_bar.cockpit.github import GhClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "project_status"
NOW = "2026-11-27T12:00:00Z"
ALL = json.loads((FIX / "timeline_prs_all.json").read_text(encoding="utf-8"))


class Runner:
    def __init__(self, fail: tuple[str, ...] = ()):
        self.fail, self.calls = fail, []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        repo = argv[argv.index("--repo") + 1]
        if repo in self.fail:
            return 1, "", "HTTP 502"
        assert argv[1:3] == ["pr", "list"] and argv[argv.index("--state") + 1] == "all"
        return 0, json.dumps(ALL if repo == "Plan2Space/exemplo" else []), ""


def gh(**kw) -> GhClient:
    return GhClient(exe=[__file__], runner=Runner(**kw))


def kinds(events):
    return [(e.kind, e.ref) for e in events]


def test_pr_events_come_from_created_and_merged_dates():
    tlr = tl.collect_github_events(gh(), ["Plan2Space/exemplo"])
    got = {(e.kind, e.ref): e.at for e in tlr.events}
    assert got[(tl.PR_CRIADO, "#1")] == "2026-11-20T08:00:00Z"
    assert got[(tl.PR_MERGEADO, "#1")] == "2026-11-21T12:00:00Z"
    assert (tl.PR_MERGEADO, "#2") not in got


def test_ci_event_only_when_every_check_finished_at_the_last_completion():
    got = {(e.kind, e.ref): e.at for e in tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events}
    assert got[(tl.CI_FALHOU, "#2")] == "2026-11-25T09:08:00Z"
    assert got[(tl.CI_APROVADO, "#1")] == "2026-11-20T08:09:00Z"   # StatusContext conta (createdAt)
    assert not any(ref == "#3" and k in (tl.CI_APROVADO, tl.CI_FALHOU) for k, ref in got)   # pendente: sem evento


def test_events_are_sorted_newest_first():
    ev = tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events
    stamps = [e.at for e in ev]
    assert stamps == sorted(stamps, reverse=True)


def test_reading_twice_does_not_duplicate():
    a = tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events
    b = tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events
    merged = tl.merge(a, b)
    assert len(merged) == len(a) and len({e.key for e in merged}) == len(merged)


def test_ci_key_includes_the_head_so_a_new_run_on_a_new_head_is_a_new_event():
    a = tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events
    ci = next(e for e in a if e.kind == tl.CI_FALHOU)
    assert "b" * 40 in ci.key


def test_repo_that_fails_is_reported_not_silently_empty():
    r = tl.collect_github_events(gh(fail=("Plan2Space/outro",)), ["Plan2Space/exemplo", "Plan2Space/outro"])
    assert r.failed == {"Plan2Space/outro": "failed: HTTP 502"} and r.events


def test_evidence_events_use_the_date_in_the_run_id_or_unknown():
    ev = tl.evidence_events(("20261122-planta_x-d1", "run-sem-data"), repo="Plan2Space/evidence")
    by = {e.ref: e for e in ev}
    assert by["20261122-planta_x-d1"].at == "2026-11-22" and by["20261122-planta_x-d1"].kind == tl.EVIDENCIA_PUBLICADA
    assert by["run-sem-data"].at is None


@pytest.mark.parametrize("period,expected", [
    ("24h", set()),
    ("7d", {("CI falhou", "#2"), ("PR criado", "#2"), ("PR criado", "#3"), ("PR mergeado", "#1"),
            ("Evidência publicada", "20261122-planta_x-d1")}),
])
def test_period_filter_is_relative_to_the_read_time_and_drops_undated(period, expected):
    ev = tl.merge(tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events,
                  tl.evidence_events(("20261122-planta_x-d1", "run-sem-data"), repo="Plan2Space/evidence"))
    got = {(e.kind, e.ref) for e in tl.filter_events(ev, period=period, now=NOW)}
    assert got == expected


def test_all_period_keeps_undated_events_at_the_end():
    ev = tl.merge(tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events,
                  tl.evidence_events(("run-sem-data",), repo="Plan2Space/evidence"))
    out = tl.filter_events(ev, period="tudo", now=NOW)
    assert out[-1].at is None and out[-1].ref == "run-sem-data"


def test_repo_filter():
    ev = tl.merge(tl.collect_github_events(gh(), ["Plan2Space/exemplo"]).events,
                  tl.evidence_events(("20261122-x",), repo="Plan2Space/evidence"))
    assert {e.repo for e in tl.filter_events(ev, repo="Plan2Space/evidence", now=NOW)} == {"Plan2Space/evidence"}


def test_collector_uses_only_read_only_gh():
    r = Runner()
    tl.collect_github_events(GhClient(exe=[__file__], runner=r), ["Plan2Space/exemplo"])
    assert all(c[1:3] == ["pr", "list"] for c in r.calls)


def test_evidence_snapshot_carries_the_published_run_ids():
    from agent_bar.cockpit import project_sources as src
    from tests.cockpit.test_project_sources import EVID, FakeGit
    ev = src.collect_evidence(EVID, now=0.0, git=FakeGit())
    assert len(ev.published_run_ids) == ev.published_runs == 7
    assert "run-1-a" in ev.published_run_ids


def test_overview_carries_a_deduplicated_timeline_and_its_failures():
    from agent_bar.cockpit import project_sources as src
    from agent_bar.cockpit import project_status as ps
    from tests.cockpit.test_project_sources import EVID, FakeGit
    from tests.cockpit.test_project_status import NOW as T
    from tests.cockpit.test_project_status import client
    ov = ps.collect_overview(client(fail_repos=("Plan2Space/evidence",)), "Plan2Space", now=T,
                             evidence=lambda: src.collect_evidence(EVID, now=T, git=FakeGit()), timeline=True)
    assert len({e.key for e in ov.timeline}) == len(ov.timeline) > 0
    assert sum(e.kind == tl.EVIDENCIA_PUBLICADA for e in ov.timeline) == 7
    assert "Plan2Space/evidence" in ov.timeline_failed
