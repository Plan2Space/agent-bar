"""E6: integração com a revisão humana — o cockpit mostra, liga ao GitHub e resume; NUNCA escreve veredito humano."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import agent_bar
from agent_bar.cockpit import evidence as E
from agent_bar.cockpit import human_review as HR
from agent_bar.cockpit import review_report as RR
from tests.cockpit.evidence_fixture import build

VALID_HUMAN = {"kind": "human", "verdict": "CHANGES_REQUESTED", "reviewer_login": "fmodesto30", "date": "2026-11-22",
               "quote": "a cabeceira está alta demais", "statement_ref": "https://github.com/Plan2Space/evidence/pull/9#issuecomment-1",
               "run_commit_sha": "c" * 40}


def _git(root: Path) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "x"]):
        subprocess.run(cmd, cwd=root, env=env, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "ev")


def runs(root):
    by = {r.stage: r for r in E.load_catalog(root, expected={}).runs}
    return by["A"], by["B"]


def test_pending_without_human_file(repo):
    _, b = runs(repo)
    d = HR.human_detail(b)
    assert d["state"] == "PENDING_HUMAN" and not d["accepted"] and "quote" not in d


def test_accepted_human_file_shows_quote_and_ref_and_says_who_checks_authorship(repo):
    (repo / "planta_x" / "quarto" / "B" / "run-b" / "verdicts" / "human.json").write_text(json.dumps(VALID_HUMAN), encoding="utf-8")
    _, b = runs(repo)
    d = HR.human_detail(b)
    assert d["accepted"] and d["state"] == "CHANGES_REQUESTED" and d["quote"] == "a cabeceira está alta demais"
    assert d["statement_ref"].endswith("#issuecomment-1") and "CI do evidence" in d["authorship"]


def test_invalid_human_file_is_not_shown_as_a_verdict(repo):
    (repo / "planta_x" / "quarto" / "B" / "run-b" / "verdicts" / "human.json").write_text(json.dumps({"kind": "human", "verdict": "APPROVED"}),
                                                                                         encoding="utf-8")
    _, b = runs(repo)
    d = HR.human_detail(b)
    assert not d["accepted"] and d["state"].startswith("PENDING_HUMAN") and "inválidos" in d["problem"]


def test_github_url_is_pinned_to_the_checkout_commit(repo):
    sha = _git(repo)
    _, b = runs(repo)
    assert HR.github_url(b, repo) == f"https://github.com/Plan2Space/evidence/tree/{sha}/planta_x/quarto/B/run-b"


def test_github_url_absent_without_git(repo):
    _, b = runs(repo)
    assert HR.github_url(b, repo) is None


def test_decision_summary_is_local_not_a_verdict_and_complete(repo, tmp_path):
    a, b = runs(repo)
    rep = RR.DiffReport.new(a, b)
    rep.add(RR.Finding("composicao", "estetico", "cabeceira corta o quadro", "top"))
    before = {p.relative_to(repo).as_posix(): p.stat().st_mtime_ns for p in repo.rglob("*")}
    out = HR.decision_summary(a, b, rep, root=tmp_path / "data", evidence_root=repo, clock=lambda: 0.0)
    text = out.read_text(encoding="utf-8")
    assert not E.is_inside(out, repo) and "Não é veredito" in text and "HUMAN_APPROVAL: PENDING_HUMAN" in text
    assert "cabeceira corta o quadro" in text and "statement_ref" in text and "verdicts/human.json" in text
    assert {p.relative_to(repo).as_posix(): p.stat().st_mtime_ns for p in repo.rglob("*")} == before


def test_decision_summary_refuses_invalid_run(repo, tmp_path):
    a, _ = runs(repo)
    c = next(r for r in E.load_catalog(repo, expected={}).runs if r.stage == "C")
    with pytest.raises(ValueError):
        HR.decision_summary(a, c, None, root=tmp_path / "d", evidence_root=repo)


def test_no_cockpit_code_writes_a_human_verdict():
    """Varredura do código: nenhuma linha que menciona human.json grava arquivo (o veredito é só do Felipe)."""
    pkg = Path(agent_bar.__file__).parent
    write = re.compile(r"write_text|write_bytes|atomic_write_json|open\([^)]*['\"][wa]|shutil\.copy|os\.replace")
    offenders = []
    for py in pkg.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "human.json" in line and write.search(line):
                offenders.append(f"{py.name}:{i}")
    assert offenders == []


def test_panel_shows_state_reviews_and_makes_summary(repo, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar import controls as ctl
    from agent_bar.cockpit.ui.human_panel import HumanPanel
    rev = repo / "planta_x" / "quarto" / "B" / "run-b" / "reviews" / "llm"
    rev.mkdir(parents=True)
    (rev / "claude-opus-5.5-2026-11-22.md").write_text("# revisão\nproporção ok, textura ausente", encoding="utf-8")
    _git(repo)
    a, b = runs(repo)
    opened = []
    monkeypatch.setattr(ctl.webbrowser, "open", lambda url: opened.append(url))
    panel = HumanPanel(lambda: repo)
    try:
        panel.set_pair(a, b)
        assert "PENDING_HUMAN" in panel.state.text() and panel.reviews.count() == 1
        panel.reviews.setCurrentRow(0)
        assert "textura ausente" in panel.review_text.toPlainText()
        url = panel.open_github()
        assert opened == [url] and url.startswith("https://github.com/Plan2Space/evidence/tree/")
        out = panel.make_summary()
        assert out is not None and out.is_file() and "não é veredito" in panel.status.text()
    finally:
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
