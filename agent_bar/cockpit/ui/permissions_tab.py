"""permissions_tab.py — aba "Permissões": o Permission Inbox na tela (SPEC P2S-STUDIO-WORKSPACE-UX, F4).

Pendentes primeiro e destacados, depois o historico (filtro por status e texto), detalhes do pedido selecionado (o que,
onde, por que, risco, politica, quem decidiu, resultado e, se for repetido, a explicacao) e as regras persistentes com
botao para desativar. So le o banco a cada 2 s (barato); a decisao de um pedido em curso continua no dialogo do Chat.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit.permission_inbox import Entry, PermissionInbox

COLS = ["Quando", "Agente", "Ferramenta", "Operação", "Risco", "Política", "Status", "Decisão", "Resultado"]
STATUS_COLOR = {"PENDING": "#ffc233", "ALLOWED": "#3ddc84", "DENIED": "#ff9a9b", "EXPIRED": "#8f8f98", "CANCELLED": "#8f8f98"}
RISK_COLOR = {"IRREVERSIBLE": "#ff6b6d", "HIGH": "#ff9a9b", "MEDIUM": "#ffc233", "LOW": "#8f8f98"}
POLICY_TEXT = {"NORMAL": "pergunta", "PREVIOUSLY_GRANTED": "regra sua", "AUTO_MODE": "fluxo automático",
               "BYPASS_MODE": "modo sem pedir permissão", "IRREVERSIBLE": "irreversível (só você)",
               "FORBIDDEN": "proibido por política"}


class PermissionsTab(QWidget):
    def __init__(self, inbox: PermissionInbox, refresh_ms: int = 2000) -> None:
        super().__init__()
        self.inbox = inbox
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight:bold")
        self.status_box = QComboBox()
        for label, value in (("Todos", ""), ("Pendentes", "PENDING"), ("Permitidos", "ALLOWED"), ("Negados", "DENIED"),
                             ("Expirados", "EXPIRED"), ("Cancelados", "CANCELLED")):
            self.status_box.addItem(label, value)
        self.search = QLineEdit()
        self.search.setPlaceholderText("buscar em ferramenta, operação ou recurso…")
        bar.addWidget(self.summary, 2)
        bar.addWidget(QLabel("Status"))
        bar.addWidget(self.status_box)
        bar.addWidget(self.search, 2)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName("Pedidos de permissão")
        self.details = QTextBrowser()
        split.addWidget(self.tree)
        split.addWidget(self.details)
        split.setSizes([500, 220])
        lay.addWidget(split, 1)
        rules_bar = QHBoxLayout()
        self.rules_label = QLabel("")
        self.rules_box = QComboBox()
        self.disable_btn = QPushButton("Desativar regra")
        rules_bar.addWidget(self.rules_label)
        rules_bar.addWidget(self.rules_box, 2)
        rules_bar.addWidget(self.disable_btn)
        lay.addLayout(rules_bar)
        self.status_box.currentIndexChanged.connect(lambda _i: self.refresh())
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show(cur))
        self.disable_btn.clicked.connect(self._disable_rule)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_ms)
        self.refresh()

    def refresh(self) -> None:
        pending = self.inbox.pending()                                      # tambem vence os prazos
        status = self.status_box.currentData()
        hist = self.inbox.history(status=[status] if status else [], text=self.search.text().strip())
        keep = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.tree.currentItem() else None
        self.tree.clear()
        rows = ([e for e in hist if e.pending] + [e for e in hist if not e.pending])
        for e in rows:
            it = QTreeWidgetItem([time.strftime("%d/%m %H:%M", time.localtime(e.created_at)), e.provider, e.title,
                                  e.operation[:80], e.risk, POLICY_TEXT.get(e.policy, e.policy), e.status,
                                  e.decision or "—", e.result or "—"])
            it.setData(0, Qt.ItemDataRole.UserRole, e.id)
            it.setForeground(6, QBrush(QColor(STATUS_COLOR.get(e.status, "#cfcfd4"))))
            it.setForeground(4, QBrush(QColor(RISK_COLOR.get(e.risk, "#cfcfd4"))))
            if e.pending:
                for c in range(len(COLS)):
                    it.setBackground(c, QBrush(QColor("#3a3220")))
            self.tree.addTopLevelItem(it)
            if e.id == keep:
                self.tree.setCurrentItem(it)
        n = len(pending)
        self.summary.setText(f"{n} pendente(s) — responda no diálogo do Chat" if n else "nenhum pedido pendente")
        self.summary.setStyleSheet(f"font-weight:bold;color:{'#ffc233' if n else '#8f8f98'}")
        rules = self.inbox.rules()
        self.rules_box.clear()
        for r in rules:
            self.rules_box.addItem(f"{r['kind']} · “{r['title_prefix']}…” · {r['workspace'] or 'qualquer workspace'}", r["id"])
        self.rules_label.setText(f"Regras persistentes: {len(rules)}")
        self.disable_btn.setEnabled(bool(rules))

    def _show(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            self.details.clear()
            return
        e: Entry = self.inbox.get(item.data(0, Qt.ItemDataRole.UserRole))
        import html as h

        def row(k: str, v: object) -> str:
            return f"<tr><td style='color:#8f8f98;padding-right:12px'>{h.escape(k)}</td><td>{h.escape(str(v or '—'))}</td></tr>"
        def when(t: float | None) -> str:
            return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(t)) if t else "—"
        self.details.setHtml(
            f"<h3>{h.escape(e.title)}</h3>"
            + (f"<p style='color:#ffc233'><b>Pedido repetido:</b> {h.escape(e.repeat_note)}</p>" if e.repeat_note else "")
            + "<table>" + "".join(row(k, v) for k, v in (
                ("Agente", e.provider), ("Sessão", e.session_id), ("Workspace", e.workspace), ("Ferramenta", e.kind),
                ("Operação", e.operation), ("Recurso", e.resource), ("Motivo (do agente)", e.reason), ("Risco", e.risk),
                ("Política", POLICY_TEXT.get(e.policy, e.policy)), ("Status", e.status), ("Decisão", e.decision),
                ("Decidido por", e.decided_by), ("Pedido em", when(e.created_at)), ("Vence em", when(e.expires_at)),
                ("Decidido em", when(e.decided_at)), ("Resultado da execução", e.result), ("Id", e.id))) + "</table>")

    def select(self, rid: str) -> None:
        """Seleciona o pedido `rid` (vindo de uma notificacao), limpando filtros que o esconderiam."""
        self.status_box.setCurrentIndex(0)
        self.search.clear()
        self.refresh()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == rid:
                self.tree.setCurrentItem(it)
                return

    def _disable_rule(self) -> None:
        rid = self.rules_box.currentData()
        if rid:
            self.inbox.disable_rule(rid)
            self.refresh()

    def shutdown(self) -> None:
        self.timer.stop()
