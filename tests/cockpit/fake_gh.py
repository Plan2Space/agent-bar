"""`gh` falso para os testes da F3: responde JSON fixo e registra cada argv (uma linha JSON) em $FAKE_GH_LOG.

FAKE_GH_MODE=noauth -> todo comando falha como `gh` sem login. Qualquer subcomando desconhecido sai com 2.
"""
from __future__ import annotations

import json
import os
import sys

SHA = "a" * 40
PRS = [
    {"number": 7, "title": "feat: coisa", "author": {"login": "fmodesto30"}, "isDraft": False, "state": "OPEN",
     "baseRefName": "main", "headRefName": "feat/coisa", "headRefOid": SHA, "mergeable": "MERGEABLE",
     "reviewDecision": "REVIEW_REQUIRED", "url": "https://github.com/Plan2Space/agent-bar/pull/7", "updatedAt": "2026-11-22T10:00:00Z",
     "statusCheckRollup": [{"name": "test (3.11)", "conclusion": "SUCCESS"}, {"name": "test (3.12)", "conclusion": "FAILURE"}]},
    {"number": 8, "title": "docs: x", "author": {"login": "fmmodesto"}, "isDraft": True, "state": "OPEN",
     "baseRefName": "main", "headRefName": "docs/x", "headRefOid": "b" * 40, "mergeable": "UNKNOWN",
     "reviewDecision": "", "url": "https://github.com/Plan2Space/agent-bar/pull/8", "updatedAt": "2026-11-22T09:00:00Z",
     "statusCheckRollup": [{"name": "test", "status": "IN_PROGRESS"}]},
]
RUNS = [
    {"databaseId": 101, "displayTitle": "feat: coisa", "workflowName": "ci", "headBranch": "feat/coisa", "headSha": SHA,
     "status": "completed", "conclusion": "failure", "event": "pull_request", "createdAt": "2026-11-22T10:01:00Z",
     "url": "https://github.com/Plan2Space/agent-bar/actions/runs/101"},
]
LOG = "test (3.12)\tTest\tcollecting\ntest (3.12)\tTest\tFAILED tests/test_x.py::test_y\ntest (3.12)\tTest\t1 failed, 9 passed\n"


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\n")
    if os.environ.get("FAKE_GH_MODE") == "noauth":
        sys.stderr.write("To get started with GitHub CLI, please run:  gh auth login\n")
        return 4
    sub = tuple(argv[:2])
    out: object
    if sub == ("auth", "status"):
        sys.stdout.write("Logged in to github.com\n")
        return 0
    if sub == ("pr", "list"):
        out = PRS
    elif sub == ("pr", "view"):
        n = int(argv[2])
        out = {**next(p for p in PRS if p["number"] == n), "body": "Corpo do PR", "comments": [
            {"author": {"login": "fmmodesto"}, "body": "parece bom"}]}
    elif sub == ("pr", "diff"):
        size = int(os.environ.get("FAKE_GH_DIFF_SIZE", "0") or 0)
        sys.stdout.write("diff --git a/x.py b/x.py\n+print('oi')\n" + "+" * size)
        return 0
    elif sub == ("run", "list"):
        out = RUNS
    elif sub == ("run", "view") and "--json" in argv:
        out = {"jobs": [{"name": "test (3.11)", "conclusion": "success"}, {"name": "test (3.12)", "conclusion": "failure"}]}
    elif sub == ("run", "view"):
        sys.stdout.write(LOG)
        return 0
    elif sub == ("repo", "list"):
        out = [{"name": "agent-runtime"}, {"name": "agent-bar"}]
    else:
        sys.stderr.write(f"unknown command {sub}\n")
        return 2
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
