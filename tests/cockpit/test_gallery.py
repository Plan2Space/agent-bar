"""S01C-F1-03 (= VS-F1-04..06): leitor de render_manifest + galeria multi-fonte. Testes que reprovam o errado."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_bar.cockpit import evidence as E
from agent_bar.cockpit import gallery as G
from tests.cockpit.gallery_fixture import (
    BAD_SHA,
    OK_BUILD,
    OK_RENDER,
    PLANT,
    R4,
    R4B,
    ROOM,
    UNPUB_FAIL,
    build_all,
    build_engine,
    build_library,
)


def gallery(tmp_path: Path, *, with_evidence: bool = True) -> G.Gallery:
    ev, engine, dl = build_all(tmp_path)
    cat = E.load_catalog(ev, expected={}) if with_evidence else None
    return G.build_gallery(cat, engine, dl, rooms=[(PLANT, ROOM)])


def test_render4_appears_with_fail_and_is_never_eligible(tmp_path):
    g = gallery(tmp_path)
    items = [i for i in g.of(PLANT, ROOM) if i.item_id == R4]
    assert len(items) == 1                                                       # VS-R4: uma vez
    r4 = items[0]
    assert (r4.source, r4.kind, r4.render_verdict) == (G.PUBLISHED, "render", "FAIL")
    assert "render FAIL" in r4.eligibility()
    assert "render FAIL" in r4.eligibility(evaluations=["avaliação válida"])   # nem com avaliação
    assert not r4.eligible(evaluations=["avaliação válida"])


def test_engine_run_already_published_is_shown_once_as_published(tmp_path):
    ids = [i.item_id for i in gallery(tmp_path).of(PLANT, ROOM)]
    assert R4B not in ids and ids.count(R4) == 1                                 # o render4b entrou no run publicado


def test_unpublished_fail_render_is_shown_labelled_and_never_eligible(tmp_path):
    it = gallery(tmp_path).get(UNPUB_FAIL)
    assert it.source == G.ENGINE and it.render_verdict == "FAIL"
    assert G.UNPUBLISHED_NOTE in it.notes and it.human_approval == "PENDING_HUMAN"   # VS-R1a, VS-R5
    assert not it.eligible(evaluations=["x"])


def test_without_publication_render4_still_appears_as_engine_fail(tmp_path):
    g = gallery(tmp_path, with_evidence=False)
    r4 = g.get(R4)
    assert (r4.source, r4.render_verdict) == (G.ENGINE, "FAIL") and not r4.eligible(evaluations=["x"])
    assert "evidence indisponível" in " ".join(g.errors)


def test_pass_render_on_a_full_pass_build_is_eligible_only_with_an_evaluation(tmp_path):
    it = gallery(tmp_path).get(OK_RENDER)
    assert it.build == {"geometria": "PASS", "funcional": "PASS", "construção": "PASS"}
    assert it.eligibility() == ["sem avaliação"]
    assert it.eligible(evaluations=["avaliação válida"])


def test_build_without_construction_verdict_blocks_eligibility(tmp_path):
    r4 = gallery(tmp_path, with_evidence=False).get(R4)                          # build antigo, sem construção
    assert "build: construção ausente" in r4.eligibility()


def test_build_runs_are_never_render_candidates(tmp_path):
    it = gallery(tmp_path).get(OK_BUILD)
    assert it.kind == "build" and "não é run de render" in it.eligibility(evaluations=["x"])


def test_image_whose_sha256_differs_from_the_manifest_is_rejected(tmp_path):
    it = gallery(tmp_path).get(BAD_SHA)
    assert [i.key for i in it.images] == ["dia_cama_3q"]                         # VS-R2: só a conferida entra
    assert it.rejected == ["noite_cama_3q: sha256 diverge do manifest"]
    img = it.images[0]
    assert img.ident == f"Plan2Space/sketchup-mcp-next:artifacts/{PLANT}_{ROOM}/{BAD_SHA}/renders/dia_cama_3q.png@{img.sha256}"
    assert img.sha256 == G.file_sha256(img.path)


def test_items_are_newest_first_by_engine_started_at(tmp_path):
    items = [i for i in gallery(tmp_path).of(PLANT, ROOM, kinds=["render"])]
    assert [i.item_id for i in items] == [UNPUB_FAIL, BAD_SHA, OK_RENDER, R4]
    assert items[0].when() == "23/11 06:16 UTC"


def test_published_run_uses_the_engine_time_and_only_the_day_without_it(tmp_path):
    g = gallery(tmp_path)
    assert g.get(R4).when() == "22/11 22:01 UTC"                                  # hora do render_manifest do motor
    day_only = G.GalleryItem(G.PUBLISHED, PLANT, ROOM, "20261121-x-r0", "build", day="2026-11-21")
    assert day_only.when() == "21/11 (dia, sem hora)"                            # VS-R3: nunca uma hora inventada


def test_unreadable_engine_manifest_is_an_error_not_an_item(tmp_path):
    g = gallery(tmp_path)
    assert any("render_manifest ilegível" in e for e in g.errors)
    assert g.get("20261123-planta_x_quarto-broken") is None


def test_reference_registered_for_the_room_enters_with_checked_sha(tmp_path):
    refs = gallery(tmp_path).of(PLANT, ROOM, sources=[G.REFERENCE])
    by_id = {r.item_id: r for r in refs}
    assert set(by_id) == {"ref-quarto-r2@v1", "ref-quarto-r1@v1"}               # a da sala fica de fora
    assert len(by_id["ref-quarto-r2@v1"].images) == 1 and by_id["ref-quarto-r2@v1"].curation == "CANDIDATE"
    assert by_id["ref-quarto-r1@v1"].images == []
    assert by_id["ref-quarto-r1@v1"].rejected == ["ref-quarto-r1@v1: sha256 diverge do manifest"]
    assert "nunca geometria" in " ".join(by_id["ref-quarto-r2@v1"].notes)


def test_sources_filter(tmp_path):
    g = gallery(tmp_path)
    assert {i.source for i in g.of(PLANT, ROOM, sources=[G.ENGINE])} == {G.ENGINE}
    assert {i.source for i in g.of(PLANT, ROOM)} == {G.PUBLISHED, G.ENGINE, G.REFERENCE}


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {p.as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


def test_reader_never_writes_to_any_source(tmp_path):
    ev, engine, dl = build_all(tmp_path)
    before = {r: _snapshot(r) for r in (ev, engine, dl, tmp_path / "assets")}
    G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])
    assert {r: _snapshot(r) for r in before} == before


def test_missing_engine_and_library_are_errors_not_crashes(tmp_path):
    g = G.build_gallery(None, tmp_path / "nao-existe", tmp_path / "nem-isto", rooms=[(PLANT, ROOM)])
    assert g.items == [] and len(g.errors) == 3


def test_engine_and_library_builders_are_independent(tmp_path):
    engine = build_engine(tmp_path / "e")
    items, errors = G.load_engine(engine, PLANT, ROOM)
    assert {i.item_id for i in items} >= {R4, R4B, OK_RENDER} and errors
    refs, _ = G.load_references(build_library(tmp_path / "dl", tmp_path / "a"), PLANT, ROOM)
    assert len(refs) == 2


REAL = os.environ.get("AGENT_BAR_REAL_GALLERY")


@pytest.mark.skipif(not REAL, reason="opt-in: AGENT_BAR_REAL_GALLERY=<evidence>;<motor>;<design-library>")
def test_real_sources_show_render4_fail_and_never_eligible():
    ev, engine, dl = (Path(p) for p in REAL.split(";"))
    g = G.build_gallery(E.load_catalog(ev), engine, dl, rooms=[("planta_74", "suite_01")])
    items = g.of("planta_74", "suite_01")
    r4 = [i for i in items if i.item_id == "20261122-planta_74_suite_01-render4"]
    assert len(r4) == 1 and r4[0].render_verdict == "FAIL" and not r4[0].eligible(evaluations=["x"])
    assert not any(i.item_id == "20261122-planta_74_suite_01-render4b" for i in items)
    assert all(not i.eligible() for i in items)                                  # sem avaliação, nada é "melhor"
