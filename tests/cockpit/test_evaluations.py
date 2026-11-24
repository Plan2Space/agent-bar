"""S01C-F1-05: registro de avaliação por rubrica v1. Testes que reprovam o errado."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bar.cockpit import evaluations as V
from agent_bar.cockpit import evidence as E
from agent_bar.cockpit import gallery as G
from tests.cockpit.gallery_fixture import OK_BUILD, OK_RENDER, PLANT, R4, ROOM, build_all

CLAUDE = V.Evaluator("agent", "claude-acp", "claude-opus-5-5", "claude-agent-acp@0.109.1", "0.109.1", "SUPPORTED",
                     "VISUAL-STUDIO §8.1 (prova por ferramenta, 2026-11-23)")
QWEN = V.Evaluator("agent", "ollama", "qwen2.5vl:7b", "5ced39df" + "0" * 56, "ollama 0.40.16", "SUPPORTED",
                   "/api/show capabilities=vision + prova reference-r2 (2026-11-23)")
LLAMA = V.Evaluator("agent", "ollama", "llama3.1:8b", "a" * 64, "ollama 0.40.16", "UNSUPPORTED", "")
SCORES = {
    "fidelidade_referencia": (3, "ripado de nogueira presente atrás da cama, mas sem as arandelas da referência"),
    "cama_cabeceira_enxoval": (2, "cabeceira lisa; a manta não tem o caimento lateral da referência"),
    "materiais_texturas": (3, "piso de madeira clara visível; tapete sem relevo"),
    "iluminacao": (4, "luz quente de entardecer no lado esquerdo; sanca acesa"),
    "mobiliario_detalhes": (3, "criados presentes dos dois lados; sem objetos de cena"),
}


@pytest.fixture()
def src(tmp_path):
    ev, engine, dl = build_all(tmp_path)
    g = G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])
    ref = g.of(PLANT, ROOM, sources=[G.REFERENCE])
    ref = next(r for r in ref if r.images)
    return g, ref, (ev, engine, dl)


def delivered(ref, shas, *, confirmed=True):
    """Bloco `delivery` como o caminho do #94 grava: referência + renders entregues, confirmado pelo provedor."""
    return {"batch_id": "b" * 32, "session_id": "s1", "status": ["SENT"], "confirmed": confirmed,
            "confirmation": "o provedor contou … (+512); cada imagem sozinha custa 256 (reference) + 256 (render)",
            "images": [{"role": "reference", "sha256": ref.images[0].sha256}]
            + [{"role": "render", "sha256": s} for s in shas], "model_reported": ""}


def evaluate(g, ref, evaluator=CLAUDE, run=OK_RENDER, scores=None, shas=None, delivery=None, **kw):
    item = g.get(run)
    shas = shas if shas is not None else [item.images[0].sha256]
    return item, V.new_evaluation(item, evaluator, reference=ref, camera="dia_cama_3q",
                                  scores=scores if scores is not None else SCORES, image_shas=shas,
                                  delivery=delivery if delivery is not None else delivered(ref, shas),
                                  clock=lambda: 1795400000.0, **kw)


def test_valid_evaluation_records_every_s01c_r10_field(src):
    g, ref, _ = src
    item, ev = evaluate(g, ref)
    d = ev.to_dict()
    assert (d["run_id"], d["camera"], d["rubric_version"]) == (OK_RENDER, "dia_cama_3q", "rubric-v1")
    assert d["evaluator"]["model"] == "claude-opus-5-5" and d["evaluator"]["digest"]
    assert d["reference"] == {"id": ref.item_id, "sha256": ref.images[0].sha256}
    assert {k: v["score"] for k, v in d["scores"].items()} == {k: s for k, (s, _) in SCORES.items()}
    assert all(v["justification"] for v in d["scores"].values())
    assert d["images"] == [{"ident": item.images[0].ident, "sha256": item.images[0].sha256}]
    assert d["created_at"] == "2026-11-23T02:13:20Z" and d["confidence"] == "normal"
    assert d["label"].startswith("opinião do avaliador")


def test_missing_justification_is_refused(src):
    g, ref, _ = src
    bad = {**SCORES, "iluminacao": (4, "   ")}
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, scores=bad)
    assert "iluminacao: sem justificativa" in e.value.reasons


def test_missing_model_digest_is_refused(src):
    g, ref, _ = src
    no_digest = V.Evaluator(**{**CLAUDE.__dict__, "digest": ""})
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, evaluator=no_digest)
    assert "sem digest do modelo" in e.value.reasons


def test_model_without_vision_is_refused(src):
    g, ref, _ = src
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, evaluator=LLAMA)
    assert any(r.startswith("modelo sem visão comprovada") for r in e.value.reasons)


def test_vision_claim_without_proof_is_refused(src):
    g, ref, _ = src
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, evaluator=V.Evaluator(**{**CLAUDE.__dict__, "vision_proof": ""}))
    assert "sem a prova de visão do avaliador (VS-R10)" in e.value.reasons


@pytest.mark.parametrize("score", [0, 6, 3.5, "4", True, 85])
def test_scores_are_integers_from_1_to_5(src, score):
    g, ref, _ = src
    with pytest.raises(V.EvaluationRejected):
        evaluate(g, ref, scores={**SCORES, "materiais_texturas": (score, "tapete claro com relevo visível")})


def test_every_criterion_of_the_rubric_is_required_and_no_other(src):
    g, ref, _ = src
    missing = {k: v for k, v in SCORES.items() if k != "iluminacao"}
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, scores=missing)
    assert "iluminacao: sem nota" in e.value.reasons
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, scores={**SCORES, "geral": (5, "tudo ótimo")})
    assert any("critério fora da rubrica" in r for r in e.value.reasons)


def test_image_not_in_the_run_or_no_image_is_refused(src):
    g, ref, _ = src
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, shas=["f" * 64])
    assert any(r.startswith("imagem fora do run") for r in e.value.reasons)
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, shas=[])
    assert "nenhuma imagem citada" in e.value.reasons


def test_only_render_runs_are_evaluated(src):
    g, ref, _ = src
    item = g.get(OK_BUILD)
    with pytest.raises(V.EvaluationRejected) as e:
        V.new_evaluation(item, CLAUDE, reference=ref, camera="preview", scores=SCORES,
                         image_shas=[item.images[0].sha256],
                         delivery=delivered(ref, [item.images[0].sha256]))
    assert "só run de render é avaliado" in e.value.reasons


def test_qwen_is_marked_low_confidence(src):
    g, ref, _ = src
    _, ev = evaluate(g, ref, evaluator=QWEN)
    assert ev.confidence == "baixa" and V.LOW_CONFIDENCE_NOTE in ev.limitations
    d = ev.to_dict()
    d["confidence"] = "normal"
    assert "qwen2.5vl precisa sair com confiança baixa até a calibração (D-S01C-2)" in V.check(d, g.get(OK_RENDER))


def test_no_0_to_100_score_is_ever_recorded(src, tmp_path):
    g, ref, roots = src
    item, ev = evaluate(g, ref)
    store = V.EvaluationStore(tmp_path / "data", protected=roots)
    path = store.save(ev, item)
    saved = json.loads(path.read_text(encoding="utf-8"))
    nums = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            nums.append(o)
    walk(saved)
    assert nums and all(1 <= n <= 5 for n in nums)                               # só as notas por critério
    for key in ("nota_0_100", "total", "media", "weighted_score", "similaridade"):
        assert any(key in r for r in V.check({**saved, key: 77}, item))


@pytest.mark.parametrize("field,value", [("HUMAN_APPROVAL", "APPROVED"), ("verdict", "PASS")])
def test_runtime_never_records_human_approval_or_a_verdict(src, field, value):
    g, ref, _ = src
    item, ev = evaluate(g, ref)
    assert V.check({**ev.to_dict(), field: value}, item)
    human = {**ev.to_dict(), "evaluator": {**ev.evaluator, "kind": "human"}}
    assert any("o runtime não grava avaliação humana" in r for r in V.check(human, item))


def test_store_writes_outside_every_source_and_reads_back_only_valid(src, tmp_path):
    g, ref, roots = src
    item, ev = evaluate(g, ref)
    store = V.EvaluationStore(tmp_path / "data", protected=roots)
    store.save(ev, item)
    d = store.root / PLANT / ROOM / OK_RENDER
    (d / "zz-broken.json").write_text("{", encoding="utf-8")
    (d / "zz-no-justification.json").write_text(json.dumps(
        {**ev.to_dict(), "scores": {**ev.scores, "iluminacao": {"score": 4, "justification": ""}}}), encoding="utf-8")
    ok, bad = store.for_item(item)
    assert [e["id"] for e in ok] == [ev.id] and len(bad) == 2
    assert store.for_item(item, rubric_version="rubric-v0")[0] == []              # rubricas não se misturam


def test_an_evaluation_on_render4_never_makes_it_eligible(src, tmp_path):
    g, ref, roots = src
    item = g.get(R4)
    ev = V.new_evaluation(item, CLAUDE, reference=ref, camera="render_dia_cama_3q", scores=SCORES,
                          image_shas=[item.images[0].sha256],
                         delivery=delivered(ref, [item.images[0].sha256]))
    store = V.EvaluationStore(tmp_path / "data", protected=roots)
    store.save(ev, item)
    evals, _ = store.for_item(item)
    assert len(evals) == 1 and not item.eligible(evals)                         # FAIL continua não elegível


def test_rubric_v1_is_the_annex_a_table():
    assert [(c.label, c.weight_pct) for c in V.RUBRIC_V1.criteria] == [
        ("Fidelidade à referência", 25), ("Cama, cabeceira e enxoval", 25), ("Materiais e texturas", 20),
        ("Iluminação", 15), ("Mobiliário e detalhes", 15)]


def test_engine_root_is_protected(src, tmp_path):
    g, ref, roots = src
    item, ev = evaluate(g, ref)
    inside = V.EvaluationStore(Path(roots[1]) / "x", protected=roots)
    with pytest.raises(E.EvidenceError):
        inside.save(ev, item)


# ------------------------------------------------------------- F1-05 sobre o caminho do #94: só com entrega confirmada
def test_evaluation_without_a_confirmed_delivery_is_refused(src):
    g, ref, _ = src
    item = g.get(OK_RENDER)
    shas = [item.images[0].sha256]
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, delivery=delivered(ref, shas, confirmed=False))
    assert any("não confirmada pelo provedor" in r for r in e.value.reasons)
    _, ev = evaluate(g, ref)
    d = ev.to_dict()
    del d["delivery"]
    assert any("sem a entrega ao modelo" in r for r in V.check(d, item))


def test_cited_images_and_reference_must_be_the_ones_delivered(src):
    g, ref, _ = src
    item = g.get(OK_RENDER)
    other = [i.sha256 for i in item.images if i.sha256 != item.images[0].sha256] or ["c" * 64]
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, delivery=delivered(ref, other))                     # entregou outra imagem
    assert any("não são as imagens de render entregues" in r for r in e.value.reasons)
    dl = delivered(ref, [item.images[0].sha256])
    dl["images"][0]["sha256"] = "d" * 64                                       # referência entregue ≠ a citada
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, delivery=dl)
    assert any("referência da avaliação não foi a imagem de referência entregue" in r for r in e.value.reasons)


def test_rubric_prompt_asks_one_to_five_per_criterion_in_json_and_no_total():
    p = V.rubric_prompt()
    assert all(f"- {c.key}: {c.label}" in p for c in V.RUBRIC_V1.criteria)
    assert "nota inteira de 1 a 5" in p and "SOMENTE com um objeto JSON" in p
    assert "Não dê nota total, média, porcentagem nem nota de 0 a 100" in p


MODEL_JSON = ("Segue a avaliação:\n```json\n{"
              + ", ".join(f'"{k}": {{"score": {s}, "justification": "{j}"}}' for k, (s, j) in SCORES.items())
              + ', "total": 15}\n```')


def test_parse_scores_reads_every_criterion_and_drops_fields_outside_the_rubric():
    scores, notes = V.parse_scores(MODEL_JSON)
    assert scores == SCORES
    assert notes == ["o modelo devolveu campos fora da rubrica, descartados: total"]


@pytest.mark.parametrize("text,why", [
    ("não consigo avaliar", "não tem o objeto JSON"),
    ('{"fidelidade_referencia": {"score": 3, "justification": "x"}', "ilegível"),              # JSON cortado
    ('{"fidelidade_referencia": {"score": 3, "justification": "x"}}', "cama_cabeceira_enxoval: o modelo não deu nota"),
    ('{"a": 1,}', "ilegível")])
def test_parse_scores_refuses_what_it_cannot_read_without_inventing(text, why):
    with pytest.raises(V.EvaluationRejected) as e:
        V.parse_scores(text)
    assert any(why in r for r in e.value.reasons), e.value.reasons


def test_score_out_of_range_from_the_model_is_refused_not_clamped(src):
    g, ref, _ = src
    bad = {**SCORES, "iluminacao": (7, "muito boa")}
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, scores=bad)
    assert "iluminacao: nota precisa ser inteira de 1 a 5 (veio 7)" in e.value.reasons


def test_from_vision_refuses_a_result_that_the_provider_did_not_confirm(src):
    g, ref, _ = src

    class NotConfirmed:
        text, deliveries, session_id, usage = MODEL_JSON, [], "s", None

        def confirmation(self):
            return False, "entregue ao adaptador; contagem do provedor indisponível"
    with pytest.raises(V.EvaluationRejected) as e:
        V.from_vision(NotConfirmed(), g.get(OK_RENDER), ref, QWEN)
    assert e.value.reasons == ["não confirmado pelo provedor: entregue ao adaptador; contagem do provedor indisponível"]


def test_summary_line_lists_each_criterion_without_a_total(src):
    g, ref, _ = src
    _, ev = evaluate(g, ref, evaluator=QWEN)
    line = V.summary(ev.to_dict())
    assert line == ("qwen2.5vl:7b, confiança baixa (câmera cama_3q · luz dia): Fidelidade 3/5 · Cama 2/5 · Materiais 3/5 · Iluminação 4/5 · "
                    "Mobiliário 3/5")
    assert "/100" not in line and "total" not in line


def test_a_score_outside_one_to_five_is_kept_as_given_so_the_record_refuses_it():
    import json as _json
    bad = {k: {"score": s, "justification": j} for k, (s, j) in SCORES.items()}
    bad["iluminacao"]["score"] = 7
    scores, _ = V.parse_scores(_json.dumps(bad))
    assert scores["iluminacao"] == (7, SCORES["iluminacao"][1])                 # nunca "corrigido" para 5


# --------------------------------------------------------------------------- revisão do #97 @ 7332319
def test_rubric_prompt_has_no_anchor_score_and_its_format_line_is_refused():
    import re as _re
    p = V.rubric_prompt()
    assert not _re.search(r'"score":\s*\d', p)                                   # nenhuma nota de exemplo
    assert '"justification": "..."' not in p
    assert "UM elemento concreto visto na referência e UM visto no render" in p and V.NO_DETAIL in p
    line = next(x for x in p.splitlines() if x.startswith("{"))
    with pytest.raises(V.EvaluationRejected):                                     # o exemplo nunca vira avaliação
        V.parse_scores(line)


@pytest.mark.parametrize("j,why", [("...", "justificativa trivial"), ("ok", "justificativa trivial"),
                                   ("similar ao render", "justificativa trivial"),
                                   (V.JUSTIFICATION_SLOT, "justificativa é o marcador do formato"), ("   ", "sem justificativa")])
def test_trivial_justification_is_refused(src, j, why):
    g, ref, _ = src
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, scores={**SCORES, "iluminacao": (4, j)})
    assert any(r.startswith("iluminacao: " + why) for r in e.value.reasons), e.value.reasons


def test_an_evaluation_of_another_run_in_this_runs_folder_is_refused(src, tmp_path):
    import json as _json
    g, ref, roots = src
    a, ev = evaluate(g, ref, run=OK_RENDER)
    b = next(i for i in g.of(PLANT, ROOM) if i.kind == "render" and i.item_id != OK_RENDER and i.images)
    store = V.EvaluationStore(tmp_path / "data", protected=roots)
    d = store.root / b.plant / b.room / b.item_id
    d.mkdir(parents=True)
    (d / "copiada.json").write_text(_json.dumps(ev.to_dict()), encoding="utf-8")   # arquivo de A na pasta de B
    ok, bad = store.for_item(b)
    assert ok == [] and any("avaliação não é deste run" in x for x in bad)


def test_save_refuses_an_evaluation_edited_after_it_was_built(src, tmp_path):
    g, ref, roots = src
    item, ev = evaluate(g, ref)
    ev.scores["iluminacao"]["score"] = 0
    with pytest.raises(V.EvaluationRejected) as e:
        V.EvaluationStore(tmp_path / "data", protected=roots).save(ev, item)
    assert "iluminacao: nota precisa ser inteira de 1 a 5 (veio 0)" in e.value.reasons


def test_a_raw_sketchup_view_is_never_a_rubric_image(src):
    g, ref, _ = src
    item = g.get(OK_RENDER)
    raw = G.GalleryImage("cama_3q", item.images[0].path, "e" * 64, "r", "views/cama_3q.png")
    item.images.append(raw)
    with pytest.raises(V.EvaluationRejected) as e:
        evaluate(g, ref, shas=[raw.sha256])
    assert "vista crua sem render não entra na rubrica: cama_3q" in e.value.reasons


def test_no_detail_answers_are_kept_and_marked_as_limitations(src):
    g, ref, _ = src

    class Confirmed:
        session_id, usage = "s", None

        def __init__(self, item, refimg):
            from types import SimpleNamespace as NS
            self.deliveries = [NS(role="reference", sha256=refimg.sha256, batch_id="b" * 32, status="SENT"),
                               NS(role="render", sha256=item.images[0].sha256, batch_id="b" * 32, status="SENT")]
            js = {k: {"score": s, "justification": j} for k, (s, j) in SCORES.items()}
            js["iluminacao"]["justification"] = f"na referência: sanca quente; no render: {V.NO_DETAIL}"
            self.text = _json.dumps(js)

        def confirmation(self):
            return True, "o provedor contou … o acréscimo só fecha com as 2 imagens"
    import json as _json
    item = g.get(OK_RENDER)
    ev = V.from_vision(Confirmed(item, ref.images[0]), item, ref, QWEN)
    assert f"iluminacao: {V.NO_DETAIL}" in ev.limitations and V.UNCHECKED_NOTE in ev.limitations


# ------------------------------------------------- D-S01C-2: avaliação sem calibração não torna o run elegível
def test_a_run_with_only_an_uncalibrated_low_confidence_evaluation_is_not_eligible(src, tmp_path):
    g, ref, roots = src
    item, ev = evaluate(g, ref, evaluator=QWEN)                                  # qwen: confiança baixa
    assert ev.confidence == "baixa"
    item.technical_blockers = lambda: []                                          # só a avaliação decide aqui
    store = V.EvaluationStore(tmp_path / "data", protected=roots)
    store.save(ev, item)
    evals, _ = store.for_item(item)
    assert len(evals) == 1
    assert not item.eligible(evals)
    assert item.eligibility(evals) == [G.UNCALIBRATED_ONLY]
    assert G.UNCALIBRATED_ONLY == "só avaliações sem calibração aprovada (S01C-F1-06)"
    assert item.states(evals)["ELEGÍVEL"] == f"não: {G.UNCALIBRATED_ONLY}"


def test_today_no_evaluator_has_an_approved_calibration():
    """#100 MUST: a F1-06 ainda não aprovou calibração nenhuma; o registro começa vazio."""
    assert G.APPROVED_CALIBRATIONS == frozenset()


def test_a_normal_confidence_evaluation_without_approved_calibration_does_not_count(src):
    """#100 MUST: "sem calibração" não é só o prefixo do qwen; QUALQUER avaliador fora do registro não pesa."""
    g, ref, _ = src
    item, ev = evaluate(g, ref, evaluator=CLAUDE)                                # confiança normal, mas sem calibração
    item.technical_blockers = lambda: []
    assert ev.confidence == "normal"
    assert not item.eligible([ev.to_dict()])
    assert item.eligibility([ev.to_dict()]) == [G.UNCALIBRATED_ONLY]


def test_only_an_evaluator_with_a_registered_approved_calibration_counts(src, monkeypatch):
    g, ref, _ = src
    item, ev = evaluate(g, ref, evaluator=CLAUDE)
    item.technical_blockers = lambda: []
    key = (CLAUDE.provider, CLAUDE.model, CLAUDE.digest)
    monkeypatch.setattr(G, "APPROVED_CALIBRATIONS", frozenset({key}))
    assert item.eligible([ev.to_dict()])
    _, low = evaluate(g, ref, evaluator=QWEN)
    assert item.eligible([low.to_dict(), ev.to_dict()])                            # a não calibrada não atrapalha
    other = {**ev.to_dict(), "evaluator": {**ev.to_dict()["evaluator"], "digest": "f" * 64}}
    assert not item.eligible([other])                                              # outro digest = outra calibração


def test_the_best_card_says_why_when_only_uncalibrated_evaluations_exist(src):
    g, ref, _ = src
    item, ev = evaluate(g, ref, evaluator=QWEN)
    best = G.cards([item], lambda i: [ev.to_dict()] if i is item else [])[2]
    assert best.item is None and "só avaliações sem calibração" in best.text
