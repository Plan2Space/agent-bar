"""UX-09 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): PRs cobrem os 8 repos do Plan2Space (D-VS-7 / VS-I2), o CI do PR aparece
dentro do PR, a lista diz a hora da leitura e um erro do `gh` nunca deixa a tabela anterior na tela sem
"desatualizado desde". Repo que não foi coletado aparece "não coletado", nunca "0". Contra o `gh` falso, sem rede."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import github as G
from agent_bar.cockpit.github import GhClient

FAKE = Path(__file__).with_name("fake_gh.py")
ORG = "Plan2Space"
EIGHT = ["agent-bar", "agent-runtime", "claude-engineering-skills", "design-library", "engineering-specs", "evidence",
         "sketchup-mcp", "sketchup-mcp-next"]

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def gh(tmp_path, monkeypatch):
    log = tmp_path / "gh_argv.jsonl"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.delenv("AGENT_BAR_COCKPIT_REPOS", raising=False)
    yield GhClient(exe=[sys.executable, str(FAKE)])
    for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
        argv = json.loads(line)
        assert not set(argv) & G.WRITE_VERBS and tuple(argv[:2]) in G.READ_ONLY, argv     # continua só leitura


def wait(cond, timeout=15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def tab_for(gh, repos, clock=lambda: 1_700_000_000.0):
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    return PrsTab(gh, repos=repos, clock=clock)


def dispose(w):
    from PySide6.QtCore import QCoreApplication, QEvent
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_the_default_list_has_the_eight_plan2space_repos(monkeypatch):
    monkeypatch.delenv("AGENT_BAR_COCKPIT_REPOS", raising=False)
    assert G.configured_repos(ORG) == [f"{ORG}/{n}" for n in EIGHT]


def test_without_gh_the_repo_list_falls_back_to_the_eight(monkeypatch):
    monkeypatch.delenv("AGENT_BAR_COCKPIT_REPOS", raising=False)
    assert GhClient(exe=["definitely-no-gh-here"]).repos(ORG) == [f"{ORG}/{n}" for n in EIGHT]


def test_all_repos_is_the_default_selection_and_lists_every_repo(gh):
    repos = [f"{ORG}/{n}" for n in EIGHT]
    tab = tab_for(gh, repos)
    try:
        assert tab.repo_box.itemText(0) == "todos (8)" and tab.repo_box.currentIndex() == 0
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 16)                       # 2 PRs falsos × 8 repos
        assert {tab.tree.topLevelItem(i).text(1) for i in range(16)} == set(EIGHT)    # coluna Repo
        assert "lido às" in tab.fresh.text() and "ao vivo" in tab.fresh.text()
    finally:
        dispose(tab)


def test_a_repo_that_failed_is_not_collected_never_zero(gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_FAIL_REPOS", f"{ORG}/evidence")
    tab = tab_for(gh, [f"{ORG}/agent-bar", f"{ORG}/evidence"])
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        assert "evidence: não coletado" in tab.fresh.text()
        assert "0 PR" not in tab.fresh.text()
    finally:
        dispose(tab)


def test_a_gh_error_with_the_previous_table_says_outdated_since(gh, monkeypatch):
    tab = tab_for(gh, [f"{ORG}/agent-bar"], clock=lambda: 1_700_000_000.0)
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        monkeypatch.setenv("FAKE_GH_MODE", "noauth")
        tab.refresh()
        assert wait(lambda: "desatualizado desde" in tab.fresh.text())
        assert tab.tree.topLevelItemCount() == 2                                       # a tabela fica, COM rótulo
        assert "lido às" not in tab.fresh.text() and "gh sem login" in tab.fresh.text()
    finally:
        dispose(tab)


def test_a_gh_error_for_another_repo_clears_the_table_instead_of_showing_the_old_one(gh, monkeypatch):
    tab = tab_for(gh, [f"{ORG}/agent-bar", f"{ORG}/evidence"])
    try:
        tab.repo_box.setCurrentIndex(1)                                                  # só agent-bar
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        monkeypatch.setenv("FAKE_GH_MODE", "noauth")
        tab.repo_box.setCurrentIndex(2)                                                  # só evidence: nunca lido
        tab.refresh()
        assert wait(lambda: "indisponível" in tab.fresh.text())
        assert tab.tree.topLevelItemCount() == 0
    finally:
        dispose(tab)


def test_the_pr_ci_shows_inside_the_pr_matching_its_head(gh):
    tab = tab_for(gh, [f"{ORG}/agent-bar"])
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))                               # PR 7: HEAD aaaa…
        assert wait(lambda: "CI deste PR" in tab.detail.toPlainText())
        text = tab.detail.toPlainText()
        assert "ci" in text and "failure" in text and "actions/runs/101" in tab.detail.toHtml()
        tab.tree.setCurrentItem(tab.tree.topLevelItem(1))                               # PR 8: HEAD bbbb…, sem run
        assert wait(lambda: "#8" in tab.detail.toPlainText())
        assert "nenhuma execução do Actions para o HEAD" in tab.detail.toPlainText()
    finally:
        dispose(tab)


def test_in_all_repos_the_detail_and_the_review_use_the_rows_repo(gh, tmp_path):
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    (tmp_path / "apps" / "evidence" / ".git").mkdir(parents=True)
    tab = PrsTab(gh, repos=[f"{ORG}/agent-bar", f"{ORG}/evidence"], apps_root=tmp_path / "apps")
    got: list = []
    tab.review_requested.connect(lambda ws, prompt: got.append((ws, prompt)))
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 4)
        row = next(tab.tree.topLevelItem(i) for i in range(4) if tab.tree.topLevelItem(i).text(1) == "evidence")
        tab.tree.setCurrentItem(row)
        assert wait(lambda: tab.review_btn.isEnabled())
        tab.request_review()
        assert got and got[0][0] == str((tmp_path / "apps" / "evidence").resolve())
        assert f"{ORG}/evidence#" in got[0][1]
    finally:
        dispose(tab)


def test_when_only_the_diff_fails_the_pr_and_its_ci_still_show_with_the_reason(gh, monkeypatch):
    """A cota REST do GitHub acaba (o `gh pr diff` e o `gh run list` usam REST; o `gh pr view` usa GraphQL): o detalhe
    não some inteiro — o PR aparece, e o diff diz por que não veio."""
    monkeypatch.setenv("FAKE_GH_FAIL_DIFF", "1")
    tab = tab_for(gh, [f"{ORG}/agent-bar"])
    try:
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
        assert wait(lambda: "#7" in tab.detail.toPlainText())
        text = tab.detail.toPlainText()
        assert "CI deste PR" in text and "failure" in text                          # o CI continua no PR
        assert "diff indisponível" in text and "rate limit" in text
        assert tab.open_btn.isEnabled()
        assert not tab.review_btn.isEnabled() and "sem o diff" in tab.review_btn.toolTip()   # revisão sem diff, não
    finally:
        dispose(tab)
