"""legacy.py — a PONTE do hardcode: os estilos antigos seguem o tema enquanto a migração para tokens não termina.

Hoje ~430 cores soltas (#rrggbb) estão espalhadas nos `setStyleSheet` das telas. Reescrever todas de uma vez seria um PR
enorme e arriscado. A ponte resolve a transição:
- guarda o estilo ORIGINAL de cada widget (propriedade `p2s_legacy_qss`);
- troca cada cor conhecida do visual antigo pelo papel equivalente do tema escolhido, olhando a PROPRIEDADE onde ela está
  (`background` → fundos, `color` → textos, `border` → bordas). A mesma cor pode ser fundo num lugar e borda noutro;
- no tema padrão (o visual atual) devolve o texto original idêntico: nada muda na tela;
- widget criado ou reestilizado depois é traduzido por um filtro de eventos no QApplication: na hora, se está
  visível (StyleChange); ao aparecer, se a troca foi com ele oculto (o Qt não avisa nesse caso).

Cor que não está nos mapas fica como está, para não inventar papel. A catraca do teste (test_ds01_qt) só deixa o
número de cores soltas cair. Quando chegar a zero, este módulo sai.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from agent_bar.cockpit.ui.theme import catalog as C
from agent_bar.cockpit.ui.theme.tokens import Theme

PROP = "p2s_legacy_qss"
KEY = "p2s_legacy_theme"                                                      # tema com que o widget foi traduzido
DECL = re.compile(r"([a-zA-Z-]+)(\s*:\s*)([^;{}]*)")
HEXC = re.compile(r"#[0-9a-fA-F]{6}\b")

# cor do visual antigo → papel (ou derivado de qt.values), por tipo de propriedade
BACKGROUND = {
    "#16161a": "bg", "#141417": "bg", "#121215": "bg", "#0e0e11": "bg",
    "#17171a": "surface", "#1a1a1f": "surface", "#1d1d22": "surface", "#1e1e23": "surface",
    "#2a2a30": "surface_alt", "#26262c": "surface_alt", "#202026": "surface_alt", "#22222a": "surface_alt",
    "#2c2c34": "surface_alt", "#34343c": "hover", "#24324a": "selection", "#2c3a55": "selection",
    "#3a1717": "danger_bg", "#3a1d1f": "danger_bg", "#2a2414": "warning_bg", "#3a2a10": "warning_bg",
}
FOREGROUND = {
    "#e6e6e6": "text", "#e6e6ea": "text", "#dcdce2": "text", "#d6d6dc": "text", "#f0f0f0": "text", "#ffffff": "text",
    "#cfcfd4": "text",
    "#8f8f98": "text_muted", "#7d7d88": "text_muted", "#6d6d75": "text_muted", "#9a9aa8": "text_muted",
    "#9a9aa3": "text_muted", "#bdbdc4": "text_muted", "#aaaaaa": "text_muted",
    "#ff9a9b": "danger", "#ff6b6d": "danger", "#ffb4b4": "danger", "#ffd0d0": "danger", "#ff6b6b": "danger",
    "#ffc233": "warning", "#ffb347": "warning", "#7ee2a8": "success", "#3ddc84": "success", "#4aa8ff": "accent",
}
BORDER = {
    "#2a2a30": "border", "#3a3a44": "border", "#2e2e36": "border", "#26262c": "border",
    "#7a2a2a": "danger_border", "#6b2a2e": "danger_border", "#7a5a20": "warning_border", "#6b5520": "warning_border",
}


def _table(prop: str) -> dict[str, str]:
    p = prop.lower()
    if p.startswith("border"):
        return BORDER
    if p in ("background", "background-color", "alternate-background-color"):
        return BACKGROUND
    if p == "selection-background-color":
        return {k: "selection" for k in BACKGROUND}
    return FOREGROUND                                                         # color, selection-color e afins


def translate(qss: str, theme: Theme) -> str:
    """O estilo antigo com as cores conhecidas trocadas pelos papéis do tema (tema padrão = texto idêntico)."""
    if theme.key == C.DEFAULT or not qss:
        return qss
    from agent_bar.cockpit.ui.theme.qt import values
    v = values(theme)

    def decl(m: re.Match) -> str:
        table = _table(m.group(1))
        value = HEXC.sub(lambda h: v.get(table.get(h.group(0).lower(), ""), h.group(0)), m.group(3))
        return m.group(1) + m.group(2) + value
    return DECL.sub(decl, qss)


class LegacyBridge(QObject):
    """Filtro de eventos: todo widget com estilo antigo segue o tema atual."""

    def __init__(self, theme_fn) -> None:
        super().__init__()
        self.theme_fn = theme_fn
        self._busy = False

    @staticmethod
    def compose(w: QWidget, original: str, theme: Theme) -> str:
        """O estilo final do widget: o antigo traduzido e, numa JANELA, o QSS global do tema na frente (aplicar o QSS
        por janela custa ~0,1 s; no QApplication repoliria até widget de aba oculta, ~0,8 s)."""
        own = translate(original, theme)
        if w.isWindow() and theme.key != C.DEFAULT:
            from agent_bar.cockpit.ui.theme.qt import stylesheet
            return stylesheet(theme) + ("\n" + own if own else "")
        return own

    def restyle(self, w: QWidget) -> None:
        original = w.property(PROP)
        if original is None:
            original = w.styleSheet()
            if not w.isWindow() and (not original or not HEXC.search(original)):
                return
            w.setProperty(PROP, original)
        theme = self.theme_fn()
        new = self.compose(w, str(original), theme)
        w.setProperty(KEY, theme.key)
        if w.styleSheet() != new:
            self._busy = True
            try:
                w.setStyleSheet(new)
            finally:
                self._busy = False

    def quiet(self):
        """Durante a troca do app inteiro (QSS/paleta global) os StyleChange não são do código: ignorar."""
        bridge = self

        class _Quiet:
            def __enter__(self):
                bridge._busy = True

            def __exit__(self, *exc):
                bridge._busy = False
        return _Quiet()

    def restyle_all(self) -> None:
        """Só o que está na tela agora: o resto (abas não visitadas, diálogos fechados) segue o tema ao aparecer."""
        for w in QApplication.allWidgets():
            if w.isVisible():
                self.restyle(w)

    def eventFilter(self, obj, event) -> bool:                                 # noqa: N802 - API do Qt
        # StyleChange: estilo trocado num widget visível (ou o 1º estilo). Show: o Qt não avisa a troca num widget
        # oculto (aba não visitada), então ele é conferido ao aparecer.
        if not self._busy and event.type() in (QEvent.Type.StyleChange, QEvent.Type.Show) and isinstance(obj, QWidget):
            current = obj.styleSheet()
            original = obj.property(PROP)
            if original is not None:
                last = obj.property(KEY)                                     # o que a ponte deixou nele
                expected = self.compose(obj, str(original), C.get(str(last))) if last else str(original)
                if current != expected:
                    obj.setProperty(PROP, current)                           # o código trocou o estilo: novo original
            self.restyle(obj)
        return False


__all__ = ["BACKGROUND", "BORDER", "FOREGROUND", "LegacyBridge", "translate"]
