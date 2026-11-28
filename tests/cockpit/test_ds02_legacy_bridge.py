"""DS-02: a ponte do hardcode. Os ~430 estilos antigos seguem o tema escolhido enquanto a migração para tokens anda.

- a cor antiga vira o papel do tema conforme a PROPRIEDADE (a mesma cor é fundo num lugar e borda noutro);
- no tema padrão o texto volta idêntico ao original (nada muda na tela);
- widget criado ou reestilizado depois de aplicar o tema também segue o tema;
- cor desconhecida fica como está (a ponte não inventa papel).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.ui.theme import catalog as C  # noqa: E402
from agent_bar.cockpit.ui.theme import legacy as L  # noqa: E402
from agent_bar.cockpit.ui.theme import qt as Q  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
LIGHT = C.get("github-light")
OLD = "QFrame{background:#16161a;color:#8f8f98;border:1px solid #2a2a30}QPushButton{background:#2a2a30}"


def test_the_same_old_color_becomes_the_right_role_for_each_property():
    new = L.translate(OLD, LIGHT)
    assert "background:#ffffff" in new                                         # bg
    assert "color:#59636e" in new                                              # text_muted
    assert "border:1px solid #d0d7de" in new                                   # #2a2a30 como borda → border
    assert "QPushButton{background:#eaeef2}" in new                            # #2a2a30 como fundo → surface_alt


def test_the_default_theme_keeps_the_exact_original_text():
    assert L.translate(OLD, C.get(C.DEFAULT)) == OLD


def test_an_unknown_color_is_left_alone():
    assert L.translate("color:#123456", LIGHT) == "color:#123456"


def test_error_banners_keep_their_meaning_on_a_light_theme():
    new = L.translate("QFrame{background:#3a1717;border:1px solid #7a2a2a}", LIGHT)
    v = Q.values(LIGHT)
    assert v["danger_bg"] in new and v["danger_border"] in new


@pytest.fixture
def mgr():
    saved = {"key": C.DEFAULT}
    m = Q.ThemeManager(load_key=lambda: saved["key"], save_key=lambda k: saved.update(key=k), app=_APP)
    old = (_APP.palette(), _APP.styleSheet(), _APP.font())
    yield m
    m.apply(C.get(C.DEFAULT))
    m.close()
    _APP.setPalette(old[0])
    _APP.setStyleSheet(old[1])
    _APP.setFont(old[2])


def test_existing_new_and_restyled_widgets_follow_the_theme(mgr):
    before = QtWidgets.QLabel("antes")
    before.setStyleSheet("color:#8f8f98")
    try:
        mgr.apply(LIGHT)
        assert before.styleSheet() == "color:#59636e"                          # já existia
        after = QtWidgets.QLabel("depois")
        after.setStyleSheet("color:#e6e6e6;background:#1d1d22")                # criado depois do tema
        assert after.styleSheet() == f"color:{LIGHT.text};background:{LIGHT.surface}"
        before.show()
        _APP.processEvents()
        before.setStyleSheet("color:#ff9a9b")                                  # o código trocou o estilo (visível)
        assert before.styleSheet() == f"color:{LIGHT.danger}"
        hidden = QtWidgets.QLabel("aba não visitada")
        hidden.setStyleSheet("color:#8f8f98")
        hidden.setStyleSheet("color:#ffc233")                                  # trocado oculto: o Qt não avisa
        hidden.show()
        _APP.processEvents()
        assert hidden.styleSheet() == f"color:{LIGHT.warning}"                 # ao aparecer, segue o tema
        hidden.deleteLater()
        mgr.apply(C.get(C.DEFAULT))                                            # volta ao padrão: textos originais
        assert before.styleSheet() == "color:#ff9a9b"
        assert after.styleSheet() == "color:#e6e6e6;background:#1d1d22"
        after.deleteLater()
    finally:
        before.deleteLater()


def test_the_default_theme_restores_the_app_as_it_was(mgr):
    original = _APP.styleSheet()
    mgr.apply(LIGHT)
    assert _APP.styleSheet() != original
    mgr.apply(C.get(C.DEFAULT))
    assert _APP.styleSheet() == original
