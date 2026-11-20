"""Layout do repositorio independente: a raiz e `agent_bar/..`, nunca uma pasta acima do projeto."""
from __future__ import annotations

from pathlib import Path

from agent_bar import collectors as col


def test_default_gov_dir_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BAR_GOV_DIR", str(tmp_path))
    assert col.default_gov_dir() == tmp_path


def test_default_gov_dir_is_the_main_checkout_even_from_a_worktree(monkeypatch):
    """S00-07 D2: o padrao e `runs/governance` do checkout PRINCIPAL (git common dir), rodando do checkout ou de uma worktree."""
    import subprocess
    monkeypatch.delenv("AGENT_BAR_GOV_DIR", raising=False)
    repo_root = Path(col.__file__).resolve().parents[1]
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo_root, capture_output=True, text=True)
    if r.returncode != 0:
        import pytest
        pytest.skip("fora de um repositorio git")
    assert col.default_gov_dir() == Path(r.stdout.strip()).resolve().parent / "runs" / "governance"


def test_default_gov_dir_falls_back_to_this_repository_without_git(monkeypatch):
    monkeypatch.delenv("AGENT_BAR_GOV_DIR", raising=False)

    def no_git(*_a, **_k):
        raise OSError("git ausente")

    monkeypatch.setattr(col.subprocess, "run", no_git)
    assert col.default_gov_dir() == Path(col.__file__).resolve().parents[1] / "runs" / "governance"


def test_once_json_writes_utf8_even_when_stdout_is_cp1252(monkeypatch):
    """S00-07 D7: diagnostico redirecionado sai em UTF-8 (antes saia em cp1252 e quebrava acentos e simbolos)."""
    import io
    import sys

    from agent_bar import app

    class Snap:
        def diagnostic(self) -> str:
            return "GPT: Indisponível ✓ ’"

    monkeypatch.setattr(col, "collect_snapshot", lambda **_k: Snap())
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    assert app.main(["--once-json"]) == 0
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").strip() == "GPT: Indisponível ✓ ’"
