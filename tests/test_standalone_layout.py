"""Layout do repositorio independente: a raiz e `agent_bar/..`, nunca uma pasta acima do projeto."""
from __future__ import annotations

from pathlib import Path

from agent_bar import collectors as col


def test_default_gov_dir_stays_inside_this_repository_when_not_configured(monkeypatch):
    monkeypatch.delenv("AGENT_BAR_GOV_DIR", raising=False)
    repo_root = Path(col.__file__).resolve().parents[1]
    assert col.default_gov_dir() == repo_root / "runs" / "governance"


def test_default_gov_dir_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BAR_GOV_DIR", str(tmp_path))
    assert col.default_gov_dir() == tmp_path
