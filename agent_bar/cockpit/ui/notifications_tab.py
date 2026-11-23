"""notifications_tab.py — sino com contador + painel "Notificações" (SPEC P2S-STUDIO-WORKSPACE-UX, F3).

O painel le o Notification Center (mesmo SQLite do Permission Inbox), sincroniza as permissoes a cada atualizacao e
mostra: o que pede acao primeiro, depois as novas, depois o resto; filtros por categoria, estado e data; busca;
agrupamento por sessao; "Abrir" leva a origem (ex.: o pedido na aba Permissoes); "Marcar como lida"/"todas".
Notificacao nativa do Windows e opcional (`native_notify`) e so acontece DEPOIS da linha existir aqui.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit.notifications import CATEGORIES, NotificationCenter

CATEGORY_TEXT = {"PERMISSION": "Permissão", "AGENT": "Agente", "TASK": "Tarefa", "BUILD": "Build", "RENDER": "Render",
                 "PR_CI": "PR/CI", "ERROR": "Erro", "REVIEW": "Revisão", "HUMAN_APPROVAL": "Aprovação humana", "IMAGE": "Imagem"}
STATE_TEXT = {"NEW": "nova", "READ": "lida", "ACTION_REQUIRED": "pede ação", "RESOLVED": "resolvida",
              "EXPIRED": "expirada", "FAILED": "falhou"}
STATE_COLOR = {"ACTION_REQUIRED": "#ffc233", "NEW": "#4aa8ff", "FAILED": "#ff9a9b", "EXPIRED": "#8f8f98",
               "RESOLVED": "#3ddc84", "READ": "#8f8f98"}
PERIODS = (("Qualquer data", None), ("Últimas 24 h", 86400), ("Últimos 7 dias", 7 * 86400), ("Últimos 30 dias", 30 * 86400))


class BellButton(QToolButton):
    """Sino com contador: novas + que pedem acao. Amarelo quando ha acao pendente."""

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("Notificações")
        self.set_count(0, 0)

    def set_count(self, badge: int, actions: int) -> None:
        self.setText(f"🔔 {badge}" if badge else "🔔")
        self.setToolTip(f"{actions} pedem ação · {badge - actions} novas" if badge else "Nenhuma notificação nova")
        color = "#ffc233" if actions else ("#4aa8ff" if badge else "#8f8f98")
        self.setStyleSheet(f"QToolButton{{color:{color};font-weight:bold;padding:4px 10px;border:0}}")


class NotificationsTab(QWidget):
    open_link = Signal(str)

    def __init__(self, center: NotificationCenter, inbox=None, refresh_ms: int = 2000,
                 native_notify: Callable[[str, str], None] | None = None,
                 answer_fn: Callable[[str, str], bool] | None = None,
                 is_open_fn: Callable[[str], bool] = lambda _id: False) -> None:
        super().__init__()
        self.answer_fn = answer_fn                                           # F4b: acoes rapidas de permissao
        self.is_open_fn = is_open_fn
        self.center = center
        self.inbox = inbox
        self.native_notify = native_notify
        self._seen_actions: set[str] = set()
        self.bell = BellButton()
        self.bell.setParent(self)                                            # dono ate a janela coloca no canto das abas
        self.bell.hide()
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.category_box = QComboBox()
        self.category_box.addItem("Todas as categorias", "")
        for c in CATEGORIES:
            self.category_box.addItem(CATEGORY_TEXT[c], c)
        self.state_box = QComboBox()
        for label, value in (("Abertas", "open"), ("Todas", ""), ("Pedem ação", "ACTION_REQUIRED"), ("Resolvidas", "RESOLVED"),
                             ("Expiradas", "EXPIRED"), ("Falhas", "FAILED")):
            self.state_box.addItem(label, value)
        self.period_box = QComboBox()
        for label, secs in PERIODS:
            self.period_box.addItem(label, secs)
        self.search = QLineEdit()
        self.search.setPlaceholderText("buscar…")
        self.group_box = QCheckBox("Agrupar por sessão")
        for w in (self.category_box, self.state_box, self.period_box):
            bar.addWidget(w)
        bar.addWidget(self.search, 2)
        bar.addWidget(self.group_box)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Quando", "Categoria", "Estado", "Título", "Detalhe"])
        self.tree.setAccessibleName("Notificações")
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(3, 420)                                    # o titulo e o que se le primeiro
        lay.addWidget(self.tree, 1)
        actions = QHBoxLayout()
        self.summary = QLabel("")
        self.open_btn = QPushButton("Abrir origem")
        self.read_btn = QPushButton("Marcar como lida")
        self.all_read_btn = QPushButton("Marcar todas como lidas")
        self.allow_btn = QPushButton("Permitir uma vez")
        self.deny_btn = QPushButton("Negar")
        self.deny_btn.setStyleSheet("color:#ff9a9b")
        actions.addWidget(self.summary, 2)
        for b in (self.allow_btn, self.deny_btn, self.open_btn, self.read_btn, self.all_read_btn):
            actions.addWidget(b)
        lay.addLayout(actions)
        for box in (self.category_box, self.state_box, self.period_box):
            box.currentIndexChanged.connect(lambda _i: self.refresh())
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.group_box.toggled.connect(lambda _b: self.refresh())
        self.open_btn.clicked.connect(self._open)
        self.read_btn.clicked.connect(self._read)
        self.all_read_btn.clicked.connect(lambda: (self.center.mark_all_read(), self.refresh()))
        self.tree.itemDoubleClicked.connect(lambda _it, _c: self._open())
        self.tree.currentItemChanged.connect(lambda _c, _p: self._update_actions())
        self.allow_btn.clicked.connect(lambda: self._answer("allow_once"))
        self.deny_btn.clicked.connect(lambda: self._answer("deny"))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.refresh()

    # ------------------------------------------------------------------------------------------------ dados
    def refresh(self) -> None:
        if self.inbox is not None:
            self.inbox.expire_due()
            self.center.sync_permissions(self.inbox)
        self._native_for_new_actions()
        state = self.state_box.currentData()
        states = ["NEW", "ACTION_REQUIRED"] if state == "open" else ([state] if state else [])
        secs = self.period_box.currentData()
        items = self.center.list(categories=[self.category_box.currentData()] if self.category_box.currentData() else [],
                                 states=states, text=self.search.text().strip(),
                                 since=(self.center.clock() - secs) if secs else None)
        keep = self._current_id()
        self.tree.clear()
        self.tree.setRootIsDecorated(self.group_box.isChecked())
        groups: dict[str, QTreeWidgetItem] = {}
        for n in items:
            it = QTreeWidgetItem([time.strftime("%d/%m %H:%M", time.localtime(n.updated_at)), CATEGORY_TEXT.get(n.category, n.category),
                                  STATE_TEXT.get(n.state, n.state), n.title, n.body])
            it.setData(0, Qt.ItemDataRole.UserRole, n.id)
            it.setForeground(2, QBrush(QColor(STATE_COLOR.get(n.state, "#cfcfd4"))))
            if self.group_box.isChecked():
                key = n.session_id or "(sem sessão)"
                if key not in groups:
                    groups[key] = QTreeWidgetItem([key])
                    self.tree.addTopLevelItem(groups[key])
                    groups[key].setExpanded(True)
                groups[key].addChild(it)
            else:
                self.tree.addTopLevelItem(it)
            if n.id == keep:
                self.tree.setCurrentItem(it)
        c = self.center.counts()
        self.bell.set_count(self.center.badge(), c["ACTION_REQUIRED"])
        self.summary.setText(f"{c['ACTION_REQUIRED']} pedem ação · {c['NEW']} novas · {len(items)} na lista")
        self._update_actions()

    def _native_for_new_actions(self) -> None:
        """Opcional: avisa no Windows quando surge acao pendente nova (a linha ja existe no banco)."""
        if self.native_notify is None:
            return
        for n in self.center.list(states=["ACTION_REQUIRED"], limit=50):
            if n.id not in self._seen_actions:
                self._seen_actions.add(n.id)
                try:
                    self.native_notify(n.title, n.body)
                except Exception:                                             # noqa: BLE001 - nativo e opcional
                    pass

    def _permission_id(self) -> str | None:
        nid = self._current_id()
        if not nid:
            return None
        n = self.center.get(nid)
        return n.source.split(":", 1)[1] if n.source.startswith("permission:") else None

    def _update_actions(self) -> None:
        rid = self._permission_id()
        can = bool(rid and self.answer_fn is not None and self.is_open_fn(rid))
        self.allow_btn.setVisible(can)
        self.deny_btn.setVisible(can)

    def _answer(self, choice: str) -> None:
        rid = self._permission_id()
        if rid and self.answer_fn is not None:
            self.answer_fn(rid, choice)
            self.refresh()

    def _current_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None

    def _read(self) -> None:
        nid = self._current_id()
        if nid:
            self.center.mark_read(nid)
            self.refresh()

    def _open(self) -> None:
        nid = self._current_id()
        if not nid:
            return
        n = self.center.get(nid)
        self.center.mark_read(nid)
        if n.link:
            self.open_link.emit(n.link)
        self.refresh()

    def shutdown(self) -> None:
        self.timer.stop()
