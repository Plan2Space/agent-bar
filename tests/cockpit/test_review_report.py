"""F7.3: relatório de diferenças — categorias, estético × físico, medida com fonte, sem percentual, fora do evidence."""
from __future__ import annotations

import json
import os

import pytest

from agent_bar.cockpit import evidence as E
from agent_bar.cockpit import review_report as RR
from tests.cockpit.evidence_fixture import build


@pytest.fixture
def runs(tmp_path):
    root = build(tmp_path / "ev")
    cat = E.load_catalog(root, expected={})
    by = {r.stage: r for r in cat.runs}
    return root, by["A"], by["B"], by["C"]


def test_report_only_between_valid_runs(runs):
    root, a, b, c = runs
    with pytest.raises(ValueError):
        RR.DiffReport.new(a, c)
    rep = RR.DiffReport.new(a, b, clock=lambda: 1.0)
    assert rep.run_a["stage"] == "A" and len(rep.run_a["manifest_sha256"]) == 64
    assert rep.run_b["engine_commit"] == "c" * 40


def test_categories_and_natures_are_validated(runs):
    _, a, b, _ = runs
    rep = RR.DiffReport.new(a, b)
    assert rep.add(RR.Finding("material", "estetico", "cabeceira sem textura de tecido", "top")) == []
    assert rep.add(RR.Finding("mobiliario", "fisico", "criado colide com a porta", "iso_sw")) == []
    assert "categoria inválida" in rep.add(RR.Finding("beleza", "estetico", "x"))[0]
    assert "natureza" in rep.add(RR.Finding("material", "bonito", "x"))[0]
    assert "descrição vazia" in rep.add(RR.Finding("material", "estetico", "  "))
    assert rep.counts() == {"estetico": 1, "fisico": 1}


def test_measure_without_source_is_refused(runs):
    _, a, b, _ = runs
    rep = RR.DiffReport.new(a, b)
    errs = rep.add(RR.Finding("geometria_medida", "fisico", "folga lateral menor", measure=RR.Measure(0.55, "m", "", "")))
    assert any("medida sem fonte" in e for e in errs)
    errs = rep.add(RR.Finding("geometria_medida", "fisico", "folga lateral"))
    assert any("sem medida" in e for e in errs)
    src_path, src_sha = RR.measure_sources(b)[0]
    assert rep.add(RR.Finding("geometria_medida", "fisico", "folga lateral 0,55 m",
                              measure=RR.Measure(0.55, "m", src_path, src_sha, "B"))) == []


def test_measure_sources_come_from_the_run_manifest(runs):
    _, a, b, _ = runs
    paths = [p for p, _ in RR.measure_sources(b)]
    assert paths == ["verdicts/functional.json", "verdicts/geometry.json", "verdicts/tests.json"]
    files = {f["path"]: f["sha256"] for f in b.manifest["files"]}
    assert all(files[p] == s for p, s in RR.measure_sources(b))


def test_no_similarity_percentage_field_is_accepted(runs):
    _, a, b, _ = runs
    rep = RR.DiffReport.new(a, b)
    d = rep.to_dict()
    assert RR.validate(d) == []
    for bad in ({"similarity": 0.93}, {"score": 8}, {"visual_%": 90}, {"percent_match": 1}):
        assert RR.validate({**d, **bad}), bad
    d2 = rep.to_dict()
    d2["findings"] = [{"category": "material", "nature": "estetico", "description": "x", "camera": "", "fix": "codigo",
                       "measure": None, "similarity": 0.8}]
    assert any("campo proibido" in e for e in RR.validate(d2))


def test_save_is_atomic_outside_evidence_and_round_trips(runs, tmp_path):
    root, a, b, _ = runs
    store = RR.ReportStore(tmp_path / "data", evidence_root=root)
    rep = RR.DiffReport.new(a, b)
    rep.add(RR.Finding("composicao", "estetico", "quadro corta a cabeceira", "iso_sw", "codigo"))
    p = store.save(rep)
    assert p.is_file() and not E.is_inside(p, root)
    assert [x.name for x in p.parent.iterdir()] == [p.name]                   # sem temporário sobrando
    again = store.latest(a, b)
    assert again is not None and again.id == rep.id and again.findings[0].description == "quadro corta a cabeceira"
    assert json.loads(p.read_text(encoding="utf-8"))["counts"] == {"estetico": 1, "fisico": 0}


def test_saving_inside_the_evidence_is_refused(runs):
    root, a, b, _ = runs
    store = RR.ReportStore(root / "planta_x" / "quarto" / "A" / "run-a", evidence_root=root)   # destino malicioso
    rep = RR.DiffReport.new(a, b)
    with pytest.raises(E.EvidenceError, match="somente leitura"):
        store.save(rep)


def test_report_for_a_different_run_content_is_not_reused(runs, tmp_path):
    root, a, b, _ = runs
    store = RR.ReportStore(tmp_path / "data", evidence_root=root)
    store.save(RR.DiffReport.new(a, b))
    fake = E.Run(b.plant, b.room, b.stage, b.run_id, a.path, present=True, manifest=a.manifest)   # outro MANIFEST
    assert store.latest(a, fake) is None


# --------------------------------------------------------------------------------------------------------- GUI


def test_panel_adds_refuses_and_saves(runs, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    _ = qtw.QApplication.instance() or qtw.QApplication([])
    from PySide6.QtCore import QCoreApplication, QEvent

    from agent_bar.cockpit.ui.diff_panel import DiffPanel
    root, a, b, c = runs
    panel = DiffPanel(RR.ReportStore(tmp_path / "data", evidence_root=root))
    try:
        panel.set_pair(a, c, [])
        assert not panel.save_btn.isEnabled() and "válidos" in panel.header.text()
        panel.set_pair(a, b, ["top", "iso_sw"])
        assert "sem similaridade percentual" in panel.header.text()
        panel.category.setCurrentIndex(panel.category.findData("geometria_medida"))
        assert panel.nature.currentData() == "fisico" and panel.use_measure.currentData() is True
        panel.desc.setText("folga do pé da cama")
        panel.source.setCurrentIndex(-1)
        assert any("sem fonte" in e for e in panel.add_from_form())             # medida sem fonte: recusada
        panel.source.setCurrentIndex(0)
        panel.value.setValue(0.6)
        panel.unit.setText("m")
        panel.desc.setText("folga do pé da cama")
        assert panel.add_from_form() == []
        panel.category.setCurrentIndex(panel.category.findData("material"))
        panel.nature.setCurrentIndex(panel.nature.findData("estetico"))
        panel.use_measure.setCurrentIndex(0)
        panel.desc.setText("tecido liso, sem textura")
        assert panel.add_from_form() == []
        assert panel.table.topLevelItemCount() == 2 and "1 físico(s) · 1 estético(s)" in panel.status.text()
        path = panel.save()
        assert path and not E.is_inside(path, root)
    finally:
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
