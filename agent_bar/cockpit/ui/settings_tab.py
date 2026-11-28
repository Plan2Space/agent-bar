"""settings_tab.py — Configurações: Saúde do sistema e Diagnóstico (UX-06 do P2S-STUDIO-UX-CONSOLIDATION-F0).

O que a Central da barra mostrava (agentes, fila de PRs, EventStore, runtime, "Copiar diagnóstico") passa a morar aqui,
dentro da janela principal: nada abre outra janela. Cada página tem rolagem própria; texto longo quebra ou rola, nunca
alarga a janela.
"""
from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import health as H
from agent_bar.cockpit import permission_mode as PM

STATE_COLOR = {H.OK: "#3ddc84", H.WARN: "#f5c542", H.BAD: "#ff6b6d", H.UNKNOWN: "#8a8f98"}
STATE_GLYPH = {H.OK: "●", H.WARN: "▲", H.BAD: "✖", H.UNKNOWN: "○"}
BTN_CSS = ("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:#34343c}")
SECTIONS = ("Saúde do sistema", "Diagnóstico", "Geral", "Primeiro uso")


class SettingsTab(QWidget):
    def __init__(self, core=None, chat=None, review=None, checkout_sha_fn: Callable[[], str] | None = None,
                 chat_fn: Callable[[], object] | None = None, first_run_kwargs: dict | None = None) -> None:
        super().__init__()
        self.first_run_kwargs = first_run_kwargs or {}                     # FR-01: o teste troca a máquina por falsos
        self.core, self.review = core, review
        self._chat_fn = chat_fn or (lambda: chat)                          # #106: a sessão do Chat aberta AGORA
        self.checkout_sha_fn = checkout_sha_fn or _checkout_sha
        self._sha: str | None = None                                        # lido uma vez (git fora do caminho quente)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.sections = QListWidget()
        self.sections.addItems(list(SECTIONS))
        self.sections.setFixedWidth(180)
        self.sections.setStyleSheet("QListWidget{background:#17171a;color:#cfcfd4;border:1px solid #2a2a30;font-size:13px}"
                                    "QListWidget::item{padding:6px 8px}"
                                    "QListWidget::item:selected{background:#24324a;color:#ffffff}")
        self.pages = QStackedWidget()
        self.health_box = QWidget()
        self.health_lay = QVBoxLayout(self.health_box)
        self.health_lay.setContentsMargins(4, 4, 12, 4)
        self.health_lay.setSpacing(10)
        self.pages.addWidget(_scroll(self.health_box))
        diag = QWidget()
        dl = QVBoxLayout(diag)
        dl.setContentsMargins(4, 4, 4, 4)
        self.diag_text = QPlainTextEdit()
        self.diag_text.setReadOnly(True)
        self.diag_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.diag_text.setStyleSheet("QPlainTextEdit{background:#121215;color:#cfcfd4;border:1px solid #2a2a30;"
                                     "font-family:Consolas,monospace;font-size:12px}")
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copiar diagnóstico")
        self.refresh_btn = QPushButton("Atualizar")
        for b in (self.copy_btn, self.refresh_btn):
            b.setStyleSheet(BTN_CSS)
            row.addWidget(b)
        row.addStretch(1)
        self.diag_status = QLabel("")
        self.diag_status.setStyleSheet("color:#8f8f98;font-size:11px")
        row.addWidget(self.diag_status)
        dl.addLayout(row)
        dl.addWidget(self.diag_text, 1)
        self.pages.addWidget(diag)
        general = QWidget()                                                  # Geral: preferências do app e permissões
        gl = QVBoxLayout(general)
        gl.setContentsMargins(4, 4, 4, 4)
        # UX-07: a barra flutuante é opcional (D-UX-1)
        self.bar_toggle = QCheckBox("Barra flutuante (opcional)")
        self.bar_toggle.setStyleSheet("QCheckBox{color:#e6e6e6;font-size:13px}")
        self.bar_toggle.setChecked(bool(getattr(getattr(core, "settings", None), "bar_enabled", False)))
        self.bar_toggle.setEnabled(hasattr(core, "set_bar_enabled"))
        self.bar_toggle.toggled.connect(self._bar_toggled)
        bar_hint = QLabel("A barra mostra Claude, GitHub e PRs num canto da tela. Desligada, a coleta, a bandeja e os "
                          "avisos continuam; o P2S Studio fecha para a bandeja e só “Sair do P2S Studio” encerra.")
        bar_hint.setWordWrap(True)
        bar_hint.setStyleSheet("color:#8f8f98;font-size:12px")
        gl.addWidget(self.bar_toggle)
        gl.addWidget(bar_hint)
        # #114: permissões dos agentes (bypass por padrão, decisão do Felipe)
        self.ask_toggle = QCheckBox("Pedir permissão antes de cada ferramenta dos agentes")
        self.ask_toggle.setStyleSheet("QCheckBox{color:#e6e6e6;font-size:13px}")
        self.ask_toggle.setChecked(PM.current() == PM.ASK)
        self.ask_toggle.toggled.connect(lambda on: PM.set_mode(PM.ASK if on else PM.BYPASS))
        hint = QLabel("Desligado (padrão, decisão do Felipe): bypass sempre — o Studio aprova sozinho e registra cada "
                      "aprovação no Inbox e em Notificações, inclusive operação irreversível (marcada como tal). "
                      "Ligado: volta o pedido a cada ferramenta e o código das irreversíveis. Vale para sessões novas.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8f8f98;font-size:12px")
        gl.addWidget(self.ask_toggle)
        gl.addWidget(hint)
        gl.addStretch(1)
        self.pages.addWidget(general)                                        # "Geral" já está em SECTIONS (#114)
        self.first_run_box = QWidget()                                       # FR-01: montada na 1ª visita (checa a máquina)
        QVBoxLayout(self.first_run_box).setContentsMargins(0, 0, 0, 0)
        self.first_run_page = None
        self.pages.addWidget(_scroll(self.first_run_box))
        lay.addWidget(self.sections)
        lay.addWidget(self.pages, 1)
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sections.currentRowChanged.connect(lambda r: r == SECTIONS.index("Primeiro uso") and self.first_run())
        self.sections.setCurrentRow(0)
        self.copy_btn.clicked.connect(self.copy_diagnostic)
        self.refresh_btn.clicked.connect(self.refresh_now)
        if core is not None and hasattr(core, "snapshot"):
            core.snapshot.connect(lambda _s: self.render())
        if review is not None and hasattr(review, "sources_changed"):
            review.sources_changed.connect(lambda _t: self.render())
        self.rows: list[H.HealthRow] = []
        self.render()

    def add_section(self, name: str, widget: QWidget) -> int:
        """Nova seção na lista lateral (Geral, Avançado). Devolve a linha dela."""
        self.sections.addItem(name)
        self.pages.addWidget(widget)
        return self.sections.count() - 1

    def first_run(self):
        """FR-01: a página "Vamos preparar o P2S Studio" (montada uma vez; cada visita relê a máquina)."""
        if self.first_run_page is None:
            from agent_bar.cockpit.ui.first_run import FirstRunPage
            self.first_run_page = FirstRunPage(**self.first_run_kwargs)
            self.first_run_box.layout().addWidget(self.first_run_page)
        else:
            self.first_run_page.recheck()
        return self.first_run_page

    def show_section(self, name: str) -> None:
        rows = [self.sections.item(i).text() for i in range(self.sections.count())]
        if name in rows:
            self.sections.setCurrentRow(rows.index(name))

    def _bar_toggled(self, on: bool) -> None:
        if hasattr(self.core, "set_bar_enabled"):
            self.core.set_bar_enabled(on)

    # ------------------------------------------------------------------------------------------------ conteúdo
    @property
    def chat(self):
        return self._chat_fn()

    def render(self) -> None:
        if self._sha is None:
            self._sha = self.checkout_sha_fn()
        self.rows = H.health_rows(self.core, self.chat, self.review, self._sha)
        while self.health_lay.count():
            w = self.health_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        title = QLabel("Saúde do sistema")
        title.setStyleSheet("color:#e6e6e6;font-size:15px;font-weight:600")
        self.health_lay.addWidget(title)
        if self.core is None:
            self.health_lay.addWidget(_wrap("núcleo do app indisponível: coleta e avisos não estão ligados a esta janela",
                                            "#ffb4b4"))
        for r in self.rows:
            self.health_lay.addWidget(_row_widget(r, self.refresh_now))
        self.health_lay.addStretch(1)
        self.diag_text.setPlainText(H.diagnostic_text(self.core, self.rows))

    def copy_diagnostic(self) -> None:
        QApplication.clipboard().setText(self.diag_text.toPlainText())
        self.diag_status.setText("copiado ✓")
        QTimer.singleShot(1500, self.diag_status, lambda: self.diag_status.setText(""))

    def refresh_now(self) -> None:
        worker = getattr(self.core, "worker", None)
        if worker is not None:
            worker.refresh(force=True)
            self.diag_status.setText("atualizando: a próxima coleta chega em instantes")
        else:
            self.diag_status.setText("sem núcleo: nada para atualizar")
        self.render()


def _wrap(text: str, color: str = "#cfcfd4", size: int = 12) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # quebra; nunca alarga
    lb.setStyleSheet(f"color:{color};font-size:{size}px")
    return lb


def _row_widget(r: H.HealthRow, on_retry=None) -> QWidget:
    w = QWidget()
    w.setObjectName("health_row")
    w.setProperty("health_name", r.name)
    w.setProperty("health_state", r.state)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(2)
    w.setStyleSheet("QWidget#health_row{background:#1d1d22;border:1px solid #2e2e36;border-radius:6px}")
    color = STATE_COLOR.get(r.state, "#8a8f98")
    head = QLabel(f"<span style='color:{color}'>{STATE_GLYPH.get(r.state, '○')}</span> "
                  f"<b style='color:#e6e6e6'>{html.escape(r.name)}</b> "
                  f"<span style='color:{color};font-size:11px'>{html.escape(r.state)}</span>")
    head.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(head)
    for line in r.lines:
        lay.addWidget(_wrap(line, "#bdbdc4", 12))
    if r.action:                                                             # FR-04: o mesmo componente das abas PRs/Actions
        from agent_bar.cockpit.ui.gh_connect import GhConnect
        box = GhConnect("os PRs e o Project")
        box.show_kind(r.action)
        if on_retry is not None:
            box.retry.connect(on_retry)
        lay.addWidget(box)
    return w


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setStyleSheet("QScrollArea{border:0;background:transparent}")
    sa.setWidget(inner)
    return sa


def _checkout_sha() -> str:
    from agent_bar.startup import checkout_version
    return checkout_version(Path(__file__).resolve().parents[3])


__all__ = ["SECTIONS", "SettingsTab"]
