"""theme_picker.py — o Theme Engine (P2S-STUDIO-THEME-ENGINE-001; pedido do Felipe de 2026-11-27, §5 e §5.1).

Botão **Temas** no canto superior direito (ícone de paleta com olho), modal com todos os temas:
- **passar o mouse** sobre um tema aplica a PRÉVIA no app inteiro, sem gravar; **tirar o mouse** da lista volta ao tema
  escolhido no modal (o clicado, ou o salvo se nada foi clicado);
- **clicar** (ou setas ↑/↓ no teclado) escolhe e mantém a prévia;
- **Salvar** (ou Enter) grava; **Cancelar**, fechar o modal ou **Esc** restauram o tema salvo;
- busca por nome/categoria, filtro por categoria e favoritos (gravados no settings.json).
A prévia por hover espera um instante (`HOVER_MS`) para não reestilizar o app a cada item cruzado com o mouse. Trocar o
tema só reestiliza widgets: não reinicia o app, não para agentes nem muda a página aberta.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit.ui.theme import catalog as C
from agent_bar.cockpit.ui.theme import qt as Q
from agent_bar.cockpit.ui.theme.tokens import Theme

HOVER_MS = 120
ALL = "Todas as categorias"
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.9 1.4-2l-.4-1a1.6 1.6 0 0 1 1.5-2.2H17a4 4 0 0 0 4-4C21 6.9 17 3 12 3z"/>'
            '<circle cx="7.5" cy="11" r="1.1"/><circle cx="10" cy="7" r="1.1"/><circle cx="15" cy="7" r="1.1"/>'
            '<path d="M14.5 17.2c1.2-1.5 2.7-2.2 4-2.2s2.8.7 4 2.2c-1.2 1.5-2.7 2.2-4 2.2s-2.8-.7-4-2.2z"/>'
            '<circle cx="18.5" cy="17.2" r=".9"/></svg>')


def themes_icon(theme: Theme, size: int = 20) -> QIcon:
    pm = QPixmap()
    pm.loadFromData(Q.tint_svg(ICON_SVG, theme, "text").encode("utf-8"), "SVG")
    return QIcon(pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


def swatch(theme: Theme, w: int = 56, h: int = 28) -> QIcon:
    """Amostra do tema: fundo, superfície, texto e destaque (a cor não é a única pista: o nome vem ao lado)."""
    pm = QPixmap(w, h)
    pm.fill(QColor(theme.bg))
    p = QPainter(pm)
    p.fillRect(4, 4, w - 8, h - 8, QColor(theme.surface))
    p.fillRect(8, 8, 18, 4, QColor(theme.text))
    p.fillRect(8, 15, 12, 4, QColor(theme.text_muted))
    p.fillRect(w - 22, 8, 14, 12, QColor(theme.accent))
    p.setPen(QColor(theme.border))
    p.drawRect(0, 0, w - 1, h - 1)
    p.end()
    return QIcon(pm)


class ThemePicker(QDialog):
    def __init__(self, manager: Q.ThemeManager, parent: QWidget | None = None,
                 favorites_load: Callable[[], list[str]] | None = None,
                 favorites_save: Callable[[list[str]], None] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Temas")
        self.setModal(True)
        self.resize(520, 560)
        self.mgr = manager
        self.chosen: Theme = manager.saved                                     # o que fica quando o mouse sai
        self.saved_on_open: Theme = manager.saved
        self._fav_load = favorites_load or _favorites_load
        self._fav_save = favorites_save or _favorites_save
        self.favorites: list[str] = list(self._fav_load())
        self._pending: Theme | None = None
        self._done = False
        lay = QVBoxLayout(self)
        intro = QLabel("Passe o mouse para ver o tema no app inteiro. Clique para escolher; Salvar grava, Esc cancela.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Pesquisar tema…")
        self.search.setAccessibleName("Pesquisar tema")
        self.category = QComboBox()
        self.category.addItem(ALL, "")
        for c in C.CATEGORIES:
            self.category.addItem(c, c)
        self.only_fav = QCheckBox("Só favoritos")
        row.addWidget(self.search, 2)
        row.addWidget(self.category, 1)
        row.addWidget(self.only_fav)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setIconSize(QSize(56, 28))
        self.list.setAccessibleName("Temas disponíveis")
        self.list.viewport().installEventFilter(self)
        lay.addWidget(self.list, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        bottom = QHBoxLayout()
        self.fav_btn = QPushButton("Favoritar")
        self.save_btn = QPushButton("Salvar")
        self.cancel_btn = QPushButton("Cancelar")
        self.save_btn.setDefault(True)
        bottom.addWidget(self.fav_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.save_btn)
        lay.addLayout(bottom)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(HOVER_MS)
        self.hover_timer.timeout.connect(self._apply_pending)
        self.list.itemEntered.connect(self._hovered)
        self.list.currentItemChanged.connect(lambda cur, _prev: self._picked(cur))
        self.search.textChanged.connect(lambda _t: self.refill())
        self.category.currentIndexChanged.connect(lambda _i: self.refill())
        self.only_fav.toggled.connect(lambda _on: self.refill())
        self.fav_btn.clicked.connect(self.toggle_favorite)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.reject)
        self.refill()

    # ------------------------------------------------------------------------------------------------ lista
    def refill(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        themes = C.find(self.search.text(), str(self.category.currentData() or ""))
        if self.only_fav.isChecked():
            themes = [t for t in themes if t.key in self.favorites]
        for t in themes:
            star = "Favorito · " if t.key in self.favorites else ""   # palavra: o ★ some na fonte
            mark = "  (salvo)" if t.key == self.mgr.saved.key else ""
            it = QListWidgetItem(swatch(t), f"{star}{t.name}{mark}\n{t.category}" + (" · escuro" if t.dark else " · claro"))
            it.setData(Qt.ItemDataRole.UserRole, t.key)
            it.setToolTip(f"{t.name} · {t.category} · fonte: {t.source}")
            self.list.addItem(it)
            if t.key == self.chosen.key:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        self.status.setText(f"{len(themes)} tema(s)" if themes else "Nenhum tema com esse filtro.")
        self._show_fav_state()

    def theme_of(self, item: QListWidgetItem | None) -> Theme | None:
        return C.get(str(item.data(Qt.ItemDataRole.UserRole))) if item is not None else None

    # ------------------------------------------------------------------------------------------------ prévia
    def _hovered(self, item: QListWidgetItem) -> None:
        self._pending = self.theme_of(item)
        self.hover_timer.start()

    def _apply_pending(self) -> None:
        if self._pending is not None and not self._done:
            self.mgr.preview(self._pending)
            self.status.setText(f"Prévia: {self._pending.name} (não salvo)")

    def _left_list(self) -> None:
        self.hover_timer.stop()
        self._pending = None
        if not self._done:
            self.mgr.preview(self.chosen)
            self.status.setText(f"Escolhido: {self.chosen.name}" + (" (salvo)" if self.chosen == self.mgr.saved else ""))

    def _picked(self, item: QListWidgetItem | None) -> None:
        theme = self.theme_of(item)
        if theme is None:
            return
        self.chosen = theme
        self.hover_timer.stop()
        self.mgr.preview(theme)
        self.status.setText(f"Escolhido: {theme.name} — Salvar para manter")
        self._show_fav_state()

    def eventFilter(self, obj, event) -> bool:                                 # noqa: N802 - API do Qt
        if obj is self.list.viewport() and event.type() == QEvent.Type.Leave:
            self._left_list()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------------------------------------ favoritos
    def toggle_favorite(self) -> None:
        key = self.chosen.key
        self.favorites = [k for k in self.favorites if k != key] if key in self.favorites else [*self.favorites, key]
        self._fav_save(self.favorites)
        self.refill()

    def _show_fav_state(self) -> None:
        self.fav_btn.setText("Tirar dos favoritos" if self.chosen.key in self.favorites else "Favoritar")

    # ------------------------------------------------------------------------------------------------ fechar
    def save(self) -> None:
        self._done = True
        self.hover_timer.stop()
        self.mgr.save(self.chosen)
        self.accept()

    def reject(self) -> None:                                                  # Cancelar, Esc e fechar (X)
        self._done = True
        self.hover_timer.stop()
        self.mgr.restore()
        super().reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:                         # noqa: N802 - API do Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self.search.hasFocus():
            self.save()
            return
        super().keyPressEvent(event)


def themes_button(manager: Q.ThemeManager, open_fn: Callable[[], None]) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(themes_icon(manager.current))
    btn.setIconSize(QSize(18, 18))
    btn.setText("Temas")
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setToolTip("Temas: pré-visualize passando o mouse e salve o que preferir")
    btn.setAccessibleName("Temas")
    btn.clicked.connect(open_fn)
    manager.on_change(lambda t: btn.setIcon(themes_icon(t)))
    return btn


def _favorites_load() -> list[str]:
    from agent_bar import settings as S
    return list(S.load().theme_favorites)


def _favorites_save(keys: list[str]) -> None:
    from agent_bar import settings as S
    s = S.load()
    s.theme_favorites = list(keys)
    S.save(s)


__all__ = ["HOVER_MS", "ThemePicker", "swatch", "themes_button", "themes_icon"]
