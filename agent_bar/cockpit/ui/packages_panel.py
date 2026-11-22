"""packages_panel.py — painel "Pacotes" da aba Revisão visual (F7.4): pedido de correção (Claude) e pedido GPT, locais."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import review_packages as RP
from agent_bar.cockpit.evidence import Run, reference_origin
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, COMBO_CSS, TEXT_CSS

ENGINE_WORKSPACE = Path(r"E:\Claude\apps\sketchup-mcp-next")


class PackagesPanel(QWidget):
    send_to_chat = Signal(str, str)                                            # workspace, texto do pedido (não enviado)

    def __init__(self, writer: RP.PackageWriter | None = None, engine_workspace: Path | None = None) -> None:
        super().__init__()
        self.writer = writer or RP.PackageWriter()
        self.engine_workspace = engine_workspace or ENGINE_WORKSPACE
        self.a: Run | None = None
        self.b: Run | None = None
        self.cameras: list[str] = []
        self.report_fn = lambda: None
        self.last_claude: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        form = QGridLayout()
        self.origin = QComboBox()
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("assunto (ex.: cama sem textura de tecido)")
        self.reference = QComboBox()
        self.criteria = QLineEdit()
        self.criteria.setPlaceholderText("critério de aceite extra (opcional)")
        self.question = QLineEdit()
        self.question.setPlaceholderText("pergunta objetiva ao GPT (obrigatória no pacote GPT)")
        self.briefing = QPlainTextEdit()
        self.briefing.setPlaceholderText("briefing para o GPT: o que se quer, o que evitar, restrições da planta")
        self.briefing.setFixedHeight(70)
        self.briefing.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + "}")
        for w in (self.origin, self.subject, self.reference, self.criteria, self.question):
            w.setStyleSheet(COMBO_CSS)
        cells = [("Run de origem", self.origin), ("Assunto", self.subject), ("Referência", self.reference),
                 ("Critério extra", self.criteria), ("Pergunta (GPT)", self.question)]
        for i, (cap, w) in enumerate(cells):
            lb = QLabel(cap)
            lb.setStyleSheet("color:#8f8f98;font-size:11px")
            form.addWidget(lb, i // 2, (i % 2) * 2)
            form.addWidget(w, i // 2, (i % 2) * 2 + 1)
        lay.addLayout(form)
        lay.addWidget(self.briefing)
        row = QHBoxLayout()
        self.claude_btn = QPushButton("Gerar pedido de correção (Claude)")
        self.chat_btn = QPushButton("Abrir pedido no Chat (motor)")
        self.gpt_btn = QPushButton("Gerar pacote GPT (local)")
        for b in (self.claude_btn, self.chat_btn, self.gpt_btn):
            b.setStyleSheet(BTN_CSS)
            row.addWidget(b)
        lay.addLayout(row)
        self.status = QLabel("Os pacotes ficam fora do evidence. A correção gera run novo. Nada é enviado sozinho.")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.claude_btn.clicked.connect(self.make_claude)
        self.gpt_btn.clicked.connect(self.make_gpt)
        self.chat_btn.clicked.connect(self.open_in_chat)
        self.chat_btn.setEnabled(False)
        self._enable(False)

    def _enable(self, on: bool) -> None:
        self.claude_btn.setEnabled(on)
        self.gpt_btn.setEnabled(on)

    def set_pair(self, a: Run | None, b: Run | None, cameras: list[str]) -> None:
        self.a, self.b, self.cameras = a, b, cameras
        self.origin.clear()
        self.reference.clear()
        self.reference.addItem("sem referência aprovada", None)
        ok = [r for r in (b, a) if r is not None and r.valid]
        for r in ok:
            self.origin.addItem(f"{r.stage} · {r.run_id}", r.stage)
            for p in r.references():
                origin = reference_origin(p)
                label = f"{r.stage}: {p.name} — {origin}" if origin else f"{r.stage}: {p.name} — SEM ORIGEM (não pode ser aprovada)"
                self.reference.addItem(label, str(p))
        self._enable(bool(ok))

    def _origin_run(self) -> Run | None:
        st = self.origin.currentData()
        return next((r for r in (self.a, self.b) if r is not None and r.stage == st), None)

    def _show(self, text: str, ok: bool) -> None:
        self.status.setText(text if ok else f"recusado: {text}")
        self.status.setStyleSheet(f"color:{'#3ddc84' if ok else '#ff9a9b'};font-size:11px")

    def make_claude(self) -> Path | None:
        origin = self._origin_run()
        other = self.a if origin is self.b else self.b
        ref = self.reference.currentData()
        try:
            out = self.writer.claude(origin, self.report_fn(), self.cameras, subject=self.subject.text() or "correção",
                                     reference=Path(ref) if ref else None, extra_criteria=[self.criteria.text()],
                                     compared_with=other)
        except (RP.PackageError, ValueError, AttributeError) as exc:
            self._show(str(exc), False)
            return None
        self.last_claude = out
        self.chat_btn.setEnabled(True)
        self._show(f"pedido de correção gerado em {out} (request.md + request.json)", True)
        return out

    def make_gpt(self) -> Path | None:
        origin = self._origin_run()
        ref = self.reference.currentData()
        try:
            out = self.writer.gpt(origin, self.report_fn(), self.cameras, subject=self.subject.text() or "revisao",
                                  briefing=self.briefing.toPlainText(), question=self.question.text(),
                                  references=[Path(ref)] if ref else [])
        except (RP.PackageError, ValueError, AttributeError) as exc:
            self._show(str(exc), False)
            return None
        self._show(f"pacote GPT gerado em {out}. Leve-o ao ChatGPT; a resposta volta por PR no evidence.", True)
        return out

    def open_in_chat(self) -> None:
        if self.last_claude is None:
            return
        text = (self.last_claude / "request.md").read_text(encoding="utf-8")
        ws = str(self.engine_workspace) if (self.engine_workspace / ".git").exists() else ""
        self.send_to_chat.emit(ws, f"{text}\n(arquivo: {self.last_claude / 'request.json'})")
