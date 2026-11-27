"""UX-16 run 6 no Windows real: na aba Design, a linha "Anexar esta seleção ao próximo prompt do Chat" ficava cortada
pela borda de baixo, rente ao rodapé de fontes, e a página também transbordava para a direita. Causa: o breadcrumb da
seleção (planta ▸ cômodo ▸ câmera ▸ run) é um QLabel sem quebra, então o texto inteiro vira a largura MÍNIMA da página,
somado ao checkbox na mesma linha. Critério: nas larguras 900 (mínima suportada) e 1100 a página cabe sem barra
horizontal, e o controle, levado à vista, fica inteiro no viewport e sem interseção com o rodapé.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from agent_bar.cockpit import gallery as G  # noqa: E402
from agent_bar.cockpit import window_state as WS  # noqa: E402

_APP = QApplication.instance() or QApplication([])
STUB = Path(__file__).with_name("fake_acp_agent.py")
PLANT, ROOM = "planta_74", "suite_01"
FONT_FILES = (r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")


@pytest.fixture
def real_font():
    """Offscreen no Windows não tem NENHUMA fonte (cada glifo vira uma caixa de 11 px): largura medida ali é ficção.
    Carrega uma fonte de sistema de verdade (a do Windows; DejaVu/Liberation no CI) e a devolve ao fim."""
    from PySide6.QtGui import QFont, QFontDatabase
    for path in FONT_FILES:
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                break
    else:
        pytest.skip("nenhuma fonte de sistema conhecida para medir largura de texto")
    old = _APP.font()
    _APP.setFont(QFont(QFontDatabase.applicationFontFamilies(fid)[0], 9))
    try:
        yield
    finally:
        _APP.setFont(old)
        QFontDatabase.removeApplicationFont(fid)


def png(tmp_path: Path, name: str) -> Path:
    p = tmp_path / f"{name}.png"
    img = QImage(32, 18, QImage.Format.Format_RGB32)
    img.fill(0x223344)
    img.save(str(p))
    return p


def render(tmp_path, room: str = ROOM) -> G.GalleryItem:
    run = f"20261123-planta_74_{room}-rc7b-render"                               # o formato do run real da captura
    key = "render_entardecer_r2_canto"
    img = G.GalleryImage(key, png(tmp_path, f"{run}-{key}"), "0" * 64, "r", f"views/{key}.png")
    return G.GalleryItem(G.PUBLISHED, PLANT, room, run, "render", started_at="2026-11-23T15:09:00Z", day="2026-11-23",
                         render_verdict="PASS", build={"geometria": "PASS", "construção": "PASS"}, images=[img])


LONG_ROOM = "sala_de_estar_e_jantar_integrada_com_varanda_gourmet"               # cômodo de nome comprido


@pytest.fixture(params=[ROOM, LONG_ROOM])
def win(request, real_font, tmp_path, monkeypatch):
    from agent_bar.cockpit.profiles import AgentProfile
    from agent_bar.cockpit.ui.window import CockpitWindow
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    for var in ("AGENT_BAR_EVIDENCE_DIR", "AGENT_BAR_ENGINE_DIR", "AGENT_BAR_DESIGN_LIBRARY_DIR"):
        (tmp_path / var).mkdir()                                                   # nada da máquina entra no teste
        monkeypatch.setenv(var, str(tmp_path / var))
    (tmp_path / "ws").mkdir()
    w = CockpitWindow(repos=["o/r"], window_store=WS.WindowStateStore(tmp_path / "window.json"),
                      profiles_fn=lambda: [AgentProfile("stub", "Stub", (sys.executable, str(STUB)))],
                      workspaces_fn=lambda: [tmp_path / "ws"])
    # A primeira visita à aba Design relê o evidence em outra thread e TROCA os itens da galeria: no CI (#124 @ 44aa356)
    # isso chegava antes da conferência ("nenhum render selecionado"); aqui, com o evidence real da máquina, trazia o
    # run rc7b-render e passava por coincidência. Este teste mede LAYOUT com itens dele: a leitura fica de fora.
    w.review.reload = lambda: None
    w.review.design.set_items([render(tmp_path, request.param)])
    w.show()
    yield w
    from PySide6.QtCore import QCoreApplication, QEvent
    w.close()
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def pump(seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


def in_window(w, widget) -> QRect:
    return QRect(widget.mapTo(w, QPoint(0, 0)), widget.size())


@pytest.mark.parametrize("width", [WS.MIN_W, 1100])
def test_the_design_page_fits_and_the_attach_control_clears_the_sources_footer(win, width):
    win.resize(width, 640)
    win.tabs.setCurrentWidget(win.review)
    d = win.review.design
    d.attach_box.setChecked(True)                                                  # com o preview aberto
    pump(1.0)
    page = win.review
    scroll = page.parentWidget().parentWidget()
    assert isinstance(scroll, QScrollArea)
    assert d.breadcrumb.text().endswith("rc7b-render")
    assert page.minimumSizeHint().width() <= scroll.viewport().width(), (
        f"a página pede {page.minimumSizeHint().width()} px e a janela dá {scroll.viewport().width()} px")
    assert not scroll.horizontalScrollBar().isVisible()
    scroll.ensureWidgetVisible(d.attach_box)
    pump()
    box, view, footer = in_window(win, d.attach_box), in_window(win, scroll.viewport()), in_window(win, win.statusBar())
    assert view.contains(box), f"controle {box.getRect()} fora do viewport {view.getRect()}"
    assert not box.intersects(footer), f"controle {box.getRect()} cruza o rodapé {footer.getRect()}"
