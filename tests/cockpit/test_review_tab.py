"""F7.2: aba "Revisão visual" — dois runs lado a lado por câmera compatível, estados separados, ausências explícitas."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402

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
        stages = [tab.history.topLevelItem(i).text(1) for i in range(tab.history.topLevelItemCount())]
        assert stages == ["A", "B", "C", "R1"]
        r1 = tab.history.topLevelItem(3)
        assert r1.text(4) == "ausente (não publicado)" and r1.text(9) == "PENDING_HUMAN"
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


# ------------------------------------------------------------------- S01C-F1-04: 3 cartões e colunas do histórico
def history_rows(tab):
    h = tab.history
    return [[h.topLevelItem(i).text(c) for c in range(h.columnCount())] for i in range(h.topLevelItemCount())]


def test_design_opens_on_latest_and_the_gallery_shows_three_big_cards(tmp_path):
    from tests.cockpit.gallery_fixture import UNPUB_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        assert tab.pages.tabText(0) == "Últimas" and tab.pages.currentIndex() == 0          # F2: abre nas últimas
        assert tab.pages.tabText(1) == "Galeria de design" and tab.pages.tabText(2) == "Detalhes técnicos"
        c = tab.design.cards
        assert [c[k].title.text() for k in c] == ["REFERÊNCIA", "ÚLTIMO RENDER", "MELHOR RENDER"]
        ref = c["referência"]
        assert ref.item.item_id == "ref-quarto-r2@v1" and not ref.thumb.pixmap().isNull()   # miniatura real
        assert "não é geometria validada" in ref.info.text() and "sintética" in ref.info.text()
        last = c["último"]
        assert last.item.item_id == UNPUB_FAIL and not last.thumb.pixmap().isNull()       # maior started_at
        for piece in ("23/11 06:16 UTC", UNPUB_FAIL, "Geometria: PASS", "Render: FAIL", "Avaliação: sem avaliação"):
            assert piece in last.info.text(), piece
        best = c["melhor"]
        assert best.item is None and best.thumb.path is None
        assert best.info.text().startswith("Ainda não avaliado") and best.thumb.text() == "Ainda não avaliado"
        assert not best.enlarge_btn.isEnabled()
    finally:
        dispose(tab)


def test_best_card_never_shows_an_invalid_unpublished_or_unruled_render(tmp_path, monkeypatch):
    from agent_bar.cockpit import gallery as G
    from tests.cockpit.gallery_fixture import (
        CAL,
        CAL_KEY,
        OK_RENDER,
        PLANT,
        PUB_OK,
        R4,
        ROOM,
        build_negatives,
    )
    monkeypatch.setattr(G, "APPROVED_CALIBRATIONS", frozenset({CAL_KEY}))            # #100: calibração de teste
    tab = _gallery_tab(tmp_path)
    try:
        for evaluated in (OK_RENDER, R4):                                       # não publicado; FAIL
            tab.evaluations_for = lambda item, e=evaluated: [CAL] if item.item_id == e else []
            tab._fill_gallery()
            best = tab.design.cards["melhor"]
            assert best.item is None and best.info.text().startswith("Nenhum render elegível")   # há avaliação
            assert "nenhum render elegível" in best.info.text() and best.thumb.path is None
        ev, engine, dl = build_negatives(tmp_path / "neg")
        g = G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])
        tab.design.set_items(g.of(PLANT, ROOM), lambda item: [CAL] if item.item_id == PUB_OK else [])
        best = tab.design.cards["melhor"]                                        # elegível existe, regra ainda não
        assert best.item is None and "S01C-F1-07" in best.info.text()
    finally:
        dispose(tab)


def test_history_gallery_is_newest_first_with_real_thumbnails_and_states(tmp_path):
    from tests.cockpit.gallery_fixture import R4, R4B, UNPUB_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        h = tab.design.history
        ids = [h.item(i).data(Qt.ItemDataRole.UserRole) for i in range(h.count())]
        renders = [i.item_id for i in tab.gallery_items() if i.kind == "render"]
        assert ids == renders and ids[0] == UNPUB_FAIL and R4B not in ids        # só renders, mais recente primeiro
        tab.design.history_toggle.click()                                    # UX-14: miniaturas sob demanda
        assert all(not h.item(i).icon().isNull() for i in range(h.count()) if tab.gallery.get(ids[i]).images)
        first = h.item(0).text()
        assert first.startswith("23/11 06:16 UTC") and "câmera cama_3q · luz dia" in first and "visual: sem avaliação" in first
        r4 = h.item(ids.index(R4)).text()
        assert "render FAIL" in r4 and "FAIL, não elegível" in r4                # FAIL visível e identificado
        import re
        assert not re.search(r"[0-9a-f]{12,}", "".join(h.item(i).text() for i in range(h.count())))  # hash: detalhes
    finally:
        dispose(tab)


def test_compare_with_reference_and_with_previous_version_side_by_side(tmp_path):
    from tests.cockpit.gallery_fixture import BAD_SHA, UNPUB_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        d = tab.design
        d.history.setCurrentRow(0)
        d.compare_with_reference()
        assert d.compared[0].item_id == UNPUB_FAIL and d.compared[1].item_id == "ref-quarto-r2@v1"
        assert "qualitative-different-camera" in d.compare_label.text()
        assert not d.left.pixmap().isNull() and not d.right.pixmap().isNull()
        d.compare_with_previous()
        assert d.compared[1].item_id == BAD_SHA                                   # o render anterior
        assert d.left.path.name == d.right.path.name == "dia_cama_3q.png"          # mesmo quadro, se existir
        assert "não é par de câmera conferido" in d.compare_label.text() and "VS-R1a" in d.compare_label.text()
    finally:
        dispose(tab)


def test_enlarge_and_technical_details_navigation(tmp_path):
    from tests.cockpit.gallery_fixture import R4
    tab = _gallery_tab(tmp_path)
    try:
        d = tab.design
        d.cards["referência"].enlarge_btn.click()
        assert d.enlarged is not None and d.enlarged.isVisible()
        h = d.history
        h.setCurrentRow(next(i for i in range(h.count()) if h.item(i).data(Qt.ItemDataRole.UserRole) == R4))
        d.tech_btn.click()
        assert tab.pages.currentWidget() is tab.tech_page and tab.details.currentWidget() is tab.gallery_view
        assert tab.gallery_tree.currentItem().text(3) == R4
        assert "MELHOR: não escolhido" in tab.gallery_detail.text()
    finally:
        dispose(tab)


def test_history_is_newest_first_with_source_visual_and_trend_columns(tmp_path):
    from tests.cockpit.gallery_fixture import R4, R4B, UNPUB_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        assert [tab.history.headerItem().text(c) for c in range(tab.history.columnCount())] == [
            "Data", "Etapa", "Run", "Fonte", "Situação", "Geometria", "Funcional", "Visual", "Tendência", "Humano"]
        rows = history_rows(tab)
        assert rows[0][2] == UNPUB_FAIL and rows[0][0] == "23/11 06:16 UTC" and rows[0][3] == "motor, não publicado"
        r4 = next(r for r in rows if r[2] == R4)
        assert r4[1] == "R4-render" and r4[3] == "publicado" and r4[4] == "render FAIL"
        assert all(r[2] != R4B for r in rows)                                    # uma vez, como publicado (VS-R4)
        renders = [r for r in rows if r[4].startswith("render")]
        assert renders and all(r[7] == "sem avaliação" and r[8] == "inconclusivo (sem avaliação)" for r in renders)
        assert all(r[9] == "PENDING_HUMAN" for r in rows)
        dated = [r[0] for r in rows if r[0] not in ("—", "sem data")]
        assert dated == sorted(dated, key=lambda d: (d[3:5], d[0:2], d[6:]), reverse=True)
    finally:
        dispose(tab)


def test_cards_follow_the_evaluation_hook(tmp_path):
    from tests.cockpit.gallery_fixture import OK_RENDER
    tab = _gallery_tab(tmp_path)
    try:
        tab.evaluations_for = lambda item: ["avaliação"] if item.item_id == OK_RENDER else []
        tab._fill_gallery()
        assert tab.design.cards["melhor"].info.text().startswith("Nenhum render elegível")  # não publicado
        row = next(r for r in history_rows(tab) if r[2] == OK_RENDER)
        assert row[7] == "1 avaliação(ões)"
        h = tab.design.history
        assert "visual: 1 avaliação(ões)" in next(h.item(i).text() for i in range(h.count())
                                                   if h.item(i).data(Qt.ItemDataRole.UserRole) == OK_RENDER)
    finally:
        dispose(tab)


# --------------------------------------------------------------- revisão do #89 @ 52fc17c: VERIFICADO na tela principal
def _neg_items(tmp_path):
    from agent_bar.cockpit import gallery as G
    from tests.cockpit.gallery_fixture import PLANT, ROOM, build_negatives
    ev, engine, dl = build_negatives(tmp_path / "neg")
    g = G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])
    return g, g.of(PLANT, ROOM)


def test_unverified_render_never_shows_the_manifest_pass_on_the_main_screen(tmp_path):
    from agent_bar.cockpit import gallery as G
    from tests.cockpit.gallery_fixture import REPORT_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        g, items = _neg_items(tmp_path)
        n1 = g.get(REPORT_FAIL)                                                  # manifest PASS, render_report FAIL
        tab.design.set_items([n1] + [i for i in items if i.source == G.REFERENCE])
        card = tab.design.cards["último"].info.text()
        tile = tab.design.history.item(0).text()
        for shown in (card, tile):
            for claim in ("Render: PASS", "render PASS", "Geometria: PASS", "geometria PASS"):
                assert claim not in shown, shown                                  # nem render nem geometria do manifest
            assert "não verificado" in shown and "render_report diz FAIL" in shown, shown
    finally:
        dispose(tab)


def test_unpublished_items_carry_the_vs_r1a_text_on_card_tile_and_comparison(tmp_path):
    from agent_bar.cockpit import gallery as G
    tab = _gallery_tab(tmp_path)
    try:
        d = tab.design
        assert G.UNPUBLISHED_NOTE in d.cards["último"].info.text()
        h = d.history
        for i in range(h.count()):
            it = tab.gallery.get(h.item(i).data(Qt.ItemDataRole.UserRole))
            assert (G.UNPUBLISHED_NOTE in h.item(i).text()) == (it.source == G.ENGINE), h.item(i).text()
        h.setCurrentRow(0)
        d.compare_with_previous()
        assert G.UNPUBLISHED_NOTE in d.left_cap.text() and G.UNPUBLISHED_NOTE in d.right_cap.text()
        d.compare_with_reference()
        assert "referência" in d.right_cap.text() and "sintética" in d.right_cap.text()
    finally:
        dispose(tab)


def test_best_card_headline_is_the_real_reason(tmp_path):
    from tests.cockpit.gallery_fixture import OK_RENDER
    tab = _gallery_tab(tmp_path)
    try:
        assert tab.design.cards["melhor"].info.text().startswith("Ainda não avaliado")   # sem nenhuma avaliação
        tab.evaluations_for = lambda item: ["avaliação"] if item.item_id == OK_RENDER else []
        tab._fill_gallery()
        best = tab.design.cards["melhor"]
        assert best.info.text().startswith("Nenhum render elegível") and best.thumb.text() == "Nenhum render elegível"
    finally:
        dispose(tab)


def test_best_card_refuses_an_item_that_is_not_eligible(tmp_path, monkeypatch):
    from agent_bar.cockpit import gallery as G
    from tests.cockpit.gallery_fixture import UNPUB_FAIL
    tab = _gallery_tab(tmp_path)
    try:
        bad = tab.gallery.get(UNPUB_FAIL)                                          # FAIL e não publicado
        real = G.cards
        monkeypatch.setattr(G, "cards", lambda items, ev=lambda _i: []: [*real(items, ev)[:2],
                                                                           G.Card("melhor conhecido", bad, "forçado")])
        tab._fill_gallery()
        best = tab.design.cards["melhor"]
        assert best.item is None and best.thumb.path is None                       # o cartão não aceita inelegível
    finally:
        dispose(tab)


def test_design_view_card_tile_and_previous_comparison_use_the_preferred_camera(tmp_path):
    """Pedido do coordenador: a miniatura não é o close do banco; a comparação usa a mesma câmera, e diz quando não dá."""
    from agent_bar.cockpit import gallery as G
    from agent_bar.cockpit.ui.design_view import DesignView
    from tests.cockpit.evidence_fixture import png

    def run(run_id, started, *keys):
        imgs = []
        for k in keys:
            p = tmp_path / run_id / f"{k}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png(f"{run_id}/{k}"))
            imgs.append(G.GalleryImage(k, p, G.file_sha256(p), "r", f"views/{k}.png"))
        return G.GalleryItem(G.PUBLISHED, "planta_74", "suite_01", run_id, "render", started_at=started,
                             render_verdict="PASS", build={"geometria": "PASS"}, images=imgs)
    rc5 = run("rc5", "2026-11-23T09:11:00Z", "render_entardecer_close_banco", "render_entardecer_perspectiva",
              "render_entardecer_r2_canto")
    rc4 = run("rc4", "2026-11-23T08:06:00Z", "render_entardecer_close_banco", "render_entardecer_perspectiva")
    rc0 = run("rc0", "2026-11-22T08:00:00Z", "render_dia_cama_3q")
    d = DesignView()
    try:
        d.set_items([rc5, rc4, rc0])
        assert d.cards["último"].thumb.path.name == "render_entardecer_r2_canto.png"
        assert "câmera r2_canto · luz entardecer" in d.cards["último"].info.text()
        assert "câmera perspectiva · luz entardecer" in d.history.item(1).text()     # rc4: perspectiva, não o banco
        d.history.setCurrentRow(0)
        d.compare_with_previous()
        assert d.left.path.name == d.right.path.name == "render_entardecer_perspectiva.png"
        assert "rc4 não tem a câmera r2_canto" in d.compare_label.text()
        d.history.setCurrentRow(1)
        d.compare_with_previous()                                                 # rc4 × rc0: sem câmera em comum
        assert (d.left.path.name, d.right.path.name) == ("render_entardecer_perspectiva.png", "render_dia_cama_3q.png")
        assert "sem câmera em comum" in d.compare_label.text()
    finally:
        d.shutdown()
        d.deleteLater()
