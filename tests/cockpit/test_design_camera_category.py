"""UX-14b (D-UX-3 + pedido literal do Felipe): "Último render real da câmera selecionada" e "Organizar referências por
categoria e apresentar somente a referência vigente de cada categoria, sem duplicação". Selecionar a câmera atualiza a
tela principal sem trocar de aba; nenhuma regra de elegibilidade muda."""
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
    img = QImage(32, 18, QImage.Format.Format_RGB32)
    img.fill(0x223344)
    img.save(str(p))
    return p


def render(tmp_path, n: int, *keys: str) -> G.GalleryItem:
    run = f"202611{n:02d}-planta_74_suite_01-r{n}"
    imgs = [G.GalleryImage(k, png(tmp_path, f"{run}-{k}"), "0" * 64, "r", f"views/{k}.png") for k in keys]
    return G.GalleryItem(G.PUBLISHED, PLANT, ROOM, run, "render", started_at=f"2026-11-{n:02d}T10:00:00Z",
                         day=f"2026-11-{n:02d}", render_verdict="PASS",
                         build={"geometria": "PASS", "funcional": "PASS", "construção": "PASS"}, images=imgs)


def reference(tmp_path, rid: str, category: str, day: int) -> G.GalleryItem:
    img = G.GalleryImage("r2", png(tmp_path, rid), "0" * 64, "dl", f"refs/{rid}.png")
    return G.GalleryItem(G.REFERENCE, PLANT, ROOM, rid, "reference", started_at=f"2026-11-{day:02d}T00:00:00Z",
                         day=f"2026-11-{day:02d}", images=[img], category=category, title=rid)


def items(tmp_path):
    runs = [render(tmp_path, 10, "render_dia_r2_canto", "render_dia_perspectiva"),     # mais antigo, 2 câmeras
            render(tmp_path, 12, "render_entardecer_perspectiva"),                    # só perspectiva
            render(tmp_path, 14, "render_entardecer_r2_canto")]                       # o mais recente: só r2_canto
    refs = [reference(tmp_path, "ref-suite@v1", "suite", 1), reference(tmp_path, "ref-suite@v2", "suite", 5),
            reference(tmp_path, "ref-cama@v1", "bed", 3)]
    return sorted(runs, key=G.GalleryItem.sort_key, reverse=True) + refs


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


def test_rendered_cameras_and_the_image_of_a_camera(tmp_path):
    its = [render(tmp_path, 1, "render_dia_r2_canto", "perspectiva"), render(tmp_path, 2, "render_noite_cama_3q")]
    assert G.rendered_cameras(its) == ["r2_canto", "cama_3q"]                       # ordem fixa; vista crua não conta
    assert G.image_for_camera(its[0], "r2_canto").key == "render_dia_r2_canto"
    assert G.image_for_camera(its[0], "perspectiva") is None                       # só vista crua: não é render
    assert G.latest_render(its, camera="r2_canto") is its[0]


def test_current_references_one_per_category_without_duplicates(tmp_path):
    refs = [i for i in items(tmp_path) if i.source == G.REFERENCE]
    cur = G.current_references(refs)
    assert [(c, r.item_id) for c, r in cur] == [("bed", "ref-cama@v1"), ("suite", "ref-suite@v2")]


def test_choosing_a_camera_updates_the_last_render_card_and_the_comparison(view, tmp_path):
    view.set_items(items(tmp_path))
    labels = [view.camera_box.itemText(i) for i in range(view.camera_box.count())]
    assert labels[0].startswith("preferida") and "perspectiva" in labels and "r2_canto" in labels
    last = view.cards["último"]
    assert last.item.item_id.endswith("-r14") and last.image.key == "render_entardecer_r2_canto"
    view.camera_box.setCurrentIndex(labels.index("perspectiva"))
    assert last.item.item_id.endswith("-r12") and last.image.key == "render_entardecer_perspectiva"
    assert "perspectiva" in last.info.text()
    view.compare_with_previous()
    assert view.compared_images[0].key.endswith("perspectiva") and view.compared_images[1].key.endswith("perspectiva")
    assert view.visible()[1].key == "render_entardecer_perspectiva"              # comentar fala desta câmera


def test_the_reference_card_offers_the_current_reference_of_each_category(view, tmp_path):
    view.set_items(items(tmp_path))
    box = view.reference_box
    assert box.isVisibleTo(view) and [box.itemText(i) for i in range(box.count())] == ["bed", "suite"]
    assert view.cards["referência"].item.item_id == "ref-suite@v2"                # a vigente mais recente
    box.setCurrentIndex(0)
    assert view.cards["referência"].item.item_id == "ref-cama@v1" and view.reference.item_id == "ref-cama@v1"


def test_a_single_category_hides_the_reference_selector(view, tmp_path):
    its = [i for i in items(tmp_path) if i.category != "bed"]
    view.set_items(its)
    assert not view.reference_box.isVisibleTo(view)
    assert view.cards["referência"].item.item_id == "ref-suite@v2"


def test_the_reference_category_comes_from_the_design_library_manifest(tmp_path):
    import json
    cat = tmp_path / "catalog" / "ref-x"
    cat.mkdir(parents=True)
    (cat / "manifest.json").write_text(json.dumps({
        "reference_id": "ref-x", "version": 1, "category": "bed", "title": "x",
        "registration": {"project_id": PLANT, "room_id": ROOM, "registered_at": "2026-11-23T00:00:00Z"},
        "asset": {"path": str(tmp_path / "nao-existe.png"), "sha256": "0" * 64}}), encoding="utf-8")
    refs, _errs = G.load_references(tmp_path, PLANT, ROOM)
    assert refs[0].category == "bed"


def test_a_non_preferred_camera_drives_comparison_and_comment_even_when_the_run_has_the_preferred_one(view, tmp_path):
    runs = [render(tmp_path, 20, "render_dia_r2_canto", "render_dia_perspectiva"),
            render(tmp_path, 22, "render_noite_r2_canto", "render_noite_perspectiva")]
    view.set_items(sorted(runs, key=G.GalleryItem.sort_key, reverse=True))
    assert view.visible()[1].key == "render_noite_r2_canto"                       # sem escolha: a preferida
    labels = [view.camera_box.itemText(i) for i in range(view.camera_box.count())]
    view.camera_box.setCurrentIndex(labels.index("perspectiva"))
    assert view.visible()[1].key == "render_noite_perspectiva"                    # comentar fala da câmera escolhida
    view.compare_with_previous()
    assert [i.key for i in view.compared_images] == ["render_noite_perspectiva", "render_dia_perspectiva"]
