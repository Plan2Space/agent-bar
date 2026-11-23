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


def evaluate(g, ref, evaluator=CLAUDE, run=OK_RENDER, scores=None, shas=None, **kw):
    item = g.get(run)
    return item, V.new_evaluation(item, evaluator, reference=ref, camera="dia_cama_3q",
                                  scores=scores if scores is not None else SCORES,
                                  image_shas=shas if shas is not None else [item.images[0].sha256],
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
                         image_shas=[item.images[0].sha256])
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
                          image_shas=[item.images[0].sha256])
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
