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


def test_unpublished_pass_render_is_verified_and_comparable_but_never_eligible(tmp_path):
    it = gallery(tmp_path).get(OK_RENDER)
    assert it.build == {"geometria": "PASS", "funcional": "PASS", "construção": "PASS"}
    assert it.verified                                                          # VERIFICADO: provas conferidas
    assert it.eligibility(evaluations=["avaliação válida"]) == [G.UNPUBLISHED_BLOCK]   # VS-R1a: nunca o melhor
    st = it.states(evaluations=["avaliação válida"])
    assert st["VISÍVEL"] == "sim" and st["VERIFICADO"] == "sim" and st["ELEGÍVEL"].startswith("não: não publicado")


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


# ------------------------------------------------------------------ revisão do #88: um teste negativo por guard
from tests.cockpit.gallery_fixture import (  # noqa: E402
    ALL_REJECTED,
    NEGATIVES,
    NO_BUILD_SKP,
    NO_REPORT,
    NO_SKP_SHA,
    PUB_OK,
    PUB_TAMPERED,
    REPORT_FAIL,
    SKP_MISMATCH,
    STATUS_BLOCKED,
    build_negatives,
)

EVAL = ["avaliação válida"]                                                    # com avaliação: só a prova bloqueia


@pytest.fixture
def neg(tmp_path) -> G.Gallery:
    ev, engine, dl = build_negatives(tmp_path)
    return G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])


def _blocked(g: G.Gallery, run_id: str, expected: str) -> G.GalleryItem:
    it = g.get(run_id)
    assert it is not None, f"{run_id} sumiu da galeria (inválido continua visível)"
    blockers = it.eligibility(EVAL)
    assert not it.eligible(EVAL) and any(expected in b for b in blockers), (expected, blockers)
    assert it.states(EVAL)["VISÍVEL"] == "sim"
    return it


def test_guard_errors_block_eligibility_report_fail_under_manifest_pass(neg):
    it = _blocked(neg, REPORT_FAIL, "inválido: render_report diz FAIL, render_manifest diz PASS")
    assert not it.verified and it.render_verdict == "PASS"                      # o manifest sozinho não basta


def test_guard_status_not_ok_blocks(neg):
    it = _blocked(neg, STATUS_BLOCKED, "inválido: status do render BLOCKED")
    assert not it.verified


def test_guard_render_whose_skp_differs_from_the_build_does_not_inherit_its_pass(neg):
    it = _blocked(neg, SKP_MISMATCH, "skp não conferido (sha256 do skp do render difere do build)")
    assert "PASS" not in it.build.values() and not it.verified


def test_guard_render_with_every_frame_rejected_is_blocked(neg):
    it = _blocked(neg, ALL_REJECTED, "nenhuma imagem com sha256 conferido")
    assert it.images == [] and len(it.rejected) == 2


def test_guard_published_run_rejected_by_verify_shows_no_image_and_is_blocked(neg):
    it = _blocked(neg, PUB_TAMPERED, "inválido:")
    assert it.source == G.PUBLISHED and it.images == [] and it.rejected and not it.verified


def test_fail_closed_missing_render_report(neg):
    it = _blocked(neg, NO_REPORT, "inválido: render_report.json ausente")
    assert not it.verified


def test_fail_closed_render_without_skp_sha(neg):
    _blocked(neg, NO_SKP_SHA, "skp não conferido (render sem input_skp.sha256)")


def test_fail_closed_build_without_skp_artifact(neg):
    _blocked(neg, NO_BUILD_SKP, "skp não conferido (build sem artefato skp com sha256)")


def test_invalid_runs_stay_visible_with_their_reasons(neg):
    ids = {i.item_id for i in neg.of(PLANT, ROOM)}
    assert set(NEGATIVES) | {PUB_TAMPERED} <= ids


def test_only_a_published_verified_pass_render_with_evaluation_is_eligible(neg):
    it = neg.get(PUB_OK)
    assert it.source == G.PUBLISHED and it.verified
    assert it.eligibility() == ["sem avaliação"]                                # sem avaliação, nada é elegível
    assert it.eligible(EVAL)
    st = it.states(EVAL)
    assert st["VERIFICADO"] == "sim" and st["ELEGÍVEL"] == "sim" and st["MELHOR"].startswith("não escolhido")
    assert [i.item_id for i in neg.of(PLANT, ROOM) if i.eligible(EVAL)] == [PUB_OK]


def test_reference_shows_synthetic_provenance_and_usage(tmp_path):
    ref = next(r for r in gallery(tmp_path).of(PLANT, ROOM, sources=[G.REFERENCE]) if r.images)
    assert ref.synthetic is True and ref.usage == "conceptual-only"
    assert "sintética (GPT_GENERATED_ON_REQUEST); uso conceptual-only" in ref.notes


def test_reference_without_synthetic_flag_says_it_is_undeclared():
    ref = G.GalleryItem(G.REFERENCE, PLANT, ROOM, "r@v1", "reference")
    assert ref.provenance_text().startswith("proveniência sintética não declarada")
# --------------------------------------------------------------------------------- S01C-F1-04 (lógica dos cartões)
def test_latest_render_is_the_greatest_started_at_whatever_the_verdict(tmp_path):
    items = gallery(tmp_path).of(PLANT, ROOM)
    assert G.latest_render(items).item_id == UNPUB_FAIL
    older_first = list(reversed(items))                                          # a ordem de entrada não importa
    assert G.latest_render(older_first).item_id == UNPUB_FAIL


def test_latest_render_ignores_builds_and_references(tmp_path):
    a = G.GalleryItem(G.ENGINE, PLANT, ROOM, "b", "build", started_at="2026-11-24T00:00:00Z", day="2026-11-24")
    r = G.GalleryItem(G.REFERENCE, PLANT, ROOM, "r", "reference", started_at="2026-11-25T00:00:00Z", day="2026-11-25")
    x = G.GalleryItem(G.ENGINE, PLANT, ROOM, "x", "render", started_at="2026-11-20T00:00:00Z", day="2026-11-20")
    assert G.latest_render([a, r, x]) is x and G.latest_render([a, r]) is None


def test_without_evaluation_the_best_card_says_so(tmp_path):
    cards = G.cards(gallery(tmp_path).of(PLANT, ROOM))
    assert [c.role for c in cards] == ["referência", "último render", "melhor conhecido"]
    assert cards[2].item is None and cards[2].text == "sem avaliação"


def test_evaluation_only_on_a_fail_render_never_makes_it_best(tmp_path):
    items = gallery(tmp_path).of(PLANT, ROOM)
    cards = G.cards(items, lambda i: ["avaliação"] if i.item_id == R4 else [])
    assert cards[2].item is None and cards[2].text.startswith("nenhum render elegível")


def test_best_card_never_names_a_winner_before_the_rule_exists(neg):
    items = neg.of(PLANT, ROOM)
    cards = G.cards(items, lambda i: ["avaliação"] if i.item_id == PUB_OK else [])   # um elegível de verdade
    assert cards[2].item is None and "S01C-F1-07" in cards[2].text


def test_an_evaluated_unpublished_render_never_becomes_best(tmp_path):
    items = gallery(tmp_path).of(PLANT, ROOM)
    cards = G.cards(items, lambda i: ["avaliação"] if i.item_id == OK_RENDER else [])
    assert cards[2].item is None and cards[2].text.startswith("nenhum render elegível")


def test_visual_and_trend_texts():
    item = G.GalleryItem(G.ENGINE, PLANT, ROOM, "x", "render")
    assert G.visual_text([]) == "sem avaliação" and G.trend_text(item, []) == "inconclusivo (sem avaliação)"
    assert G.visual_text(["a", "b"]) == "2 avaliação(ões)"


def test_card_image_prefers_the_render_frame(tmp_path):
    img = lambda k: G.GalleryImage(k, Path(k), "0" * 64, "r", k)                 # noqa: E731
    item = G.GalleryItem(G.PUBLISHED, PLANT, ROOM, "x", "render", images=[img("cama_3q"), img("render_dia_cama_3q")])
    assert G.primary_image(item).key == "render_dia_cama_3q"


def test_a_single_rejected_frame_makes_the_run_unverified(tmp_path):
    it = gallery(tmp_path).get(BAD_SHA)                                           # 1 quadro conferido, 1 divergente
    assert len(it.images) == 1 and len(it.rejected) == 1
    assert not it.verified
    assert any("1 quadro(s) recusado(s)" in g for g in it.verification_gaps())


# ------------------------------------------------------------- câmera da miniatura e da comparação (ordem fixa)
def _img(k: str) -> G.GalleryImage:
    return G.GalleryImage(k, Path(f"{k}.png"), "0" * 64, "r", f"views/{k}.png")


def _run(run_id: str, *keys: str, source: str = G.PUBLISHED) -> G.GalleryItem:
    return G.GalleryItem(source, PLANT, ROOM, run_id, "render", images=[_img(k) for k in keys])


def test_thumbnail_shows_the_perspective_not_the_bench_close_up():
    item = _run("x", "render_entardecer_close_banco", "render_entardecer_perspectiva")
    assert G.primary_image(item).key == "render_entardecer_perspectiva"


def test_thumbnail_order_is_fixed_and_does_not_depend_on_the_manifest_order():
    import itertools
    keys = ("render_entardecer_close_banco", "render_entardecer_frente", "render_entardecer_perspectiva",
            "render_entardecer_r2_canto", "render_entardecer_close_cama", "perspectiva", "r2_canto")
    picks = {G.primary_image(_run("x", *p)).key for p in itertools.permutations(keys)}
    assert picks == {"render_entardecer_r2_canto"}                                # r2_canto primeiro, sempre
    assert G.CAMERA_PREFERENCE == ("r2_canto", "perspectiva", "frente", "cama_3q")
    rest = [G.primary_image(_run("x", *p)).key for p in itertools.permutations(
        ("render_dia_close_painel", "render_dia_close_banco", "render_noite_close_banco"))]
    assert set(rest) == {"render_dia_close_banco"}                                # o resto: alfabético, luz fixa


@pytest.mark.parametrize("keys,want", [
    (("render_entardecer_close_banco", "render_entardecer_frente", "render_entardecer_perspectiva"),
     "render_entardecer_perspectiva"),
    (("render_entardecer_close_banco", "render_entardecer_frente"), "render_entardecer_frente"),
    (("render_noite_cama_3q", "render_dia_cama_3q", "render_dia_quarto_janela"), "render_dia_cama_3q"),
    (("perspectiva", "render_entardecer_close_banco"), "render_entardecer_close_banco"),   # render antes de vista crua
    (("dia_cama_3q", "noite_cama_3q"), "dia_cama_3q"),                               # quadro do motor sem render_
    (("r2",), "r2")])
def test_thumbnail_follows_the_declared_order_and_never_invents_a_camera(keys, want):
    item = _run("x", *keys)
    img = G.primary_image(item)
    assert img.key == want and img in item.images
    assert G.primary_image(_run("vazio")) is None


def test_camera_text_names_camera_and_light():
    assert G.camera_text(_img("render_entardecer_r2_canto")) == "câmera r2_canto · luz entardecer"
    assert G.camera_text(_img("dia_cama_3q")) == "câmera cama_3q · luz dia"
    assert G.camera_text(_img("perspectiva")) == "vista perspectiva (sem render)"
    assert G.camera_text(None) == "câmera —"


def test_comparison_uses_the_same_camera_on_both_sides_when_both_runs_have_it():
    rc5 = _run("rc5", "render_entardecer_close_banco", "render_entardecer_perspectiva", "render_entardecer_r2_canto")
    rc4 = _run("rc4", "render_entardecer_perspectiva", "render_entardecer_close_banco")
    a, b, note = G.comparison_pair(rc5, rc4)
    assert (a.key, b.key) == ("render_entardecer_perspectiva",) * 2
    assert "rc4 não tem a câmera r2_canto" in note and "perspectiva" in note
    a, b, note = G.comparison_pair(rc4, _run("rc3", "render_entardecer_perspectiva"))
    assert a.key == b.key == "render_entardecer_perspectiva" and note == ""


def test_comparison_keeps_the_camera_across_different_lights():
    a, b, _ = G.comparison_pair(_run("rc1", "render_entardecer_perspectiva", "render_entardecer_close_banco"),
                                _run("vb", "render_dia_close_banco", "render_noite_perspectiva", "render_dia_perspectiva"))
    assert (a.key, b.key) == ("render_entardecer_perspectiva", "render_dia_perspectiva")


def test_comparison_without_a_common_camera_says_so():
    a, b, note = G.comparison_pair(_run("n", "render_entardecer_perspectiva", "cama_3q"),
                                 _run("o", "render_dia_cama_3q", "perspectiva", "cama_3q"))   # só vistas cruas em comum
    assert (a.key, b.key) == ("render_entardecer_perspectiva", "render_dia_cama_3q")   # render nunca com vista crua
    assert note.startswith("sem câmera em comum") and "o não tem perspectiva" in note


def test_comparison_with_the_reference_never_claims_a_camera_pair():
    ref = _run("ref@v1", "r2", source=G.REFERENCE)
    a, b, note = G.comparison_pair(_run("rc5", "render_entardecer_close_banco", "render_entardecer_r2_canto"), ref)
    assert (a.key, b.key, note) == ("render_entardecer_r2_canto", "r2", "")


# ------------------------------------------------ evidence ilegível: publicação não conferida, nunca "não publicado"
def test_without_a_readable_evidence_an_engine_run_never_claims_it_was_not_published(tmp_path):
    from agent_bar.cockpit.ui.design_view import source_text
    _ev, engine, dl = build_all(tmp_path)
    g = G.build_gallery(None, engine, dl, rooms=[(PLANT, ROOM)])                   # o evidence não pôde ser lido
    eng = [i for i in g.of(PLANT, ROOM) if i.source == G.ENGINE]
    assert eng
    for it in eng:
        assert not it.publication_checked
        assert G.UNPUBLISHED_NOTE not in it.notes and G.UNCHECKED_NOTE in it.notes
        why = it.eligibility(["avaliação"])
        assert G.UNPUBLISHED_BLOCK not in why and not it.eligible(["avaliação"])    # continua fora do "melhor"
        if it.kind == "render":
            assert G.UNCHECKED_BLOCK in why
        assert source_text(it) == f"motor: {G.UNCHECKED_NOTE}"


def test_with_a_readable_evidence_an_unpublished_engine_run_still_says_not_published(tmp_path):
    from agent_bar.cockpit.ui.design_view import source_text
    g = gallery(tmp_path)
    eng = [i for i in g.of(PLANT, ROOM) if i.source == G.ENGINE]
    assert eng and all(i.publication_checked for i in eng)
    assert all(source_text(i) == f"motor: {G.UNPUBLISHED_NOTE}" and G.UNPUBLISHED_NOTE in i.notes for i in eng)
