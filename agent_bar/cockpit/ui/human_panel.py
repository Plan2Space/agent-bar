"""human_panel.py — painel "Revisão humana" da aba Revisão visual (E6). Só leitura e resumo local; nunca veredito."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from agent_bar import controls as ctl
from agent_bar.cockpit import human_review as HR
from agent_bar.cockpit.evidence import Run
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, TEXT_CSS


class HumanPanel(QWidget):
    def __init__(self, evidence_root_fn=None) -> None:
        super().__init__()
        self.evidence_root_fn = evidence_root_fn
        self.a: Run | None = None
        self.b: Run | None = None
        self.report_fn = lambda: None
        self.last_summary: Path | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.state = QLabel("")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        self.state.setWordWrap(True)
        self.state.setOpenExternalLinks(False)
        self.state.setStyleSheet("color:#e6e6e6;font-size:12px")
        lay.addWidget(self.state)
        how = QLabel(HR.HOW_TO_DECIDE)
        how.setWordWrap(True)
        how.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(how)
        row = QHBoxLayout()
        self.github_btn = QPushButton("Abrir o run B no GitHub (commit exato)")
        self.summary_btn = QPushButton("Gerar resumo para a decisão (local, não é veredito)")
        for b in (self.github_btn, self.summary_btn):
            b.setStyleSheet(BTN_CSS)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.reviews = QListWidget()
        self.reviews.setStyleSheet("QListWidget{background:#17171a;color:#cfcfd4;border:1px solid #2a2a30;font-size:12px}")
        self.review_text = QTextBrowser()
        self.review_text.setStyleSheet(TEXT_CSS)
        split.addWidget(self.reviews)
        split.addWidget(self.review_text)
        split.setSizes([260, 700])
        lay.addWidget(split, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.status)
        self.github_btn.clicked.connect(self.open_github)
        self.summary_btn.clicked.connect(self.make_summary)
        self.reviews.currentRowChanged.connect(self._show_review)
        self._paths: list[Path] = []

    def _root(self):
        return self.evidence_root_fn() if self.evidence_root_fn else None

    def set_pair(self, a: Run | None, b: Run | None) -> None:
        self.a, self.b = a, b
        e = html.escape
        rows = []
        for r in (a, b):
            if r is None:
                continue
            d = HR.human_detail(r)
            color = "#3ddc84" if d["state"] == "APPROVED" else "#ff6b6d" if d["state"] == "CHANGES_REQUESTED" else "#ffc233"
            extra = ""
            if d.get("accepted"):
                extra = (f"<br>&nbsp;&nbsp;{e(str(d.get('reviewer_login')))} · {e(str(d.get('date')))} · “{e(str(d.get('quote')))}”"
                         f"<br>&nbsp;&nbsp;{e(str(d.get('statement_ref')))}<br>&nbsp;&nbsp;<i>{e(d['authorship'])}</i>")
            elif d.get("problem"):
                extra = f"<br>&nbsp;&nbsp;<span style='color:#ff9a9b'>human.json recusado: {e(d['problem'])}</span>"
            rows.append(f"<b>{e(d['run'])}</b>: <b style='color:{color}'>{e(d['state'])}</b>{extra}")
        self.state.setText("<br>".join(rows) or "selecione dois runs")
        self.reviews.clear()
        self.review_text.clear()
        self._paths = list(b.llm_reviews()) if b is not None else []
        for p in self._paths:
            self.reviews.addItem(f"{b.stage}: {p.name} (consultivo)")
        if not self._paths:
            self.review_text.setPlainText("Nenhuma revisão de LLM neste run. VISUAL_REVIEW é consultivo e não decide.")
        ok = b is not None and b.valid
        self.github_btn.setEnabled(ok)
        self.summary_btn.setEnabled(ok and a is not None)

    def _show_review(self, row: int) -> None:
        if 0 <= row < len(self._paths):
            try:
                self.review_text.setMarkdown(self._paths[row].read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                self.review_text.setPlainText(f"ilegível: {exc}")

    def open_github(self) -> str | None:
        if self.b is None:
            return None
        url = HR.github_url(self.b, self._root())
        res = ctl.open_url(url)
        self.status.setText(res.message if hasattr(res, "message") else str(url))
        return url

    def make_summary(self) -> Path | None:
        if self.a is None or self.b is None:
            return None
        try:
            out = HR.decision_summary(self.a, self.b, self.report_fn(), evidence_root=self._root())
        except ValueError as exc:
            self.status.setText(f"recusado: {exc}")
            return None
        self.last_summary = out
        self.status.setText(f"resumo gerado em {out} — não é veredito; o veredito é um comentário seu no GitHub + PR no evidence.")
        return out
