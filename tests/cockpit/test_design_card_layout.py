"""Cartões REFERÊNCIA / ÚLTIMO RENDER / MELHOR RENDER: a legenda nunca é desenhada por cima da imagem.

Visto pelo Felipe no Windows (~1680×1050): a legenda de várias linhas cruzava a parte de baixo da miniatura, porque o
QLabel com quebra informava ao layout o mínimo de uma linha só. Vale para os cartões atuais (a tela muda no UX-14).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit import evidence as E  # noqa: E402
from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit.ui.design_view import DesignView  # noqa: E402
from tests.cockpit.gallery_fixture import PLANT, ROOM, build_all  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
# a legenda real da referência da Suíte 01 (6 linhas) e a do último render com nota visual (5 linhas)
REF_LINES = ["SUÍTE 01, convergência visual r2: render com prancha lateral (gerado pelo ChatGPT)",
             "ref-benchmark-suite01-convergence-r2@v2", "Referência estética: não é geometria validada.",
             "sintética (GPT_GENERATED_ON_REQUEST); uso conceptual-only", "curadoria CANDIDATE"]
LAST_LINES = ["23/11 09:11 UTC · 20261123-planta_74_suite_01-rc5-render", "câmera r2_canto · luz entardecer", "publicado",
              "Geometria: PASS · Render: PASS",
              "Nota visual: qwen2.5vl:7b, confiança baixa (câmera r2_canto · luz entardecer): Fidelidade 4/5 · Cama 5/5 · "
              "Materiais 4/5 · Iluminação 4/5 · Mobiliário 4/5"]


@pytest.fixture
def view(tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent
    ev, engine, dl = build_all(tmp_path)
    g = G.build_gallery(E.load_catalog(ev, expected={}), engine, dl, rooms=[(PLANT, ROOM)])
    d = DesignView()
    d.set_items(g.of(PLANT, ROOM))
    yield d
    d.shutdown()
    d.close()
    d.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("w,h", [(1280, 800), (1680, 1050), (1920, 1080)])
def test_the_caption_rectangle_never_crosses_the_image_rectangle(view, w, h):
    ref, last = view.cards["referência"], view.cards["último"]
    ref.fill(ref.item, REF_LINES)
    last.fill(last.item, LAST_LINES)
    view.resize(w, h - 140)                                                        # abas e cabeçalho do Cockpit acima
    view.show()
    for _ in range(15):
        _APP.processEvents()
    for name, card in view.cards.items():
        if not card.isVisible():                                                  # UX-14: melhor só com destaque merecido
            continue
        image, caption = card.thumb.geometry(), card.info.geometry()
        assert not image.intersects(caption), (name, w, h, image, caption)
        assert caption.top() > image.bottom(), (name, w, h)
        assert card.info.height() >= card.info.heightForWidth(card.info.width()), (name, w, h)   # nenhuma linha cortada
        assert card.enlarge_btn.geometry().top() > caption.bottom(), (name, w, h)


@pytest.mark.parametrize("w,h", [(1280, 800), (1680, 1050), (1920, 1080)])
def test_the_thumbnail_area_is_at_least_240x135_and_the_image_fills_it_keeping_its_proportion(view, w, h):
    """#102 SHOULD: a ÁREA da miniatura nunca fica abaixo de 240×135; a imagem cabe nela sem distorcer, encostando em
    pelo menos um dos lados (um render 16:10 fica 216×135 numa área 240×135: proporção, não encolhimento)."""
    from agent_bar.cockpit.ui.design_view import CARD_THUMB
    assert (CARD_THUMB.width(), CARD_THUMB.height()) == (240, 135)
    view.resize(w, h - 140)
    view.show()
    for _ in range(15):
        _APP.processEvents()
    for name, card in view.cards.items():
        if not card.isVisible():                                                  # UX-14: melhor só com destaque merecido
            continue
        area = card.thumb.size()
        assert area.width() >= 240 and area.height() >= 135, (name, w, h, area)
        pix = card.thumb.pixmap()
        if pix is None or pix.isNull():
            continue
        src = card.thumb._pix
        assert abs(pix.width() / pix.height() - src.width() / src.height()) < 0.02, (name, "distorceu")
        assert pix.width() <= area.width() and pix.height() <= area.height()
        assert pix.width() >= area.width() - 2 or pix.height() >= area.height() - 2, (name, w, h, pix.size(), area)
