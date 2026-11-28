"""FR-05 (FRIENDLY-AUDIT): o mesmo diagnóstico do GitHub em toda tela.

Antes: a Saúde (collectors.run_gh) procurava o `gh` só no PATH; a aba PRs (GhClient) também tentava
`C:\\Program Files\\GitHub CLI\\gh.exe` e o `AGENT_BAR_GH`. Com o gh instalado fora do PATH, a mesma máquina via
"Não instalado — gh não encontrado" na Saúde e "gh sem login" em PRs. Agora as duas usam a mesma resolução.
"""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_bar import collectors as col
from agent_bar import gh_exe
from agent_bar.cockpit.github import GhClient, GhError

NOT_LOGGED = "You are not logged into any GitHub hosts. To log in, run: gh auth login"


@pytest.fixture
def gh_only_in_program_files(tmp_path, monkeypatch):
    fake = tmp_path / "GitHub CLI" / "gh.exe"
    fake.parent.mkdir()
    fake.write_bytes(b"")                                                    # existe; nunca é executado (run é falso)
    monkeypatch.delenv("AGENT_BAR_GH", raising=False)
    monkeypatch.setattr(gh_exe, "CANDIDATES", ("gh", str(fake)))
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name in ("gh", "gh.exe") else real_which(name, *a, **k))
    return fake


def test_the_resolver_finds_gh_outside_the_path(gh_only_in_program_files):
    assert gh_exe.resolve() == [str(gh_only_in_program_files)]


def test_an_explicit_agent_bar_gh_wins(gh_only_in_program_files, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BAR_GH", str(tmp_path / "outro-gh.exe"))
    assert gh_exe.resolve() == [str(tmp_path / "outro-gh.exe")]


def test_health_and_prs_give_the_same_state_when_gh_is_only_in_program_files(gh_only_in_program_files, monkeypatch):
    seen: list[list[str]] = []

    def fake_run(argv, **kw):
        seen.append(list(argv))
        return SimpleNamespace(returncode=1, stdout="", stderr=NOT_LOGGED)
    monkeypatch.setattr(subprocess, "run", fake_run)
    health = col.collect_github_status(col.run_gh)
    assert seen and seen[0][0] == str(gh_only_in_program_files)              # a Saúde chamou o MESMO executável
    assert health.label == "Sem acesso", health                             # não "Não instalado"

    client = GhClient(runner=lambda argv, timeout: (1, "", NOT_LOGGED))
    assert list(client.exe) == [str(gh_only_in_program_files)]
    with pytest.raises(GhError) as err:
        client._run(["pr", "list"], "o/r")                                   # noqa: SLF001
    assert err.value.kind == "auth"                                          # os dois: sem login


def test_both_say_not_installed_when_there_is_no_gh_anywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_BAR_GH", raising=False)
    monkeypatch.setattr(gh_exe, "CANDIDATES", ("gh", str(tmp_path / "nao-existe" / "gh.exe")))
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name in ("gh", "gh.exe") else real_which(name, *a, **k))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(FileNotFoundError(2, "nao achou")))
    assert col.collect_github_status(col.run_gh).label == "Nao instalado"
    with pytest.raises(GhError) as err:
        GhClient(runner=lambda argv, timeout: (0, "[]", ""))._run(["pr", "list"], "o/r")   # noqa: SLF001
    assert err.value.kind == "not_found"
