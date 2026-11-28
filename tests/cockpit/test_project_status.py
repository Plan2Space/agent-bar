"""Contract tests dos coletores da Central de acompanhamento (§7, P0). Fixtures gravadas do `gh` real; sem rede."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bar.cockpit import project_status as ps
from agent_bar.cockpit.github import GhClient, GhError, read_only_argv

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "project_status"


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class Recorded:
    """Runner que responde com as fixtures gravadas, por subcomando/estado/repo. Repo sem fixture = falha do gh."""

    def __init__(self, fail_repos: tuple[str, ...] = ()):
        self.fail_repos = fail_repos

    def __call__(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        sub = tuple(argv[1:3])
        repo = argv[argv.index("--repo") + 1] if "--repo" in argv else ""
        if repo in self.fail_repos:
            return 1, "", "GraphQL: Could not resolve to a Repository"
        if sub == ("repo", "list"):
            return 0, json.dumps(_load("repo_list.json") + [{"name": "repo-novo"}]), ""
        if sub == ("project", "item-list"):
            return 0, json.dumps(_load("project_items.json")), ""
        if sub == ("pr", "list"):
            state = argv[argv.index("--state") + 1]
            if state == "all":                     # timeline (§7.5): fixture sintético
                return 0, (json.dumps(_load("timeline_prs_all.json")) if repo == "Plan2Space/agent-bar" else "[]"), ""
            if repo == "Plan2Space/agent-bar":
                return 0, json.dumps(_load(f"prs_{state}_agent-bar.json")), ""
            return 0, "[]", ""
        return 2, "", "unknown"


def client(**kw) -> GhClient:
    return GhClient(exe=[__file__], runner=Recorded(**kw))


NOW = 1_764_230_400.0  # 2025-11-27T08:00:00Z, fixo


def test_project_item_list_is_allowed_read_only_without_repo():
    argv = read_only_argv(("project", "item-list"), None, {"--owner": "Plan2Space", "--format": "json", "--limit": "500"},
                          positional=["1"])
    assert argv[:3] == ["project", "item-list", "1"] and "--repo" not in argv


def test_project_write_verbs_stay_refused():
    for sub in (("project", "item-edit"), ("project", "item-add"), ("project", "delete")):
        with pytest.raises(GhError):
            read_only_argv(sub, None)


def test_github_counts_come_from_the_recorded_prs():
    gh = ps.collect_github(client(), "Plan2Space", now=NOW)
    bar = gh.repo("Plan2Space/agent-bar")
    open_prs = _load("prs_open_agent-bar.json")
    assert bar.open_prs == len(open_prs)
    assert bar.ci_failing == sum(1 for p in open_prs if ps.ci_failed(p.get("statusCheckRollup")))
    assert bar.merged_recent == len(_load("prs_merged_agent-bar.json"))
    assert bar.last_activity == max(p["updatedAt"] for p in open_prs + _load("prs_merged_agent-bar.json"))


def test_awaiting_review_counts_non_draft_without_approval():
    gh = ps.collect_github(client(), "Plan2Space", now=NOW)
    open_prs = _load("prs_open_agent-bar.json")
    expected = sum(1 for p in open_prs if not p["isDraft"] and p.get("reviewDecision") != "APPROVED")
    assert gh.repo("Plan2Space/agent-bar").awaiting_review == expected


def test_all_eight_repos_are_monitored_and_new_ones_discovered():
    gh = ps.collect_github(client(), "Plan2Space", now=NOW)
    names = {r.repo for r in gh.repos}
    assert {f"Plan2Space/{n}" for n in ps.MONITORED} <= names
    assert gh.new_repos == ["Plan2Space/repo-novo"]


def test_a_repo_that_fails_is_unknown_not_zero():
    gh = ps.collect_github(client(fail_repos=("Plan2Space/evidence",)), "Plan2Space", now=NOW)
    ev = gh.repo("Plan2Space/evidence")
    assert ev.state == ps.UNKNOWN and ev.open_prs is None and ev.error
    assert gh.totals()["open_prs"] is None or gh.totals_partial


def test_totals_never_hide_an_unknown_repo():
    gh = ps.collect_github(client(fail_repos=("Plan2Space/evidence",)), "Plan2Space", now=NOW)
    assert gh.totals_partial is True
    assert gh.totals()["open_prs"] == len(_load("prs_open_agent-bar.json"))


def test_project_tasks_are_bucketed_from_the_board_status():
    proj = ps.collect_project(client(), "Plan2Space", 1, now=NOW)
    items = _load("project_items.json")["items"]
    by = {s: sum(1 for i in items if i["status"] == s) for s in {i["status"] for i in items}}
    assert proj.counts[ps.T_DONE] == by.get("Done", 0)
    assert proj.counts[ps.T_BLOCKED] == by.get("Blocked", 0)
    assert proj.counts[ps.T_IN_PROGRESS] == by.get("In Progress", 0) + by.get("GPT Review", 0)
    assert proj.total == len(items)


@pytest.mark.parametrize("status,proof,expected", [
    ("Backlog", None, ps.PLANEJADO),
    ("Ready", None, ps.PLANEJADO),
    ("In Progress", "NOT_TESTED", ps.EM_IMPLEMENTACAO),
    ("In Progress", "UNIT_PASS", ps.IMPLEMENTADO),
    ("GPT Review", "UNIT_PASS", ps.EM_REVISAO),
    ("Done", "UNIT_PASS", ps.INTEGRADO),          # PR mergeado / card Done sem prova de integração não é Comprovado
    ("Done", None, ps.INTEGRADO),
    ("Done", "SKETCHUP_PASS", ps.COMPROVADO),
    ("Done", "INTEGRATION_PASS", ps.COMPROVADO),
    ("Blocked", "NOT_TESTED", ps.BLOQUEADO),
    ("In Progress", "BLOCKED", ps.BLOQUEADO),
    ("Coluna Nova", None, ps.UNKNOWN),
])
def test_item_state_distinguishes_the_delivery_states(status, proof, expected):
    item = {"status": status, "title": "x"}
    if proof:
        item["estado da prova"] = proof
    assert ps.item_state(item) == expected


def test_specs_group_tasks_by_linked_spec_with_counts_not_percent():
    proj = ps.collect_project(client(), "Plan2Space", 1, now=NOW)
    for spec in proj.specs:
        assert sum(spec.states.values()) == spec.total
        assert not hasattr(spec, "percent")
    no_spec = [i for i in _load("project_items.json")["items"] if not i.get("sPEC vinculada")]
    assert proj.unlinked == len(no_spec)


def test_project_failure_is_unknown_with_reason():
    class Boom:
        def __call__(self, argv, timeout):
            return 1, "", "HTTP 401: authentication required"
    proj = ps.collect_project(GhClient(exe=[__file__], runner=Boom()), "Plan2Space", 1, now=NOW)
    assert proj.state == ps.UNKNOWN and proj.total is None and "auth" in proj.error


def test_overview_reports_health_per_source_and_read_time():
    ov = ps.collect_overview(client(fail_repos=("Plan2Space/evidence",)), "Plan2Space", now=NOW)
    assert ov.read_at == NOW
    health = dict(ov.health)
    assert health["github"].startswith("parcial")
    assert health["project"] == "ok"
    assert ov.active_agents is None          # sem fonte do Runtime neste PR: desconhecido, nunca 0 inventado


def test_fronts_come_from_the_project_field_and_missing_is_unknown():
    proj = ps.collect_project(client(), "Plan2Space", 1, now=NOW)
    # O board ainda não tem o campo "Frente" (a H cria): tudo cai em "desconhecida", nada inventado.
    assert [f.front for f in proj.fronts] == [ps.FRONT_UNKNOWN]
    assert proj.fronts[0].total == proj.total


def test_fronts_group_counts_by_the_front_field():
    items = [{"status": "Done", "frente": "D"}, {"status": "Blocked", "Frente": "D"}, {"status": "In Progress"}]

    class R:
        def __call__(self, argv, timeout):
            return 0, json.dumps({"items": items}), ""
    proj = ps.collect_project(GhClient(exe=[__file__], runner=R()), "Plan2Space", 1, now=NOW)
    d = next(f for f in proj.fronts if f.front == "D")
    assert d.total == 2 and d.counts[ps.T_DONE] == 1 and d.counts[ps.T_BLOCKED] == 1
    assert next(f for f in proj.fronts if f.front == ps.FRONT_UNKNOWN).counts[ps.T_IN_PROGRESS] == 1
def test_discovery_failure_is_unknown_not_no_new_repos():
    """Review #156 (MUST): `gh repo list` em 502 não pode virar "nenhum repo novo" com saúde ok."""
    class ListDown(Recorded):
        def __call__(self, argv, timeout):
            if tuple(argv[1:3]) == ("repo", "list"):
                return 1, "", "HTTP 502: Bad Gateway"
            return super().__call__(argv, timeout)
    gh_client = GhClient(exe=[__file__], runner=ListDown())
    gh = ps.collect_github(gh_client, "Plan2Space", now=NOW)
    assert gh.discovery == ps.UNKNOWN and "502" in gh.discovery_error
    assert gh.new_repos is None
    assert {r.repo for r in gh.repos} == {f"Plan2Space/{n}" for n in ps.MONITORED}   # os 8 seguem monitorados
    ov = ps.collect_overview(gh_client, "Plan2Space", now=NOW)
    assert dict(ov.health)["github"] != "ok" and "descoberta" in dict(ov.health)["github"]
