"""F3: PRs e Actions via `gh` SOMENTE LEITURA, contra um `gh` falso (tests/cockpit/fake_gh.py). Sem rede."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import github as G
from agent_bar.cockpit.github import GhClient, GhError, read_only_argv

FAKE = Path(__file__).with_name("fake_gh.py")
REPO = "Plan2Space/agent-bar"


@pytest.fixture
def gh(tmp_path, monkeypatch):
    log = tmp_path / "gh_argv.jsonl"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    client = GhClient(exe=[sys.executable, str(FAKE)])
    yield client
    # auditoria: NENHUM argv executado contem verbo de escrita, e todo comando de repo leva --repo
    argvs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    for argv in argvs:
        assert not set(argv) & G.WRITE_VERBS, f"argv de escrita executado: {argv}"
        assert tuple(argv[:2]) in G.READ_ONLY, argv
        if tuple(argv[:2]) not in (("auth", "status"), ("repo", "list")):
            assert argv[argv.index("--repo") + 1] == REPO


@pytest.mark.parametrize("verb", sorted(G.WRITE_VERBS))
def test_every_write_verb_is_refused_before_any_process(verb):
    for sub in (("pr", verb), ("run", verb), (verb, "list")):
        with pytest.raises(GhError) as ei:
            read_only_argv(sub, REPO)
        assert ei.value.kind == "refused"


def test_unknown_flag_bad_repo_and_flag_like_positional_are_refused():
    with pytest.raises(GhError):
        read_only_argv(("pr", "list"), REPO, {"--body": "x"})
    with pytest.raises(GhError):
        read_only_argv(("pr", "list"), "not a repo")
    with pytest.raises(GhError):
        read_only_argv(("pr", "list"), None)
    with pytest.raises(GhError):
        read_only_argv(("pr", "view"), REPO, positional=["--admin"])
    with pytest.raises(GhError):
        read_only_argv(("pr", "view"), REPO, positional=["7; rm -rf /"])
    assert read_only_argv(("pr", "view"), REPO, {"--json": "number,title"}, ["7"]) == [
        "pr", "view", "7", "--repo", REPO, "--json", "number,title"]


def test_client_has_no_write_method():
    public = {n for n in dir(GhClient) if not n.startswith("_")}
    assert public == {"auth_ok", "repos", "prs", "pr", "pr_diff", "runs", "run_jobs", "run_log", "runner",
                      "timeout"}


def test_list_and_detail_prs(gh):
    prs = gh.prs(REPO)
    assert [p["number"] for p in prs] == [7, 8]
    assert G.checks_summary(prs[0]["statusCheckRollup"]) == "1 ok · 1 falhou"
    assert G.checks_summary(prs[1]["statusCheckRollup"]) == "1 pendente"
    pr = gh.pr(REPO, 7)
    assert pr["body"] == "Corpo do PR" and pr["headRefOid"] == "a" * 40
    assert gh.pr_diff(REPO, 7).startswith("diff --git")


def test_runs_jobs_and_log(gh):
    runs = gh.runs(REPO, branch="feat/coisa")
    assert runs[0]["databaseId"] == 101
    assert [j["conclusion"] for j in gh.run_jobs(REPO, 101)] == ["success", "failure"]
    assert "FAILED tests/test_x.py::test_y" in gh.run_log(REPO, 101, failed_only=True)
    assert ["--branch", "feat/coisa"] == gh.calls[0][gh.calls[0].index("--branch"):gh.calls[0].index("--branch") + 2]


def test_repos_from_gh_and_env(gh, monkeypatch):
    assert gh.repos("Plan2Space") == ["Plan2Space/agent-bar", "Plan2Space/agent-runtime"]
    monkeypatch.setenv("AGENT_BAR_COCKPIT_REPOS", "agent-bar, Other/x")
    assert gh.repos("Plan2Space") == ["Plan2Space/agent-bar", "Other/x"]
    assert G.configured_repos("Plan2Space") == ["Plan2Space/agent-bar", "Other/x"]


def test_diff_is_truncated(gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_DIFF_SIZE", "70000")
    d = gh.pr_diff(REPO, 7)
    assert len(d) < 60_100 and "truncado" in d


def test_not_logged_in_is_explicit(gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_MODE", "noauth")
    with pytest.raises(GhError) as ei:
        gh.prs(REPO)
    assert ei.value.kind == "auth"
    assert gh.auth_ok() is False


def test_missing_gh_is_not_found():
    with pytest.raises(GhError) as ei:
        GhClient(exe=["definitely-no-gh-here"]).prs(REPO)
    assert ei.value.kind == "not_found"


def test_gh_env_drops_tokens():
    env = G.gh_env({"PATH": "x", "GH_TOKEN": "s", "GITHUB_TOKEN": "s", "github_token": "s"})
    assert env == {"PATH": "x"}


def test_review_prompt_has_number_sha_and_diff():
    pr = {"number": 7, "title": "t", "headRefOid": "a" * 40, "baseRefName": "main", "headRefName": "feat/x"}
    text = G.review_prompt(REPO, pr, "diff --git a b")
    assert "Plan2Space/agent-bar#7" in text and "a" * 40 in text and "diff --git a b" in text
    assert "Não altere arquivos" in text


# ------------------------------------------------------------------------------------------------- GUI (offscreen)

QtWidgets = None


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return pytest.importorskip("PySide6.QtWidgets")


def wait(cond, timeout=15.0):
    app = _qt().QApplication.instance() or _qt().QApplication([])
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def dispose(w):
    from PySide6.QtCore import QCoreApplication, QEvent
    if hasattr(w, "shutdown"):
        w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_prs_tab_lists_details_and_requests_review(gh, tmp_path):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    (tmp_path / "apps" / "agent-bar" / ".git").mkdir(parents=True)
    tab = PrsTab(gh, repos=[REPO], apps_root=tmp_path / "apps")
    got: list = []
    tab.review_requested.connect(lambda ws, prompt: got.append((ws, prompt)))
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        assert tab.tree.topLevelItem(1).text(1).startswith("[rascunho]")
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
        assert wait(lambda: tab.review_btn.isEnabled())
        assert "a" * 40 in tab.detail.toPlainText() and "parece bom" in tab.detail.toPlainText()
        tab.request_review()
        assert got and got[0][0] == str((tmp_path / "apps" / "agent-bar").resolve())
        assert "a" * 40 in got[0][1] and "diff --git" in got[0][1]
    finally:
        dispose(tab)


def test_review_without_local_checkout_is_explained(gh, tmp_path):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    tab = PrsTab(gh, repos=[REPO], apps_root=tmp_path / "empty")
    got: list = []
    tab.review_requested.connect(lambda *a: got.append(a))
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
        assert wait(lambda: tab.review_btn.isEnabled())
        tab.request_review()
        assert got == [] and "sem checkout local" in tab.status.text()
    finally:
        dispose(tab)


def test_prs_tab_shows_auth_error(gh, monkeypatch):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    monkeypatch.setenv("FAKE_GH_MODE", "noauth")
    tab = PrsTab(gh, repos=[REPO])
    try:
        tab.refresh()
        assert wait(lambda: "[auth]" in tab.status.text())
    finally:
        dispose(tab)


def test_actions_tab_lists_runs_jobs_and_filters_log(gh):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from agent_bar.cockpit.ui.github_tabs import ActionsTab
    tab = ActionsTab(gh, repos=[REPO])
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 1)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
        assert wait(lambda: "FAILED" in tab.log.toPlainText())
        assert "test (3.12)" in tab.jobs.text()
        tab.search.setText("failed,")
        assert tab.log.toPlainText().strip().endswith("1 failed, 9 passed")
        assert "collecting" not in tab.log.toPlainText()
    finally:
        dispose(tab)


def test_window_review_opens_prefilled_chat_without_sending(gh, tmp_path, monkeypatch):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    ws = tmp_path / "ws"
    ws.mkdir()
    stub = Path(__file__).with_name("fake_acp_agent.py")
    win = CockpitWindow(gh=gh, repos=[REPO], profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(stub)))],
                        workspaces_fn=lambda: [tmp_path / "other"])
    try:
        assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Chat", "Fluxos", "Revisão visual", "Biblioteca", "PRs", "Actions", "Dev"]
        win.open_review(str(ws), "Revise o PR X#1")
        assert win.tabs.currentWidget() is win.chat
        assert win.chat.workspace() == str(ws)
        assert win.chat.input.toPlainText() == "Revise o PR X#1"
        assert win.chat.chat.messages == []                                     # nada foi enviado
    finally:
        dispose(win)


def test_actions_status_is_cleared_after_detail(gh):
    qtw = _qt()
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from agent_bar.cockpit.ui.github_tabs import ActionsTab
    tab = ActionsTab(gh, repos=[REPO])
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 1)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
        assert wait(lambda: "job(s)" in tab.status.text())
        assert "consultando" not in tab.status.text()
    finally:
        dispose(tab)
