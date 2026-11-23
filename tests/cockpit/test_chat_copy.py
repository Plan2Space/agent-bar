"""UX-10 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): mensagens do Chat copiáveis.

Seleção com o mouse, Ctrl+C, menu Copiar e botão Copiar por mensagem, preservando quebras de linha, crases e código;
Markdown renderizado com segurança (HTML do agente nunca é interpretado); mensagem de 20 000 caracteres não trava a UI.
PySide6 real (offscreen).
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from agent_bar.cockpit.ui import widgets as W  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

MESSAGE = ("Resultado do build:\n\n"
           "```python\n"
           "def build_room(fixture):\n"
           "    return run(fixture)   # `crases` dentro do código\n"
           "```\n\n"
           "Linha 1\nLinha 2 com **negrito** e `codigo inline`.")


def block(role="agent", text=MESSAGE) -> W.MessageBlock:
    b = W.MessageBlock(role, text)
    b.resize(700, 300)
    b.show()
    _APP.processEvents()
    return b


def dispose(b) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    b.close()
    b.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def clipboard() -> str:
    return QtWidgets.QApplication.clipboard().text()


def test_the_copy_button_copies_the_exact_message_with_line_breaks_backticks_and_code():
    b = block()
    try:
        QtWidgets.QApplication.clipboard().setText("")
        b.copy_btn.click()
        assert clipboard() == MESSAGE                                             # byte a byte, incluindo ``` e \n
        assert b.copy_btn.text() == "Copiado ✓"
    finally:
        dispose(b)


def test_markdown_is_rendered_but_the_copy_keeps_the_source():
    b = block()
    try:
        shown = b.view.toPlainText()
        assert "negrito" in shown and "**negrito**" not in shown                 # formatado na tela
        assert "def build_room(fixture):" in shown and "    return run(fixture)" in shown   # código com recuo
        assert b.full_text() == MESSAGE
    finally:
        dispose(b)


def test_html_written_by_the_agent_is_shown_as_text_never_interpreted():
    evil = 'Veja <img src="x" onerror="alert(1)"> e <b>isto</b> <script>alert(2)</script>'
    b = block(text=evil)
    try:
        shown = b.view.toPlainText()
        assert '<img src="x" onerror="alert(1)">' in shown and "<script>alert(2)</script>" in shown
        assert "<b>isto</b>" in shown                                              # nem o negrito em HTML vira formatação
        assert b.view.openLinks() is False and b.view.openExternalLinks() is False
    finally:
        dispose(b)


def test_mouse_selection_and_ctrl_c_copy_what_is_selected():
    b = block()
    try:
        QtWidgets.QApplication.clipboard().setText("")
        b.view.setFocus()
        b.view.selectAll()
        QTest.keyClick(b.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        got = clipboard()
        assert "Linha 1" in got and "Linha 2" in got and "def build_room(fixture):" in got
        assert "\n" in got                                                         # quebras preservadas
        assert b.view.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    finally:
        dispose(b)


def test_the_context_menu_offers_copy_selection_copy_message_and_select_all():
    b = block()
    try:
        m = b.context_menu()
        texts = [a.text() for a in m.actions() if a.text()]
        assert texts == ["Copiar seleção", "Copiar mensagem inteira", "Selecionar tudo"]
        assert not m.actions()[0].isEnabled()                                     # sem seleção, nada a copiar
        b.view.selectAll()
        assert b.context_menu().actions()[0].isEnabled()
        QtWidgets.QApplication.clipboard().setText("")
        next(a for a in b.context_menu().actions() if a.text() == "Copiar mensagem inteira").trigger()
        assert clipboard() == MESSAGE
    finally:
        dispose(b)


def test_user_messages_have_the_same_copy_and_selection():
    b = W.UserBubble("minha pergunta\ncom duas linhas")
    try:
        b.show()
        _APP.processEvents()
        QtWidgets.QApplication.clipboard().setText("")
        b.copy_btn.click()
        assert clipboard() == "minha pergunta\ncom duas linhas" and b.who.text() == "Você"
    finally:
        dispose(b)


def test_a_20000_character_message_streams_without_freezing_and_copies_whole():
    long = "".join(f"linha {i:05d} com algum texto do agente\n" for i in range(600))    # ~22 000 caracteres
    b = W.MessageBlock("agent")
    b.resize(700, 300)
    b.show()
    try:
        t0 = time.perf_counter()
        for i in range(0, len(long), 500):                                          # 45 lotes de streaming
            b.append(long[i:i + 500])
            _APP.processEvents()
        elapsed = time.perf_counter() - t0
        assert len(long) > W.MARKDOWN_LIMIT and elapsed < 3.0, elapsed
        assert b.view.toPlainText().startswith("linha 00000") and "linha 00599" in b.view.toPlainText()
        QtWidgets.QApplication.clipboard().setText("")
        b.copy_btn.click()
        assert clipboard() == long
    finally:
        dispose(b)


def test_a_long_message_wraps_inside_the_window_without_horizontal_scroll():
    b = block(text="palavra " * 400)
    try:
        assert b.view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert b.view.document().size().width() <= b.view.viewport().width() + 1
        assert b.view.height() > 60                                                 # cresceu em altura, não em largura
    finally:
        dispose(b)


def test_code_blocks_get_their_own_background_and_long_code_lines_wrap_instead_of_being_cut():
    from PySide6.QtGui import QTextFormat
    long_line = "x = " + " + ".join(f"valor_{i}" for i in range(80))
    b = block(text=f"antes\n\n```python\n{long_line}\n```\n\ndepois")
    try:
        doc = b.view.document()
        code = [doc.findBlockByNumber(i) for i in range(doc.blockCount())
                if doc.findBlockByNumber(i).blockFormat().hasProperty(QTextFormat.Property.BlockCodeFence)]
        assert code and all(c.blockFormat().background().color().name() == W.CODE_BG for c in code)
        assert all(not c.blockFormat().nonBreakableLines() for c in code)
        assert doc.size().width() <= b.view.viewport().width() + 1                 # a linha longa quebra, não some
        assert long_line in b.full_text() and "valor_79" in b.view.toPlainText()
    finally:
        dispose(b)


def test_a_message_never_stretches_beyond_its_text_in_a_tall_transcript():
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    host = QWidget()
    lay = QVBoxLayout(host)
    sa.setWidget(host)
    b = W.UserBubble("uma linha só")
    lay.addWidget(b)                                                               # sem stretch: sobra altura
    sa.resize(900, 900)
    sa.show()
    try:
        for _ in range(10):
            _APP.processEvents()
        assert b.height() <= b.view.height() + 40                                  # cabeçalho + margens, nada de vão
    finally:
        dispose(sa)


def test_the_view_height_matches_the_text_after_the_window_grows():
    b = W.UserBubble(MESSAGE)
    b.resize(300, 200)                                                             # nasce estreito (texto alto)
    b.show()
    try:
        for _ in range(5):
            _APP.processEvents()
        b.resize(1100, 200)                                                        # a janela cresce
        for _ in range(10):
            _APP.processEvents()
        doc_h = b.view.document().size().height()
        assert abs(b.view.height() - (doc_h + 6)) <= 1, (b.view.height(), doc_h)    # sem vão sobrando
    finally:
        dispose(b)
