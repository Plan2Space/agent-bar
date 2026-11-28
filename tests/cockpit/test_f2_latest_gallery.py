"""F2 (Felipe, 2026-11-28): o Design abre nas últimas plantas e nos últimos renders, mais recentes primeiro, com
miniatura, data e "Abrir no SketchUp" / "Abrir pasta". Somente leitura: abrir é com o programa do Windows; nada é
escrito, copiado ou movido."""
from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar.cockpit import latest_designs as L  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _png(color: str) -> bytes:
    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    data = QByteArray()                                                     # referência viva enquanto o buffer escreve
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def _file(p: Path, mtime: float, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


def _skp(p: Path, mtime: float) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("meta/preview_thumbnail.png", _png("#3366cc"))
    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "engine"
    old = _skp(r / "suite" / "rc6b" / "planta.skp", 1_800_000_000)
    new = _skp(r / "suite" / "rc7b" / "planta.skp", 1_800_003_600)
    frente = _file(r / "suite" / "rc7b-render" / "renders" / "frente.png", 1_800_003_700, _png("#cc6633"))
    return r, old, new, frente


@pytest.fixture
def gallery(tree):
    from agent_bar.cockpit.ui.latest_gallery import LatestGallery
    root = tree[0]
    opened: list[tuple[str, str]] = []
    g = LatestGallery(scan_fn=lambda: L.scan([root]), open_file=lambda p: opened.append(("arquivo", str(p))),
                      open_folder=lambda p: opened.append(("pasta", str(p))))
    g.refresh()
    yield g, opened
    g.deleteLater()


def test_plants_and_renders_newest_first_with_thumbnail_and_date(gallery, tree):
    g, _ = gallery
    _root, old, new, frente = tree
    plants = [g.plants.item(i) for i in range(g.plants.count())]
    assert [it.data(L.PATH_ROLE) for it in plants] == [str(new), str(old)]
    first_line, run = plants[0].text().split("\n")
    assert run == "rc7b" and re.fullmatch(r"\d\d/\d\d \d\d:\d\d", first_line)   # data primeiro, depois a execução
    assert not plants[0].icon().isNull()                                     # a miniatura que o SketchUp gravou
    renders = [g.renders.item(i) for i in range(g.renders.count())]
    assert [it.data(L.PATH_ROLE) for it in renders] == [str(frente)] and not renders[0].icon().isNull()


def test_open_in_sketchup_and_open_folder_use_the_selected_plant(gallery, tree):
    g, opened = gallery
    g.plants.setCurrentRow(1)
    g.open_plant_btn.click()
    g.plant_folder_btn.click()
    assert opened == [("arquivo", str(tree[1])), ("pasta", str(tree[1]))]


def test_a_double_click_opens_the_render(gallery, tree):
    g, opened = gallery
    g.renders.itemDoubleClicked.emit(g.renders.item(0))
    assert opened == [("arquivo", str(tree[3]))]


def test_nothing_found_says_so_and_names_the_folders(tmp_path):
    from agent_bar.cockpit.ui.latest_gallery import LatestGallery
    g = LatestGallery(scan_fn=lambda: L.scan([tmp_path / "vazio"]), open_file=lambda p: None, open_folder=lambda p: None)
    g.refresh()
    try:
        assert g.plants.count() == 0 and "Nenhuma planta" in g.summary.text() and "vazio" in g.summary.text()
        assert not g.open_plant_btn.isEnabled()
    finally:
        g.deleteLater()


def test_the_design_area_opens_on_the_latest_page(tmp_path, monkeypatch):
    from agent_bar.cockpit.ui.latest_gallery import LatestGallery
    from agent_bar.cockpit.ui.review_tab import ReviewTab
    monkeypatch.setenv("AGENT_BAR_DESIGN_ROOTS", str(tmp_path / "nada"))
    tab = ReviewTab()
    try:
        assert tab.pages.tabText(0) == "Últimas" and isinstance(tab.pages.currentWidget(), LatestGallery)
        assert tab.pages.currentWidget() is tab.latest
    finally:
        tab.deleteLater()


def test_the_gallery_never_writes_to_the_folders_it_reads(gallery, tree):
    g, _ = gallery
    root = tree[0]
    before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    g.refresh()
    g.plants.setCurrentRow(0)
    after = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    assert before == after
