"""qt.py — aplica um `Theme` ao Qt: QPalette, QSS global, SVG tematizável e estilos por widget com tokens.

- `palette(theme)` e `stylesheet(theme)`: o tema inteiro do app (botões, campos, menus, tabelas, abas, scrollbars,
  tooltips, foco e desabilitado), gerado dos tokens, sem cor solta.
- `themed(widget, template)`: o caminho para TIRAR o hardcode aos poucos. O widget declara o estilo com papéis
  (`"color:@text_muted"`), e a troca de tema reaplica. `@nome` aceita só papéis de cor e os derivados de `values()`;
  o resto é recusado. (`@` porque o QSS já usa chaves.)
- `tint_svg(svg, theme, role)`: ícone SVG com `currentColor` pintado com um papel do tema.
- `ThemeManager`: tema salvo × tema na tela. `preview` aplica sem gravar; `restore` volta ao salvo; `save` grava. Nada
  disso reinicia o app nem mexe em agentes: só reestiliza widgets.
"""
from __future__ import annotations

import re
import weakref
from collections.abc import Callable

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from agent_bar.cockpit.ui.theme import catalog as C
from agent_bar.cockpit.ui.theme.tokens import COLOR_ROLES, Theme, mix

TOKEN = re.compile(r"@([a-z_]+)")


def values(theme: Theme) -> dict[str, str]:
    """Os papéis do tema e os derivados (hover, pressionado, desabilitado, fundos e bordas de aviso/erro)."""
    v = {role: theme.color(role) for role in COLOR_ROLES}
    v.update(radius=f"{theme.radius}px", space=f"{theme.spacing()}px", font_size=f"{theme.font_size}pt",
             font_family=theme.font_family, hover=mix(theme.surface_alt, theme.accent, 0.12),
             pressed=mix(theme.surface_alt, theme.accent, 0.25), disabled_text=mix(theme.text_muted, theme.bg, 0.45),
             danger_bg=mix(theme.bg, theme.danger, 0.15), danger_border=mix(theme.bg, theme.danger, 0.45),
             warning_bg=mix(theme.bg, theme.warning, 0.15), warning_border=mix(theme.bg, theme.warning, 0.45))
    return v


def render(template: str, theme: Theme) -> str:
    """Troca `@papel` pelo valor do tema. Nome desconhecido = KeyError (erro de digitação não vira cor velha)."""
    v = values(theme)
    unknown = sorted({m for m in TOKEN.findall(template) if m not in v})
    if unknown:
        raise KeyError(f"token desconhecido no estilo: {', '.join(unknown)}")
    return TOKEN.sub(lambda m: v[m.group(1)], template)


def palette(theme: Theme) -> QPalette:
    p = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    pairs = {R.Window: theme.bg, R.WindowText: theme.text, R.Base: theme.surface, R.AlternateBase: theme.surface_alt,
             R.Text: theme.text, R.Button: theme.surface_alt, R.ButtonText: theme.text, R.Highlight: theme.selection,
             R.HighlightedText: theme.text, R.ToolTipBase: theme.surface_alt, R.ToolTipText: theme.text,
             R.PlaceholderText: theme.text_muted, R.Link: theme.accent, R.BrightText: theme.danger}
    for role, color in pairs.items():
        p.setColor(role, QColor(color))
    off = values(theme)["disabled_text"]
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, QColor(off))
    return p


QSS = """
QWidget { background:@bg; color:@text; font-family:"@font_family"; font-size:@font_size; }
QToolTip { background:@surface_alt; color:@text; border:1px solid @border; padding:4px; }
QPushButton { background:@surface_alt; color:@text; border:1px solid @border; border-radius:@radius;
              padding:@space 12px; }
QPushButton:hover { background:@hover; }
QPushButton:pressed { background:@pressed; }
QPushButton:default { background:@accent; color:@accent_text; border-color:@accent; }
QPushButton:disabled, QComboBox:disabled, QLineEdit:disabled { color:@disabled_text; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QCheckBox:focus,
QTreeWidget:focus, QListWidget:focus, QTabBar::tab:focus { border:2px solid @focus; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox { background:@surface; color:@text;
              border:1px solid @border; border-radius:@radius; padding:2px 6px; selection-background-color:@selection; }
QComboBox QAbstractItemView, QMenu { background:@surface; color:@text; border:1px solid @border;
              selection-background-color:@selection; selection-color:@text; }
QMenu::item:selected { background:@selection; }
QTreeWidget, QTreeView, QListWidget, QTableView { background:@surface; color:@text; border:1px solid @border;
              alternate-background-color:@surface_alt; }
QTreeWidget::item:selected, QListWidget::item:selected, QTableView::item:selected { background:@selection; color:@text; }
QHeaderView::section { background:@surface_alt; color:@text_muted; border:0; padding:3px 6px; }
QTabWidget::pane { border:1px solid @border; }
QTabBar::tab { background:@surface; color:@text_muted; padding:6px 12px; border:1px solid @border;
              border-bottom:0; border-top-left-radius:@radius; border-top-right-radius:@radius; }
QTabBar::tab:selected { background:@bg; color:@text; }
QScrollBar:vertical, QScrollBar:horizontal { background:@bg; border:0; width:12px; height:12px; }
QScrollBar::handle { background:@border; border-radius:5px; min-height:24px; min-width:24px; }
QScrollBar::handle:hover { background:@text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QCheckBox, QRadioButton, QLabel { background:transparent; }
QStatusBar { background:@surface; color:@text_muted; border-top:1px solid @border; }
"""


def stylesheet(theme: Theme) -> str:
    return render(QSS, theme)


def tint_svg(svg: str, theme: Theme, role: str = "text") -> str:
    """Ícone tematizável: troca `currentColor` pela cor do papel. O SVG não ganha script nem link: é só texto de cor."""
    return svg.replace("currentColor", theme.color(role))


# ------------------------------------------------------------------------------------------------ gerenciador
class ThemeManager:
    """Tema SALVO (persistido) × tema na TELA (pode ser uma prévia). Ouvintes recebem o tema a cada troca."""

    def __init__(self, load_key: Callable[[], str] | None = None, save_key: Callable[[str], None] | None = None,
                 app: QApplication | None = None) -> None:
        self._load_key = load_key or _settings_load
        self._save_key = save_key or _settings_save
        self.app = app
        self.saved = _safe_get(self._load_key())
        self.current = self.saved
        self._listeners: list[Callable[[Theme], None]] = []
        self._themed: list[tuple[weakref.ref, str]] = []

    # aplicar
    def apply(self, theme: Theme) -> None:
        self.current = theme
        app = self.app or QApplication.instance()
        if app is not None:
            app.setPalette(palette(theme))
            app.setFont(QFont(theme.font_family, theme.font_size))
            app.setStyleSheet(stylesheet(theme))
        alive = []
        for ref, template in self._themed:
            w = ref()
            if w is not None:
                w.setStyleSheet(render(template, theme))
                alive.append((ref, template))
        self._themed = alive
        for fn in list(self._listeners):
            fn(theme)

    def themed(self, widget: QWidget, template: str) -> QWidget:
        widget.setStyleSheet(render(template, self.current))
        self._themed.append((weakref.ref(widget), template))
        return widget

    def on_change(self, fn: Callable[[Theme], None]) -> None:
        self._listeners.append(fn)

    # prévia × salvo (§5.1)
    def preview(self, theme: Theme) -> None:
        if theme != self.current:
            self.apply(theme)

    def restore(self) -> None:
        if self.current != self.saved:
            self.apply(self.saved)

    def save(self, theme: Theme | None = None) -> None:
        theme = theme or self.current
        self._save_key(theme.key)
        self.saved = theme
        if self.current != theme:
            self.apply(theme)


def _safe_get(key: str) -> Theme:
    try:
        return C.get(key)
    except KeyError:
        return C.get(C.DEFAULT)                                                 # tema salvo que sumiu: o padrão


def _settings_load() -> str:
    from agent_bar import settings as S
    return S.load().theme


def _settings_save(key: str) -> None:
    from agent_bar import settings as S
    s = S.load()
    s.theme = key
    S.save(s)


_MANAGER: ThemeManager | None = None


def manager() -> ThemeManager:
    """O gerenciador do processo (um por app)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ThemeManager()
    return _MANAGER


def style(widget: QWidget, template: str) -> QWidget:
    """Atalho: `style(btn, "color:@text_muted")` = `manager().themed(btn, ...)`."""
    return manager().themed(widget, template)


__all__ = ["QSS", "ThemeManager", "manager", "palette", "render", "style", "stylesheet", "tint_svg", "values"]
