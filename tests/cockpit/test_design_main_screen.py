"""UX-14 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9, decisão D-UX-3 aprovada pelo Felipe: "Approve everything").

Pedido literal: a tela principal do Design mostra a referência vigente e o ÚLTIMO render real, com os estados de
geometria, construção, renderização, publicação e avaliação sem confundi-los, e as ações ampliar, comparar e comentar.
Não renderiza 14 miniaturas na tela principal: histórico completo, runs antigos e FAILs ficam sob demanda. O melhor
render só ocupa espaço de destaque com avaliação válida e elegibilidade comprovada. Nenhuma regra de elegibilidade muda.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit.ui import design_view as DV  # noqa: E402

_APP = QApplication.instance() or QApplication([])
PLANT, ROOM = "planta_74", "suite_01"


def png(tmp_path: Path, name: str) -> Path:
    p = tmp_path / f"{name}.png"
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(0x334455)
    img.save(str(p))
    return p


def render(tmp_path, n: int, verdict="PASS", source=G.PUBLISHED) -> G.GalleryItem:
    run = f"202611{n:02d}-planta_74_suite_01-r{n}"
    img = G.GalleryImage("render_entardecer_r2_canto", png(tmp_path, run), "0" * 64, "r", f"views/{run}.png")
    return G.GalleryItem(source, PLANT, ROOM, run, "render", started_at=f"2026-11-{n:02d}T10:00:00Z",
                         day=f"2026-11-{n:02d}", render_verdict=verdict,
                         build={"geometria": "PASS", "funcional": "PASS", "construção": "PASS"}, images=[img])


def reference(tmp_path) -> G.GalleryItem:
    img = G.GalleryImage("r2", png(tmp_path, "ref"), "0" * 64, "dl", "refs/r2.png")
    return G.GalleryItem(G.REFERENCE, PLANT, ROOM, "ref-suite01@v2", "reference", day="2026-11-20", images=[img],
                         title="Suíte 01 r2", curation="CANDIDATE", synthetic=True, usage="conceptual-only",
                         provenance="GPT_GENERATED_ON_REQUEST")


@pytest.fixture
def view():
    d = DV.DesignView()
    d.resize(1280, 760)
    d.show()
    yield d
    from PySide6.QtCore import QCoreApplication, QEvent
    d.shutdown()
    d.close()
    d.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def fourteen(tmp_path):
    """14 runs: 10 PASS publicados, 3 FAIL e 1 não publicado (motor). O mais recente é o r14 (PASS publicado)."""
    items = [render(tmp_path, n, "FAIL" if n in (3, 7, 11) else "PASS",
                    G.ENGINE if n == 9 else G.PUBLISHED) for n in range(1, 15)]
    return sorted(items, key=G.GalleryItem.sort_key, reverse=True) + [reference(tmp_path)]


def test_fourteen_runs_show_one_render_on_the_main_screen_and_no_thumbnails(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    _APP.processEvents()
    assert view.cards["último"].item.item_id.endswith("-r14")                  # o último render real
    assert view.cards["referência"].item.item_id == "ref-suite01@v2"
    assert not view.history.isVisible() and not view.thumbs_loaded             # nada de 14 miniaturas sem pedir
    assert all(view.history.item(i).icon().isNull() for i in range(view.history.count()))
    assert "14 runs" in view.history_toggle.text() and "3 FAIL" in view.history_toggle.text()


def test_fail_and_unpublished_runs_are_only_in_the_history_on_demand(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    shown = {c.item.item_id for c in view.cards.values() if c.item is not None and c.isVisible()}
    assert not any(i.endswith(("-r3", "-r7", "-r11", "-r9")) for i in shown)
    view.history_toggle.click()
    _APP.processEvents()
    assert view.history.isVisible() and view.history.count() == 14 and view.thumbs_loaded
    assert not view.history.item(0).icon().isNull()                            # miniaturas só agora, sob demanda
    texts = [view.history.item(i).text() for i in range(14)]
    assert sum("FAIL" in t for t in texts) == 3 and any("motor" in t for t in texts)
    view.history_toggle.click()
    _APP.processEvents()
    assert not view.history.isVisible()


def test_the_last_render_card_keeps_the_five_states_apart(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    lines = view.cards["último"].info.text().splitlines()
    for state in ("Geometria:", "Construção:", "Render:", "Publicação:", "Avaliação:"):
        assert sum(line.startswith(state) for line in lines) == 1, (state, lines)   # uma linha por estado


def test_without_a_valid_evaluation_the_best_render_takes_no_prominent_space(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    _APP.processEvents()
    assert not view.cards["melhor"].isVisible()                                # nenhum cartão grande vazio
    assert view.best_note.isVisible() and "Melhor render" in view.best_note.text()
    assert "sem avaliação" in view.best_note.text()


def test_an_eligible_best_render_does_take_the_prominent_card(view, tmp_path, monkeypatch):
    items = fourteen(tmp_path)
    best = next(i for i in items if i.item_id.endswith("-r12"))
    monkeypatch.setattr(G, "cards", lambda its, ev: [G.Card("referência", None, ""), G.Card("último render", None, ""),
                                                     G.Card("melhor conhecido", best, "escolhido pela regra")])
    monkeypatch.setattr(G.GalleryItem, "eligible", lambda self, evs=(): self is best)
    view.set_items(items)
    _APP.processEvents()
    assert view.cards["melhor"].isVisible() and view.cards["melhor"].item is best and not view.best_note.isVisible()


def test_compare_and_comment_act_on_the_last_render_without_opening_the_history(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    assert view.selected().item_id.endswith("-r14")
    assert view.cmp_ref_btn.isEnabled() and view.cmp_prev_btn.isEnabled() and view.enlarge_btn.isEnabled()
    view.compare_with_reference()
    assert view.compared[0].item_id.endswith("-r14") and view.compared[1].source == G.REFERENCE
    seen = view.visible()
    assert seen is not None and seen[0].item_id.endswith("-r14")             # comentar fala do que está na tela


def test_choosing_a_run_in_the_open_history_moves_the_actions_to_it(view, tmp_path):
    view.set_items(fourteen(tmp_path))
    view.history_toggle.click()
    _APP.processEvents()
    row = next(i for i in range(view.history.count()) if view.history.item(i).text().splitlines()[0].endswith("r7"))
    view.history.setCurrentRow(row)
    assert view.selected().item_id.endswith("-r7")
    view.history_toggle.click()                                                # recolheu: volta ao último render
    assert view.selected().item_id.endswith("-r14")


def test_an_empty_room_says_so_without_a_wall_of_empty_cards(view):
    view.set_items([])
    _APP.processEvents()
    assert view.cards["último"].info.text() == "nenhum render do cômodo"
    assert "0 runs" in view.history_toggle.text() and not view.history_toggle.isEnabled()
    assert not view.cards["melhor"].isVisible()
