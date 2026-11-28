"""DS-01: o tema aplicado ao Qt (QPalette, QSS, SVG, estilo por token) e o gerenciador salvo × prévia.

Também a catraca do hardcode: o número de cores soltas (#rrggbb) fora do pacote de tema só pode cair. Cada PR que
migra um widget para `themed()` baixa o número em BASELINE; nenhum PR pode subir.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QPalette  # noqa: E402

from agent_bar.cockpit.ui.theme import catalog as C  # noqa: E402
from agent_bar.cockpit.ui.theme import qt as Q  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
ROOT = Path(__file__).resolve().parents[2] / "agent_bar"
BASELINE = 430                                                                 # cores soltas em 2026-11-28 (main 76c8bdb + migração do Anexar)


@pytest.fixture
def mgr():
    saved = {"key": "p2s-escuro"}
    m = Q.ThemeManager(load_key=lambda: saved["key"], save_key=lambda k: saved.update(key=k), app=None)
    m.store = saved
    old_palette, old_sheet, old_font = _APP.palette(), _APP.styleSheet(), _APP.font()
    yield m
    _APP.setPalette(old_palette)
    _APP.setStyleSheet(old_sheet)
    _APP.setFont(old_font)


@pytest.mark.parametrize("theme", C.THEMES, ids=lambda t: t.key)
def test_every_theme_renders_a_complete_stylesheet_and_palette(theme):
    qss = Q.stylesheet(theme)
    assert "{" in qss and not re.search(r"@[a-z_]+", qss)                    # nenhum token sem valor
    for part in ("QPushButton", "QLineEdit", "QMenu", "QTreeWidget", "QHeaderView", "QTabBar", "QScrollBar", "QToolTip",
                 ":focus", ":disabled"):
        assert part in qss, part
    p = Q.palette(theme)
    assert p.color(QPalette.ColorRole.Window).name() == theme.bg
    assert p.color(QPalette.ColorRole.Text).name() == theme.text
    assert p.color(QPalette.ColorRole.Highlight).name() == theme.selection


def test_an_unknown_token_is_an_error_not_an_old_color():
    with pytest.raises(KeyError, match="token desconhecido no estilo: txet"):
        Q.render("color:@txet", C.get("graphite"))


def test_themed_widgets_follow_the_theme_and_previews_restore(mgr):
    label = QtWidgets.QLabel("x")
    try:
        mgr.themed(label, "color:@text_muted;background:@surface")
        assert C.get("p2s-escuro").text_muted in label.styleSheet()
        seen = []
        mgr.on_change(lambda t: seen.append(t.key))
        mgr.preview(C.get("github-light"))
        assert C.get("github-light").text_muted in label.styleSheet() and mgr.current.key == "github-light"
        assert mgr.store["key"] == "p2s-escuro"                                 # prévia não grava
        mgr.restore()
        assert mgr.current.key == "p2s-escuro" and C.get("p2s-escuro").text_muted in label.styleSheet()
        mgr.preview(C.get("graphite"))
        mgr.save()
        assert mgr.store["key"] == "graphite" and mgr.saved.key == "graphite"
        mgr.restore()                                                           # depois de salvar, restaurar = o salvo
        assert mgr.current.key == "graphite"
        assert seen == ["github-light", "p2s-escuro", "graphite"]
    finally:
        label.deleteLater()


def test_applying_a_theme_reaches_the_application(mgr):
    mgr.app = _APP
    mgr.apply(C.get("github-light"))
    assert _APP.palette().color(QPalette.ColorRole.Window).name() == "#ffffff"
    assert "#0969da" in _APP.styleSheet()


def test_a_saved_theme_that_no_longer_exists_falls_back_to_the_default():
    m = Q.ThemeManager(load_key=lambda: "tema-apagado", save_key=lambda k: None)
    assert m.saved.key == C.DEFAULT


def test_svg_icons_take_the_theme_color():
    svg = '<svg viewBox="0 0 16 16"><path fill="currentColor" d="M0 0h16v16H0z"/></svg>'
    assert 'fill="#e4e6e8"' in Q.tint_svg(svg, C.get("graphite"))
    assert 'fill="#f28b82"' in Q.tint_svg(svg, C.get("graphite"), "danger")


def test_hardcoded_colors_only_go_down():
    files = [p for p in ROOT.rglob("*.py") if "theme" not in p.relative_to(ROOT).parts]
    count = sum(len(re.findall(r"#[0-9a-fA-F]{6}\b", p.read_text(encoding="utf-8"))) for p in files)
    assert count <= BASELINE, (f"cores soltas subiram para {count} (catraca {BASELINE}): use os tokens "
                               "(theme.qt.manager().themed)")
