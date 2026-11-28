"""Contract tests dos coletores locais da Central (§7.1/§7.3): catálogo de SPECs (engineering-specs) e evidências
publicadas (evidence). Lidos por `git` só de leitura no ref padrão; saídas gravadas do git real; sem rede, sem repo."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_bar.cockpit import project_sources as src

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "project_status"
NOW = 1_764_230_400.0


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeGit:
    """Responde `git -C <repo> <args>` com as saídas gravadas. Registra cada chamada para checar que é só leitura."""

    def __init__(self, lab: bool = True, broken: bool = False):
        self.lab, self.broken, self.calls = lab, broken, []

    def __call__(self, repo: Path, *args: str) -> str | None:
        self.calls.append(args)
        if self.broken:
            return None
        name = Path(repo).name
        if args[:1] == ("rev-parse",):
            return _read(f"{'specs' if name == 'engineering-specs' else 'evidence'}_rev.txt").strip()
        if args[:3] == ("ls-tree", "-r", "--name-only"):
            return _read("specs_ls_tree.txt")
        if args[:1] == ("show",):
            path = args[1].split(":", 1)[1]
            if name == "evidence":
                if path == "index.json":
                    return _read("evidence_index.json")
                if path == "lab/index.json":
                    return _read("evidence_lab_index.json") if self.lab else None
            shown = FIX / "specs_show" / path.replace("/", "_")
            return shown.read_text(encoding="utf-8") if shown.exists() else "# Sem status\n\ntexto\n"
        return None


# Fixtures SINTÉTICOS no formato do git real: engineering-specs e evidence são privados e o agent-bar é público.
SPECS = Path("E:/x/engineering-specs")
EVID = Path("E:/x/evidence")


def test_spec_catalog_lists_spec_files_and_skips_non_specs():
    cat = src.collect_specs(SPECS, now=NOW, git=FakeGit())
    paths = {s.path for s in cat.specs}
    assert paths == {"produto-a/FP-900_exemplo_com_status_negrito.md", "produto-a/EXEMPLO-PAINEL-001.md",
                     "plataforma/SPEC-EXEMPLO-002_modelo_de_sessoes.md",
                     "plataforma/S99-01_registros_sem_linha_de_status.md", "plataforma/fila_de_microfases.md"}
    assert not any(p.endswith("README.md") or p.startswith(("templates/", "scripts/", ".github/")) for p in paths)
    assert cat.sha == _read("specs_rev.txt").strip() and cat.ref == "origin/develop"


def test_spec_declared_status_is_verbatim_and_only_from_a_status_line():
    cat = src.collect_specs(SPECS, now=NOW, git=FakeGit())
    by = {s.path: s for s in cat.specs}
    som = by["plataforma/SPEC-EXEMPLO-002_modelo_de_sessoes.md"]
    assert som.title.startswith("SPEC-EXEMPLO-002")
    assert som.declared_status.startswith("vigente desde 2026-01-01")
    # "status, tentativas…" numa lista de campos não é linha de status → desconhecido, nada inventado
    assert by["plataforma/S99-01_registros_sem_linha_de_status.md"].declared_status == src.UNKNOWN
    assert by["produto-a/FP-900_exemplo_com_status_negrito.md"].declared_status.startswith("implementação incremental")
    assert by["produto-a/EXEMPLO-PAINEL-001.md"].declared_status.startswith("PROPOSTA (aguarda aprovação)")
    assert by["plataforma/fila_de_microfases.md"].declared_status == src.UNKNOWN


def test_spec_catalog_without_the_checkout_is_unknown():
    cat = src.collect_specs(SPECS, now=NOW, git=FakeGit(broken=True))
    assert cat.state == src.UNKNOWN and cat.specs == [] and cat.error


def test_git_calls_are_read_only():
    g = FakeGit()
    src.collect_specs(SPECS, now=NOW, git=g)
    src.collect_evidence(EVID, now=NOW, git=g)
    assert {c[0] for c in g.calls} <= {"rev-parse", "ls-tree", "show"}


def test_published_evidence_counts_runs_from_the_index():
    ev = src.collect_evidence(EVID, now=NOW, git=FakeGit())
    assert ev.state == "ok" and ev.published_runs == 7
    assert ev.lab_runs == 6                     # lab/index.json: soma dos runs por modelo


def test_lab_index_not_on_the_default_branch_is_unknown_not_zero():
    ev = src.collect_evidence(EVID, now=NOW, git=FakeGit(lab=False))
    assert ev.published_runs == 7 and ev.lab_runs is None
    assert "lab/" in ev.note


def test_evidence_without_the_checkout_is_unknown():
    ev = src.collect_evidence(EVID, now=NOW, git=FakeGit(broken=True))
    assert ev.state == src.UNKNOWN and ev.published_runs is None and ev.lab_runs is None


@pytest.mark.parametrize("line,expected", [
    ("**Status:** PROPOSTA (aguarda o APROVADO)", "PROPOSTA (aguarda o APROVADO)"),
    ("status: EM_EXECUCAO        # P0 entregue", "EM_EXECUCAO        # P0 entregue"),
    ("- **Status:** vigente", "vigente"),
    ("| Status | Critério |", None),
    ("O status, tentativas e duração", None),
])
def test_status_line_parser(line, expected):
    assert src.status_line(line) == expected


@pytest.mark.parametrize("path,linked,expected", [
    ("produto-a/FP-900_exemplo.md", "FP-900", True),
    ("produto-a/FP-901_x.md", "FP-901/FP-900", True),
    ("produto-a/FP-90_x.md", "FP-900", False),
    ("plataforma/SPEC-EXEMPLO-002.md", "SPEC-EXEMPLO-002 (es#1, 0123abcd)", True),
    ("plataforma/SPEC-EXEMPLO.md", "SPEC-EXEMPLO-002 (es#1)", False),
    ("plataforma/fila_de_microfases.md", "fila_de_microfases", True),
    ("plataforma/fila_de_microfases.md", "fila", False),
])
def test_spec_matches_the_project_link_by_its_id(path, linked, expected):
    assert src.spec_matches(path, linked) is expected


def test_overview_carries_specs_and_evidence_and_their_health():
    from agent_bar.cockpit import project_status as ps
    from tests.cockpit.test_project_status import client
    ov = ps.collect_overview(client(), "Plan2Space", now=NOW,
                             specs=lambda: src.collect_specs(SPECS, now=NOW, git=FakeGit(lab=False)),
                             evidence=lambda: src.collect_evidence(EVID, now=NOW, git=FakeGit(lab=False)))
    health = dict(ov.health)
    assert ov.specs.state == "ok" and health["engineering-specs"].startswith("ok")
    assert ov.evidence.published_runs == 7 and health["evidence"].startswith("ok")
    ov2 = ps.collect_overview(client(), "Plan2Space", now=NOW,
                              specs=lambda: src.collect_specs(SPECS, now=NOW, git=FakeGit(broken=True)),
                              evidence=lambda: src.collect_evidence(EVID, now=NOW, git=FakeGit(broken=True)))
    assert dict(ov2.health)["evidence"].startswith("indisponível")
    assert dict(ov2.health)["engineering-specs"].startswith("indisponível")
