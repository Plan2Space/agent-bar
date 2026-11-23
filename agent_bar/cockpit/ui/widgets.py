"""widgets.py — pecas visuais do chat: bloco de texto, ToolCard, PlanCard e dialogo de permissao (D9)."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit.acp import PermissionRequest

TOOL_CONTENT_LIMIT = 8000
KIND_ICON = {"read": "📄", "edit": "✏️", "delete": "🗑", "move": "↔", "search": "🔎", "execute": "▶", "think": "💭",
             "fetch": "🌐", "switch_mode": "⇄"}
STATUS_COLOR = {"pending": "#8a8f98", "in_progress": "#4aa8ff", "completed": "#3ddc84", "failed": "#ff6b6d"}
STATUS_LABEL = {"pending": "pendente", "in_progress": "rodando", "completed": "concluída", "failed": "falhou"}

CARD_CSS = "QFrame#card{background:#1d1d22;border:1px solid #2e2e36;border-radius:8px}"


def content_text(content: object, limit: int = TOOL_CONTENT_LIMIT) -> str:
    """Texto legivel do `content` de uma ferramenta (blocos ACP), truncado com marcador."""
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        inner = block.get("content") if block.get("type") == "content" else block
        if isinstance(inner, dict) and inner.get("type") == "text":
            parts.append(str(inner.get("text", "")))
        elif block.get("type") == "diff":
            parts.append(f"diff {block.get('path', '')}\n{block.get('newText', '')}")
        elif block.get("type") == "terminal":
            parts.append(f"[terminal {block.get('terminalId', '')}]")
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n… [truncado: {len(text) - limit} caracteres omitidos]"
    return text


class TextBlock(QLabel):
    """Texto do agente (ou pensamento), acrescentado em lotes pelo flush coalescido."""

    def __init__(self, thought: bool = False) -> None:
        super().__init__()
        self.thought = thought
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet("color:#9a9aa8;font-style:italic;font-size:12px;padding:2px 6px;border-left:2px solid #444"
                           if thought else "color:#e6e6e6;font-size:13px;padding:2px 2px")
        self._text = ""

    def append(self, text: str) -> None:
        self._text += text
        self.setText(self._text)

    def full_text(self) -> str:
        return self._text


class UserBubble(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet("color:#f0f0f0;background:#24324a;border-radius:8px;padding:6px 9px;font-size:13px")


class Notice(QLabel):
    def __init__(self, text: str, color: str = "#8f8f98") -> None:
        super().__init__(text)
        self.setWordWrap(True)
        self.setStyleSheet(f"color:{color};font-size:11px;padding:2px")


class ToolCard(QFrame):
    """Uma ferramenta por toolCallId (R13). `apply` mescla tool_call e tool_call_update."""

    def __init__(self, tool_call_id: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(CARD_CSS)
        self.tool_call_id = tool_call_id
        self.state: dict = {"title": tool_call_id, "kind": "other", "status": "pending", "content": []}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(3)
        row = QHBoxLayout()
        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.header, 1)
        self.toggle = QToolButton()
        self.toggle.setText("▸")
        self.toggle.setCheckable(True)
        self.toggle.setStyleSheet("QToolButton{color:#aaa;border:0}")
        self.toggle.toggled.connect(self._toggled)
        row.addWidget(self.toggle)
        lay.addLayout(row)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setMaximumHeight(160)
        self.body.setStyleSheet("QPlainTextEdit{background:#121215;color:#cfcfd4;border:0;font-family:Consolas,monospace;"
                                "font-size:11px}")
        self.body.setVisible(False)
        lay.addWidget(self.body)
        self._render()

    def _toggled(self, on: bool) -> None:
        self.toggle.setText("▾" if on else "▸")
        self.body.setVisible(on)

    def apply(self, data: dict) -> None:
        for k in ("title", "kind", "status", "content", "rawInput", "locations"):
            if k in data and data[k] is not None:
                self.state[k] = data[k]
        self._render()

    def _render(self) -> None:
        st = str(self.state.get("status", "pending"))
        icon = KIND_ICON.get(str(self.state.get("kind")), "•")
        color = STATUS_COLOR.get(st, "#8a8f98")
        from html import escape
        self.header.setText(f"<span style='font-size:12px'>{icon}</span> <b style='color:#e6e6e6'>"
                            f"{escape(str(self.state.get('title', '')))}</b> "
                            f"<span style='color:{color}'>{STATUS_LABEL.get(st, st)}</span>")
        self.body.setPlainText(content_text(self.state.get("content")))

    def summary(self) -> dict:
        return {"id": self.tool_call_id, "title": str(self.state.get("title", "")), "kind": str(self.state.get("kind", "")),
                "status": str(self.state.get("status", ""))}


class PlanCard(QFrame):
    """Plano do agente, substituido inteiro a cada update (R14)."""

    MARK = {"completed": "☑", "in_progress": "◐", "pending": "☐"}

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(CARD_CSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        lay.addWidget(self.label)
        self.entries: list[dict] = []

    def set_entries(self, entries: list) -> None:
        from html import escape
        self.entries = [e for e in entries if isinstance(e, dict)]
        rows = [f"{self.MARK.get(str(e.get('status')), '☐')} "
                f"<span style='color:{STATUS_COLOR.get(str(e.get('status')), '#ccc')}'>{escape(str(e.get('content', '')))}</span>"
                for e in self.entries]
        self.label.setText("<b style='color:#e6e6e6'>Plano</b><br>" + "<br>".join(rows))


class PermissionDialog(QDialog):
    """Modal com as opcoes do agente (R15). Fechar sem escolher = None (`cancelled`)."""

    def __init__(self, req: PermissionRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Permissão pedida pelo agente")
        self.setModal(True)
        self.choice: str | None = None
        lay = QVBoxLayout(self)
        tc = req.tool_call or {}
        title = QLabel(f"<b>{tc.get('title', 'ferramenta')}</b> <span style='color:#888'>({tc.get('kind', 'other')})</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)
        detail = content_text(tc.get("content"), 1200) or str(tc.get("rawInput", ""))[:1200]
        if detail:
            box = QPlainTextEdit(detail)
            box.setReadOnly(True)
            box.setMaximumHeight(140)
            lay.addWidget(box)
        row = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for opt in req.options:
            oid = str(opt.get("optionId"))
            b = QPushButton(str(opt.get("name", oid)))
            kind = str(opt.get("kind", ""))
            if kind.startswith("reject"):
                b.setStyleSheet("color:#ff9a9b")
            elif kind == "allow_always":
                b.setStyleSheet("color:#ffc233")
            b.clicked.connect(lambda _=False, o=oid: self._pick(o))
            self.buttons[oid] = b
            row.addWidget(b)
        lay.addLayout(row)

    def _pick(self, option_id: str) -> None:
        self.choice = option_id
        self.accept()


class IrreversibleDialog(QDialog):
    """Confirmacao propria para comando irreversivel (R31): so libera se o Felipe digitar o codigo de uso unico."""

    def __init__(self, req: PermissionRequest, danger: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import secrets

        from PySide6.QtWidgets import QLineEdit
        self.setWindowTitle("Comando IRREVERSÍVEL pedido pelo agente")
        self.setModal(True)
        self.code = secrets.token_hex(3)
        self.confirmed = False
        lay = QVBoxLayout(self)
        tc = req.tool_call or {}
        msg = QLabel(f"<b style='color:#ff6b6d'>Irreversível: {danger}</b><br>{tc.get('title', '')}<br><br>"
                     f"O app não executa isso sozinho. Para deixar o agente seguir, digite <b>{self.code}</b>.")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        lay.addWidget(msg)
        self.entry = QLineEdit()
        lay.addWidget(self.entry)
        row = QHBoxLayout()
        self.ok = QPushButton("Confirmar")
        self.cancel = QPushButton("Recusar")
        self.ok.clicked.connect(self._confirm)
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.ok)
        row.addWidget(self.cancel)
        lay.addLayout(row)

    def _confirm(self) -> None:
        if self.entry.text().strip().lower() == self.code:
            self.confirmed = True
            self.accept()


def ask_permission_dialog(req: PermissionRequest, parent: QWidget | None = None) -> str | None:
    from agent_bar.cockpit.devmode import irreversible_request
    danger = irreversible_request(req.tool_call or {})
    if danger:
        gate = IrreversibleDialog(req, danger, parent)
        gate.exec()
        if not gate.confirmed:
            return None
    dlg = PermissionDialog(req, parent)
    dlg.exec()
    return dlg.choice


class PermissionCard(QFrame):
    """Pedido de permissao dentro do Chat, sem bloquear a janela (UX F4b). Os botoes sao as opcoes do agente; a
    decisao pode vir daqui, da aba Permissoes ou das Notificacoes, e o cartao mostra o resultado de onde vier."""

    def __init__(self, req: PermissionRequest, entry, on_choice) -> None:
        super().__init__()
        self.entry_id = entry.id
        self.setStyleSheet("QFrame{background:#2a2414;border:1px solid #6b5520;border-radius:8px}"
                           "QLabel{border:0;background:transparent}")
        lay = QVBoxLayout(self)
        tc = req.tool_call or {}
        risk_color = {"IRREVERSIBLE": "#ff6b6d", "HIGH": "#ff9a9b", "MEDIUM": "#ffc233", "LOW": "#8f8f98"}.get(entry.risk, "#ccc")
        head = QLabel(f"<b style='color:#ffc233'>Permissão pedida</b> · <b>{escape(str(tc.get('title', 'ferramenta')))}</b> "
                      f"<span style='color:#888'>({escape(str(tc.get('kind', 'other')))})</span> · "
                      f"<span style='color:{risk_color}'>risco {entry.risk}</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        lay.addWidget(head)
        detail = entry.resource if entry.kind in ("edit", "read", "delete", "move") and entry.resource else entry.operation
        for text, css in ((detail[:300] if detail else "", "color:#cfcfd4;font-family:Consolas,monospace;font-size:11px"),
                          (f"motivo do agente: {entry.reason[:200]}" if entry.reason else "", "color:#cfcfd4;font-size:11px"),
                          (f"pedido repetido: {entry.repeat_note}" if entry.repeat_note else "", "color:#ffc233;font-size:11px")):
            if text:
                lbl = QLabel(text)
                lbl.setTextFormat(Qt.TextFormat.PlainText)                   # texto do agente: nunca interpretado
                lbl.setWordWrap(True)
                lbl.setStyleSheet(css)
                lay.addWidget(lbl)
        row = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for opt in req.options:
            oid = str(opt.get("optionId"))
            b = QPushButton(str(opt.get("name", oid)))
            kind = str(opt.get("kind", ""))
            if kind.startswith("reject"):
                b.setStyleSheet("color:#ff9a9b")
            elif kind == "allow_always":
                b.setStyleSheet("color:#ffc233")
            b.clicked.connect(lambda _=False, o=oid: on_choice(o))
            self.buttons[oid] = b
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.result = QLabel("aguardando sua resposta — também dá para responder na aba Permissões ou nas Notificações")
        self.result.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.result)

    def set_resolved(self, text: str, ok: bool) -> None:
        for b in self.buttons.values():
            b.setEnabled(False)
        self.result.setText(text)
        self.result.setStyleSheet(f"color:{'#3ddc84' if ok else '#ff9a9b'};font-size:11px")
