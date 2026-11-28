"""Trocar o tema depois que um widget estilizado foi destruído não pode levantar.

Achado em 2026-11-28 (F1): `ThemeManager.apply` reestiliza tudo o que passou por `style()` e chama os `on_change`.
Um widget cujo objeto C++ já foi apagado (diálogo com WA_DeleteOnClose fechado, janela destruída) mas cujo wrapper
Python continua vivo fazia o `apply` levantar `RuntimeError: Internal C++ object ... already deleted` — no app, ao
trocar o tema depois de fechar a Visão geral; na suíte, ao abrir uma segunda janela com tema salvo.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from agent_bar.cockpit.ui.theme import catalog as C  # noqa: E402
from agent_bar.cockpit.ui.theme import qt as Q  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w) -> None:
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_applying_a_theme_skips_a_destroyed_styled_widget_and_forgets_it():
    mgr = Q.ThemeManager(load_key=lambda: C.DEFAULT, save_key=lambda _k: None, app=_APP)
    try:
        keep, dead = QLabel("vivo"), QLabel("morto")
        mgr.themed(keep, "QLabel{color:@accent}")
        mgr.themed(dead, "QLabel{color:@accent}")
        _destroy(dead)                                                         # o wrapper Python segue em `dead`
        other = next(t for t in C.THEMES if t.key != mgr.current.key)
        mgr.apply(other)                                                       # antes: RuntimeError
        assert other.accent in keep.styleSheet()
        assert [ref() for ref, _t in mgr._themed] == [keep]
        keep.deleteLater()
    finally:
        mgr.close()


def test_a_listener_whose_widget_was_destroyed_is_dropped_not_raised():
    mgr = Q.ThemeManager(load_key=lambda: C.DEFAULT, save_key=lambda _k: None, app=_APP)
    try:
        label = QLabel("x")
        calls = []
        mgr.on_change(lambda t, w=label: w.setText(t.key))                      # como o botão Temas troca o ícone
        mgr.on_change(lambda t: calls.append(t.key))
        _destroy(label)
        other = next(t for t in C.THEMES if t.key != mgr.current.key)
        mgr.apply(other)                                                       # antes: RuntimeError
        assert calls == [other.key] and len(mgr._listeners) == 1
    finally:
        mgr.close()
