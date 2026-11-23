"""H-015: versoes de SPEC consumidas ficam num contracts.lock conferido — no CI contra o codigo, localmente contra um
checkout de SPECs (MINOR novo = INFO, MAJOR novo ou hash divergente = ERRO)."""
from __future__ import annotations

import copy
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from agent_bar import contracts_lock as cl


def test_the_real_lock_is_well_formed_and_matches_what_the_code_accepts():
    lock = cl.load_lock()
    assert {e["spec"] for e in lock["consumes"]} >= {"plan2space/P2S-CONTRACTS-v1.md",
                                                    "agent-bar/P2S-STUDIO-MULTI-AGENT-PLATFORM.md"}
    assert cl.verify_code(lock) == []


def test_a_lock_the_code_does_not_accept_fails():
    lock = copy.deepcopy(cl.load_lock())
    contracts = next(e for e in lock["consumes"] if e["spec"] == "plan2space/P2S-CONTRACTS-v1.md")
    contracts["contracts"]["C4"] = "3"
    (f,) = cl.verify_code(lock)
    assert f.level == "ERROR" and "C4" in f.message and "v3" in f.message


def test_a_malformed_lock_is_refused(tmp_path):
    p = tmp_path / "contracts.lock"
    p.write_text('{"schema": "p2s.contracts-lock.v1", "consumes": [{"spec": "x"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="sem"):
        cl.load_lock(p)


TABLE = "# SPEC\n\n| Mudanca | Classe |\n|---|---|\n| v1.0.0 | - |\n| v2.0.0: c4 v2 | MAJOR |\n"


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True, env=env).stdout.decode().strip()


@pytest.fixture
def specs(tmp_path):
    repo = tmp_path / "specs"
    (repo / "plan2space").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    f = repo / "plan2space" / "P2S-CONTRACTS-v1.md"
    f.write_bytes(TABLE.encode())
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "v2")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", commit)
    lock = {"schema": "p2s.contracts-lock.v1", "consumes": [{
        "spec": "plan2space/P2S-CONTRACTS-v1.md", "version": "2.0.0", "ref": "main", "commit": commit,
        "sha256": hashlib.sha256(TABLE.encode()).hexdigest(), "contracts": {"C4": "2"}, "reader": "x"}]}
    return repo, f, lock


def _bump(repo, f, row):
    f.write_bytes((TABLE + row).encode())
    _git(repo, "commit", "-qam", "bump")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD"))


def test_unchanged_spec_is_clean(specs):
    repo, _, lock = specs
    assert cl.check_specs(repo, lock) == []


def test_a_newer_minor_is_information_not_an_error(specs):
    repo, f, lock = specs
    _bump(repo, f, "| v2.1.0: aditivo | MINOR |\n")
    (x,) = cl.check_specs(repo, lock)
    assert x.level == "INFO" and "v2.1.0" in x.message and "aditivo" in x.message


def test_a_new_major_is_an_error(specs):
    repo, f, lock = specs
    _bump(repo, f, "| v3.0.0: quebra | MAJOR |\n")
    (x,) = cl.check_specs(repo, lock)
    assert x.level == "ERROR" and "MAJOR mudou" in x.message


def test_a_hash_that_does_not_match_the_pinned_commit_is_an_error(specs):
    repo, _, lock = specs
    lock["consumes"][0]["sha256"] = "0" * 64
    assert any(x.level == "ERROR" and "sha256" in x.message for x in cl.check_specs(repo, lock))


SPECS_DIR = os.environ.get("AGENT_BAR_SPECS_DIR")


@pytest.mark.skipif(not SPECS_DIR or not Path(SPECS_DIR, ".git").exists(),
                    reason="checkout do engineering-specs (privado) ausente: roda local com AGENT_BAR_SPECS_DIR")
def test_real_specs_checkout_matches_the_lock():
    assert [f for f in cl.check_specs(Path(SPECS_DIR)) if f.level == "ERROR"] == []
