"""widgets.py — pecas visuais do chat: bloco de texto, ToolCard, PlanCard e dialogo de permissao (D9)."""
from __future__ import annotations

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


def ask_permission_dialog(req: PermissionRequest, parent: QWidget | None = None) -> str | None:
    dlg = PermissionDialog(req, parent)
    dlg.exec()
    return dlg.choice
