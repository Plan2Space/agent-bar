"""F7.2: aba "Revisão visual" — dois runs lado a lado por câmera compatível, estados separados, ausências explícitas."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import evidence as E  # noqa: E402
from agent_bar.cockpit.ui.review_tab import ReviewTab  # noqa: E402
from tests.cockpit.evidence_fixture import build  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def dispose(w):
    from PySide6.QtCore import QCoreApplication, QEvent
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def make(root: Path, expected=None, gallery_fn=None) -> ReviewTab:
    tab = ReviewTab(root_fn=lambda: root, load_fn=lambda r: E.load_catalog(r, expected=expected or {}),
                    gallery_fn=gallery_fn)                                      # None: sem ler o motor real do disco
    tab.resize(1200, 800)
    tab.reload()
    assert wait(lambda: tab.catalog is not None or tab.error)
    return tab


def items(tab):
    return [tab.cams.item(i).text() for i in range(tab.cams.count())]


def select(tab, stage_a, stage_b):
    for box, st in ((tab.a_box, stage_a), (tab.b_box, stage_b)):
        idx = next(i for i in range(box.count()) if box.itemData(i)[0] == st)
        box.setCurrentIndex(idx)
    tab._compare()


def test_two_runs_side_by_side_with_only_compatible_cameras_paired(tmp_path):
    tab = make(build(tmp_path / "ev"))
    try:
        select(tab, "A", "B")
        texts = items(tab)
        assert texts[:2] == ["● top", "● iso_sw"]
        assert any(t.startswith("⊘ interno — definição diferente") for t in texts)
        assert any(t.startswith("⊘ detalhe — só existe em B") for t in texts)
        assert "▣ pdf_x_skp (prancha do motor)" in texts
        tab.cams.setCurrentRow(0)
        assert tab.pane_a.path and tab.pane_a.path.name == "top.png" and tab.pane_b.path.name == "top.png"
        assert tab.pane_a.image.pixmap() is not None and not tab.pane_a.image.pixmap().isNull()
        assert "mesma câmera" in tab.measure.text() and "%" not in tab.measure.text().split("compare/")[0]
    finally:
        dispose(tab)


def test_refused_camera_explains_and_is_not_a_pair(tmp_path):
    tab = make(build(tmp_path / "ev"))
    try:
        select(tab, "A", "B")
        row = next(i for i, t in enumerate(items(tab)) if t.startswith("⊘ interno"))
        tab.cams.setCurrentRow(row)
        assert "comparação recusada para interno" in tab.measure.text() and "não são pares" in tab.measure.text()
    finally:
        dispose(tab)


def test_absent_or_invalid_run_shows_no_image_and_says_why(tmp_path):
    root = build(tmp_path / "ev")
    tab = make(root)
    try:
        select(tab, "A", "C")                                                  # C citado no índice, ausente no disco
        assert items(tab)[0].startswith("⊘ *") and "ausente" in items(tab)[0]
        tab.cams.setCurrentRow(0)
        assert tab.pane_b.path is None and "ausente" in tab.pane_b.image.text()
    finally:
        dispose(tab)


def test_states_are_four_separate_rows_and_human_is_pending(tmp_path):
    tab = make(build(tmp_path / "ev"))
    try:
        select(tab, "A", "B")
        rows = {tab.states.topLevelItem(i).text(0): (tab.states.topLevelItem(i).text(1), tab.states.topLevelItem(i).text(2))
                for i in range(4)}
        assert rows == {"GEOMETRY_PASS": ("PASS", "PASS"), "FUNCTIONAL_PASS": ("ausente", "PASS"),
                        "VISUAL_REVIEW": ("nenhuma revisão", "nenhuma revisão"),
                        "HUMAN_APPROVAL": ("PENDING_HUMAN", "PENDING_HUMAN")}
    finally:
        dispose(tab)


def test_missing_render_and_human_verdict_are_listed_explicitly(tmp_path):
    tab = make(build(tmp_path / "ev"))
    try:
        select(tab, "A", "B")
        text = tab.absent.toPlainText()
        assert "render de renderizador real (PoC 03)" in text
        assert "verdicts/human.json (HUMAN_APPROVAL → PENDING_HUMAN)" in text
        assert "verdicts/functional.json" in text                               # A não tem funcional
    finally:
        dispose(tab)


def test_history_lists_every_stage_including_unpublished(tmp_path):
    tab = make(build(tmp_path / "ev"), expected={("planta_x", "quarto"): ("A", "B", "R1")})
    try:
        stages = [tab.history.topLevelItem(i).text(0) for i in range(tab.history.topLevelItemCount())]
        assert stages == ["A", "B", "C", "R1"]
        r1 = tab.history.topLevelItem(3)
        assert r1.text(2) == "ausente (não publicado)" and r1.text(6) == "PENDING_HUMAN"
    finally:
        dispose(tab)


def test_repository_read_failure_is_shown_not_hidden(tmp_path):
    tab = make(tmp_path / "nao-existe")
    try:
        assert tab.catalog is None and "evidence indisponível" in tab.source.text()
        assert tab.room_box.count() == 0
    finally:
        dispose(tab)


def test_tampered_run_is_invalid_in_the_ui(tmp_path):
    root = build(tmp_path / "ev")
    victim = root / "planta_x" / "quarto" / "B" / "run-b" / "views" / "top.png"
    victim.write_bytes(victim.read_bytes() + b"x")
    tab = make(root)
    try:
        labels = [tab.b_box.itemText(i) for i in range(tab.b_box.count())]
        assert any(t.startswith("B · run-b — inválido") for t in labels)
    finally:
        dispose(tab)


REAL = os.environ.get("AGENT_BAR_EVIDENCE_DIR")


@pytest.mark.skipif(not REAL or not Path(REAL, "tools", "verify.py").is_file(), reason="checkout real do evidence ausente")
def test_real_suite01_d1_vs_poc01(tmp_path):
    tab = make(Path(REAL), expected={("planta_74", "suite_01"): ("D1", "D2", "PoC02", "PoC01", "R1")})
    try:
        select(tab, "D1", "PoC01")
        texts = items(tab)
        assert sum(t.startswith("●") for t in texts) == 6 and sum(t.startswith("⊘") for t in texts) == 4
        tab.cams.setCurrentRow(0)
        assert not tab.pane_a.image.pixmap().isNull() and not tab.pane_b.image.pixmap().isNull()
    finally:
        dispose(tab)


def test_a_failed_reload_never_keeps_showing_the_previous_pair(tmp_path):
    """Auditoria F0 (#43): com o evidence indisponivel, estados/relatorios/historico do par anterior continuavam na tela."""
    root = build(tmp_path / "ev")
    tab = make(root)
    try:
        select(tab, "A", "B")
        assert tab.states.topLevelItemCount() and tab.history.topLevelItemCount() and tab.reports.toPlainText()
        tab.root_fn = lambda: tmp_path / "nao-existe"
        tab.load_fn = lambda r: (_ for _ in ()).throw(FileNotFoundError(str(r)))
        tab.reload()
        assert wait(lambda: tab.catalog is None and tab.error)
        assert tab.states.topLevelItemCount() == 0 and tab.history.topLevelItemCount() == 0
        assert tab.reports.toPlainText() == "" and tab.absent.toPlainText() == "" and tab.cams.count() == 0
        assert tab.selected_runs() == (None, None)
        assert tab.pane_a.image.pixmap().isNull() and tab.pane_b.image.pixmap().isNull()
    finally:
        dispose(tab)


# ----------------------------------------------------------------------------------- S01C-F1-03: galeria multi-fonte
def _gallery_tab(tmp_path, *, evidence_ok=True):
    from agent_bar.cockpit import gallery as G
    from tests.cockpit.gallery_fixture import build_all
    ev, engine, dl = build_all(tmp_path)
    root = ev if evidence_ok else tmp_path / "nao-existe"
    tab = make(root, gallery_fn=lambda cat: G.build_gallery(cat, engine, dl, rooms=[("planta_x", "quarto")]))
    assert wait(lambda: tab.gallery is not None or tab.gallery_error)
    assert wait(lambda: tab.gallery_tree.topLevelItemCount() > 0)
    return tab


def gallery_rows(tab):
    t = tab.gallery_tree
    return {t.topLevelItem(i).text(3): [t.topLevelItem(i).text(c) for c in range(t.columnCount())]
            for i in range(t.topLevelItemCount())}


def test_gallery_tab_shows_render4_fail_and_not_eligible(tmp_path):
    from tests.cockpit.gallery_fixture import R4, R4B
    tab = _gallery_tab(tmp_path)
    try:
        assert tab.details.tabText(0) == "Galeria (todas as fontes)"
        rows = gallery_rows(tab)
        assert R4B not in rows
        r4 = rows[R4]
        assert r4[1] == "PUBLICADO" and r4[4] == "FAIL" and r4[11].startswith("não: ") and "render FAIL" in r4[11]
        assert r4[10] == "sim"                                                      # verificado, mas FAIL: visível
        assert all(not r[11].startswith("sim") for r in rows.values())             # sem avaliação: nada elegível
        assert "não publicado, não validado pelo verify.py" in tab.gallery_note.text()
    finally:
        dispose(tab)


def test_gallery_item_lists_images_by_ident_and_explains_rejections(tmp_path):
    from tests.cockpit.gallery_fixture import BAD_SHA
    tab = _gallery_tab(tmp_path)
    try:
        t = tab.gallery_tree
        row = next(t.topLevelItem(i) for i in range(t.topLevelItemCount()) if t.topLevelItem(i).text(3) == BAD_SHA)
        t.setCurrentItem(row)
        assert tab.gallery_images.count() == 1
        assert "Plan2Space/sketchup-mcp-next:artifacts/planta_x_quarto/" in tab.gallery_images.item(0).text()
        assert "@" in tab.gallery_images.item(0).text()
        assert "imagem recusada: noite_cama_3q: sha256 diverge do manifest" in tab.gallery_detail.text()
        assert "não publicado, não validado pelo verify.py" in tab.gallery_detail.text()
        assert "VERIFICADO: não: inválido" not in tab.gallery_detail.text()        # imagem recusada não invalida o run
        assert "ELEGÍVEL: não:" in tab.gallery_detail.text() and "MELHOR: não escolhido" in tab.gallery_detail.text()
        assert not tab.gallery_pane.image.pixmap().isNull()
    finally:
        dispose(tab)


def test_gallery_still_shows_the_engine_when_the_evidence_is_down(tmp_path):
    from tests.cockpit.gallery_fixture import R4
    tab = _gallery_tab(tmp_path, evidence_ok=False)
    try:
        assert tab.catalog is None and tab.room_box.count() == 1
        r4 = gallery_rows(tab)[R4]
        assert r4[1] == "MOTOR_NAO_PUBLICADO" and r4[4] == "FAIL" and r4[11].startswith("não: ")
        assert "evidence indisponível" in tab.gallery_note.text()
    finally:
        dispose(tab)
