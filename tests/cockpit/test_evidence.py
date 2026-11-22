"""F7.1: leitor do evidence — somente leitura, mesmo contrato (verify.py do checkout), fail-closed, ausente explicito."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_bar.cockpit import evidence as E
from tests.cockpit.evidence_fixture import build


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "evidence")


def test_catalog_lists_runs_validated_by_the_checkout_verify(repo):
    cat = E.load_catalog(repo, expected={})
    by = {r.stage: r for r in cat.runs}
    assert by["A"].valid and by["B"].valid
    assert by["A"].status() == "válido (verify.py)"
    assert cat.rooms() == [("planta_x", "quarto")]


def test_run_in_index_missing_on_disk_is_absent_not_pass(repo):
    c = {r.stage: r for r in E.load_catalog(repo, expected={}).runs}["C"]
    assert not c.present and not c.valid and c.status() == "ausente no disco"
    assert c.states()["GEOMETRY_PASS"] == "ausente" and c.states()["HUMAN_APPROVAL"] == "PENDING_HUMAN"


def test_expected_stage_not_published_is_absent(repo):
    cat = E.load_catalog(repo, expected={("planta_x", "quarto"): ("A", "B", "R1")})
    r1 = next(r for r in cat.runs if r.stage == "R1")
    assert not r1.published and r1.status() == "ausente (não publicado)" and r1.label == "R1 · (ausente)"


def test_tampered_file_makes_the_run_invalid_with_the_verify_message(repo):
    png = repo / "planta_x" / "quarto" / "A" / "run-a" / "views" / "top.png"
    png.write_bytes(png.read_bytes() + b"x")
    a = {r.stage: r for r in E.load_catalog(repo, expected={}).runs}["A"]
    assert not a.valid and any("sha256 diverge" in e for e in a.errors)
    assert a.view_path("top") is None                                       # run invalido nao entrega imagem


def test_incomplete_run_is_invalid(repo):
    (repo / "planta_x" / "quarto" / "B" / "run-b" / "verdicts" / "tests.json").unlink()
    b = {r.stage: r for r in E.load_catalog(repo, expected={}).runs}["B"]
    assert not b.valid and any("ausente" in e for e in b.errors)


def test_missing_verify_refuses_everything(tmp_path):
    root = build(tmp_path / "ev", with_verify=False)
    with pytest.raises(E.EvidenceError, match="verify.py ausente"):
        E.load_catalog(root)


def test_broken_verify_is_fail_closed(repo):
    (repo / "tools" / "verify.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    with pytest.raises(E.EvidenceError, match="não carregou|nao carregou"):
        E.load_catalog(repo)


def test_verify_crash_inside_check_run_marks_run_invalid(repo):
    src = (repo / "tools" / "verify.py").read_text(encoding="utf-8")
    (repo / "tools" / "verify.py").write_text(src.replace("def check_run(root, d, engine):\n",
                                                          "def check_run(root, d, engine):\n    raise ValueError('x')\n"), encoding="utf-8")
    runs = [r for r in E.load_catalog(repo, expected={}).runs if r.present]
    assert runs and all(not r.valid and "verify.check_run falhou" in r.errors[0] for r in runs)


def test_repository_read_failures(tmp_path, repo):
    with pytest.raises(E.EvidenceError, match="inexistente"):
        E.load_catalog(tmp_path / "nao-existe")
    (repo / "index.json").write_text("{quebrado", encoding="utf-8")
    with pytest.raises(E.EvidenceError, match="index.json"):
        E.load_catalog(repo)


def test_cameras_are_paired_only_when_compatible(repo):
    cat = E.load_catalog(repo, expected={})
    a, b = (next(r for r in cat.runs if r.stage == s) for s in ("A", "B"))
    pairs, refused = E.compatible_cameras(a, b)
    assert pairs == ["top", "iso_sw"]
    assert ("interno", "definição diferente entre planta_x/quarto/cameras.json e planta_x/quarto/cameras.v2.json") in refused
    assert ("detalhe", "só existe em B") in refused
    assert all(v["name"] != "pdf_x_skp" for v in a.views("cameras"))        # prancha nao vira camera


def test_identical_camera_definitions_across_versions_are_compatible(tmp_path):
    root = build(tmp_path / "ev", v2_changes_interno=False)
    cat = E.load_catalog(root, expected={})
    a, b = (next(r for r in cat.runs if r.stage == s) for s in ("A", "B"))
    assert E.compatible_cameras(a, b)[0] == ["top", "iso_sw", "interno"]


def test_comparison_with_an_invalid_or_absent_run_is_refused(repo):
    cat = E.load_catalog(repo, expected={})
    a, c = (next(r for r in cat.runs if r.stage == s) for s in ("A", "C"))
    pairs, refused = E.compatible_cameras(a, c)
    assert pairs == [] and "ausente" in refused[0][1]


def test_different_resolution_is_refused(repo):
    cams = repo / "planta_x" / "quarto" / "cameras.v2.json"
    cat = E.load_catalog(repo, expected={})
    a, b = (next(r for r in cat.runs if r.stage == s) for s in ("A", "B"))
    b.image_size = (800, 600)                                                # resolucao lida do arquivo de cameras
    pairs, refused = E.compatible_cameras(a, b)
    assert pairs == [] and "resolução diferente" in refused[0][1]
    assert cams.is_file()


def test_human_state_is_pending_without_file_and_only_valid_file_counts(repo):
    b_dir = repo / "planta_x" / "quarto" / "B" / "run-b"
    get_b = lambda: {r.stage: r for r in E.load_catalog(repo, expected={}).runs}["B"]   # noqa: E731
    assert get_b().states()["HUMAN_APPROVAL"] == "PENDING_HUMAN"
    (b_dir / "verdicts" / "human.json").write_text(json.dumps({"kind": "human", "verdict": "APPROVED"}), encoding="utf-8")
    st = get_b().states()["HUMAN_APPROVAL"]
    assert st.startswith("PENDING_HUMAN (human.json inválido")
    (b_dir / "verdicts" / "human.json").write_text(json.dumps({"kind": "human", "verdict": "APPROVED",
                                                               "statement_ref": "https://github.com/Plan2Space/x/issues/1#issuecomment-1"}),
                                                   encoding="utf-8")
    assert get_b().states()["HUMAN_APPROVAL"] == "APPROVED"


def test_four_states_are_separate(repo):
    cat = E.load_catalog(repo, expected={})
    a, b = (next(r for r in cat.runs if r.stage == s) for s in ("A", "B"))
    assert a.states() == {"GEOMETRY_PASS": "PASS", "FUNCTIONAL_PASS": "ausente", "VISUAL_REVIEW": "nenhuma revisão",
                          "HUMAN_APPROVAL": "PENDING_HUMAN"}
    assert b.states()["FUNCTIONAL_PASS"] == "PASS"


def test_reference_without_origin_has_no_origin(repo):
    a = {r.stage: r for r in E.load_catalog(repo, expected={}).runs}["A"]
    refs = {p.name: E.reference_origin(p) for p in a.references()}
    assert refs["briefing.md"].startswith("PDF do Felipe") and "documento interno" in refs["briefing.md"]
    assert refs["sem_origem.md"] is None


def test_writing_inside_the_evidence_checkout_is_refused(repo, tmp_path):
    with pytest.raises(E.EvidenceError, match="somente leitura"):
        E.ensure_outside(repo / "planta_x" / "quarto" / "A" / "run-a" / "requests" / "x.md", repo)
    with pytest.raises(E.EvidenceError):
        E.ensure_outside(repo, repo)
    assert E.ensure_outside(tmp_path / "fora" / "x.md", repo) == (tmp_path / "fora" / "x.md").resolve()


def test_reader_never_modifies_the_checkout(repo):
    def snapshot():
        return {p.relative_to(repo).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size) for p in repo.rglob("*")}   # inclui __pycache__
    before = snapshot()
    cat = E.load_catalog(repo, expected={})
    for r in cat.runs:
        r.states(), r.references(), r.compare_images(), r.llm_reviews()
    assert snapshot() == before


# ----------------------------------------------------------------------------------- integracao com o checkout real

REAL = os.environ.get("AGENT_BAR_EVIDENCE_DIR")


@pytest.mark.skipif(not REAL or not Path(REAL, "tools", "verify.py").is_file(),
                    reason="checkout real do Plan2Space/evidence ausente (repo privado; roda local com AGENT_BAR_EVIDENCE_DIR)")
def test_real_suite01_runs_and_tampered_copy(tmp_path):
    cat = E.load_catalog(Path(REAL))
    suite = cat.runs_of("planta_74", "suite_01")
    assert {"D1", "D2", "PoC02", "PoC01"} <= {r.stage for r in suite if r.valid}
    d1, poc01 = (next(r for r in suite if r.stage == s and r.valid) for s in ("D1", "PoC01"))
    pairs, refused = E.compatible_cameras(d1, poc01)
    assert set(pairs) == {"top", "iso_sw", "iso_ne", "interno_porta_o010", "interno_porta_o002", "interno_janela_o005"}
    assert {n for n, _ in refused} == {"cama_3q", "cama_cabeceira", "cama_canto_pe", "quarto_janela"}
    # copia adulterada: o verify.py real aponta o sha256
    copy = tmp_path / "ev"
    shutil.copytree(REAL, copy, ignore=shutil.ignore_patterns(".git"))
    victim = copy / d1.path.relative_to(Path(REAL)) / "views" / "top.png"
    victim.write_bytes(victim.read_bytes() + b"\0")
    bad = next(r for r in E.load_catalog(copy).runs if r.stage == "D1" and r.present)
    assert not bad.valid and any("sha256 diverge" in e for e in bad.errors)
