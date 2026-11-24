"""notifications_area.py — a área "Notificações" (UX-08; D-UX-2 e D-VS-6): Todas, "Precisa de você" e Permissões.

Tudo o que pede o Felipe mora num lugar só, a quarta área principal:
- **Todas**: o Notification Center (sino, filtros, permitir/negar rápido, "Abrir origem");
- **Precisa de você**: os PRs em que a decisão é dele (as notificações HUMAN_APPROVAL abertas, publicadas pelo núcleo a
  cada coleta, com ou sem barra), com "Abrir PR";
- **Permissões**: o Permission Inbox completo (pendentes, histórico, detalhes, regras). Saiu de Configurações → Avançado.
O título de cada seção mostra quantas pedem ação. Nada aqui decide sozinho: o que responde é o mesmo broker do Chat.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar import controls as ctl
from agent_bar.cockpit.notifications import NotificationCenter

NEEDS_STATES = ("NEW", "READ", "ACTION_REQUIRED")                               # aberto: ainda não resolvido nem vencido


class NeedsYouPanel(QWidget):
    """Os PRs que precisam do Felipe, lidos do Notification Center (fonte única com o sino)."""

    def __init__(self, center: NotificationCenter) -> None:
        super().__init__()
        self.center = center
        lay = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#cfcfd4;font-size:12px")
        lay.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Quando", "PR", "Detalhe"])
        self.tree.setAccessibleName("Precisa de você")
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(1, 380)
        self.tree.header().setStretchLastSection(True)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.open_btn = QPushButton("Abrir PR")
        self.open_btn.setEnabled(False)
        row.addWidget(self.open_btn)
        lay.addLayout(row)
        self.open_btn.clicked.connect(self._open)
        self.tree.currentItemChanged.connect(lambda cur, _p: self.open_btn.setEnabled(bool(self._url(cur))))
        self.tree.itemDoubleClicked.connect(lambda _it, _c: self._open())

    def items(self) -> list:
        return self.center.list(categories=["HUMAN_APPROVAL"], states=list(NEEDS_STATES))

    def refresh(self) -> None:
        keep = self._url(self.tree.currentItem())
        self.tree.clear()
        items = self.items()
        for n in items:
            it = QTreeWidgetItem([time.strftime("%d/%m %H:%M", time.localtime(n.updated_at)), n.title, n.body])
            it.setData(0, Qt.ItemDataRole.UserRole, n.link)
            self.tree.addTopLevelItem(it)
            if n.link and n.link == keep:
                self.tree.setCurrentItem(it)
        self.summary.setText(f"{len(items)} PR(s) esperando uma decisão sua" if items else
                             "nenhum PR esperando por você agora (a lista vem da última coleta)")
        self.open_btn.setEnabled(bool(self._url(self.tree.currentItem())))

    @staticmethod
    def _url(item: QTreeWidgetItem | None) -> str:
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _open(self) -> None:
        url = self._url(self.tree.currentItem())
        if url.startswith(("http://", "https://")):
            ctl.open_url(url)


class NotificationsArea(QWidget):
    """A quarta área: seções Todas, Precisa de você e Permissões, com a contagem do que pede ação no título."""

    TITLES = ("Todas", "Precisa de você", "Permissões")

    def __init__(self, notifications, needs: NeedsYouPanel, permissions, refresh_ms: int = 2000) -> None:
        super().__init__()
        self.notifications, self.needs, self.permissions = notifications, needs, permissions
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.sections = QTabWidget()
        self.sections.setDocumentMode(True)
        for page, title in zip((notifications, needs, permissions), self.TITLES):
            self.sections.addTab(page, title)
        lay.addWidget(self.sections)
        self.sections.currentChanged.connect(lambda _i: self._section_shown())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_counts)
        self.timer.start(refresh_ms)
        self.update_counts()

    def show_section(self, page) -> None:
        self.sections.setCurrentWidget(page)
        self._section_shown()

    def _section_shown(self) -> None:
        page = self.sections.currentWidget()
        if page is self.needs:
            self.needs.refresh()
        elif page is self.permissions:
            self.permissions.refresh()

    def update_counts(self) -> None:
        needs = len(self.needs.items())
        pending = len(self.permissions.inbox.pending())
        self.sections.setTabText(1, f"Precisa de você ({needs})" if needs else "Precisa de você")
        self.sections.setTabText(2, f"Permissões ({pending} pendente{'s' if pending > 1 else ''})" if pending
                                 else "Permissões")

    def shutdown(self) -> None:
        self.timer.stop()


__all__ = ["NEEDS_STATES", "NeedsYouPanel", "NotificationsArea"]
