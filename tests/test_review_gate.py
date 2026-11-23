"""H-013: PR que toca a Revisao visual so passa com o bloco do gate local (testes com o evidence REAL), travado no
HEAD, checkout limpo e "2 passed". PR que nao toca a Revisao visual nao precisa de nada."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_bar import review_gate as rg

HEAD = "a" * 40
EV = "b" * 40


def block(ev=EV, clean="limpo", ab=HEAD, result="2 passed"):
    return f"texto\n{rg.BEGIN}\nevidence: {ev} ({clean})\nagent-bar: {ab}\nresultado: {result}\n{rg.END}\n"


def test_a_pr_that_does_not_touch_the_review_needs_nothing():
    assert rg.check_pr("", ["agent_bar/platform/sessions.py", "README.md"], HEAD) == []


@pytest.mark.parametrize("path", ["agent_bar/cockpit/evidence.py", "agent_bar/cockpit/review_report.py",
                                  "agent_bar/cockpit/ui/review_tab.py", "agent_bar\\cockpit\\ui\\human_panel.py"])
def test_a_review_pr_without_the_block_fails(path):
    (err,) = rg.check_pr("so descricao", [path], HEAD)
    assert "Revisao visual" in err and "review_gate run" in err


def test_a_valid_block_at_the_pr_head_passes():
    assert rg.check_pr(block(), ["agent_bar/cockpit/evidence.py"], HEAD) == []


@pytest.mark.parametrize("kw,expected", [
    ({"ab": "c" * 40}, "HEAD do PR"),
    ({"clean": "SUJO"}, "sujo"),
    ({"result": "2 skipped"}, "pulado ou falho"),
    ({"result": "1 failed, 1 passed"}, "pulado ou falho"),
    ({"ev": "nao-e-sha"}, "evidence"),
])
def test_stale_dirty_skipped_or_malformed_blocks_fail(kw, expected):
    errs = rg.check_pr(block(**kw), ["agent_bar/cockpit/evidence.py"], HEAD)
    assert errs and any(expected in e for e in errs)


def test_the_pr_template_does_not_contain_the_markers():
    text = (rg.ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert rg.BEGIN not in text and rg.END not in text and "review_gate run" in text


class FakeRun:
    def __init__(self, pytest_out, status=""):
        self.pytest_out, self.status = pytest_out, status

    def __call__(self, argv, **kw):
        if argv[:1] == ["git"]:
            out = {"rev-parse": EV if "evidence" in argv[2] else HEAD, "status": self.status}[argv[3]]
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")
        return subprocess.CompletedProcess(argv, 0 if "passed" in self.pytest_out and "failed" not in self.pytest_out else 1,
                                           self.pytest_out, "")


def test_run_local_builds_the_block_that_check_pr_accepts(tmp_path):
    out = rg.run_local(Path("E:/evidence"), repo=Path("E:/agent-bar"),
                       run=FakeRun("..\n========= 2 passed in 1.43s =========\n"))
    assert out["passed"] and out["result"] == "2 passed"
    assert rg.check_pr(out["block"], ["agent_bar/cockpit/evidence.py"], HEAD) == []


@pytest.mark.parametrize("pytest_out,status,why", [
    ("ss\n===== 2 skipped in 0.1s =====\n", "", "2 skipped"),
    ("..\n===== 2 passed in 1s =====\n", " M tools/verify.py", "SUJO"),
])
def test_run_local_reports_a_failing_gate(pytest_out, status, why):
    out = rg.run_local(Path("E:/evidence"), repo=Path("E:/agent-bar"), run=FakeRun(pytest_out, status))
    assert not out["passed"] and why in out["block"]
    assert rg.check_pr(out["block"], ["agent_bar/cockpit/evidence.py"], HEAD) != []
