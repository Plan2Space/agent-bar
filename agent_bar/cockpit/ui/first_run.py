"""first_run.py (UI) — a página "Vamos preparar o P2S Studio" (FR-01).

Uma linha por item de `cockpit.first_run.collect`: Pronto, Falta ou Opcional, uma frase simples e o botão da ação (Copiar
comando, Abrir página de instalação, Escolher pasta…). "Verificar de novo" relê a máquina; "Começar" só fica ativo sem
nada essencial faltando e grava `first_run_done`. A mesma página fica em Configurações → Primeiro uso.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar import controls as ctl
from agent_bar.cockpit import first_run as FR

BTN_CSS = ("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:#34343c}QPushButton:disabled{color:#666}")
GLYPH = {FR.OK: ("Pronto", "#3ddc84"), FR.MISSING: ("Falta", "#ff6b6d"),   # palavras: o ✓/✗ some em fonte
         FR.OPTIONAL: ("Opcional", "#8f8f98")}                              # sem o símbolo (vira caixa)


class _Row(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("first_run_row")
        self.setStyleSheet("QFrame#first_run_row{background:#1d1d22;border:1px solid #2e2e36;border-radius:6px}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.glyph = QLabel("")
        self.glyph.setFixedWidth(64)
        self.title = QLabel("")
        self.title.setStyleSheet("color:#e6e6e6;font-weight:600;border:0")
        self.title.setFixedWidth(230)
        self.sentence = QLabel("")
        self.sentence.setWordWrap(True)
        self.sentence.setStyleSheet("color:#cfcfd4;border:0")
        self.button = QPushButton("")
        self.button.setStyleSheet(BTN_CSS)
        for w, s in ((self.glyph, 0), (self.title, 0), (self.sentence, 1), (self.button, 0)):
            lay.addWidget(w, s)
        self.check: FR.Check | None = None

    def show_check(self, c: FR.Check) -> None:
        self.check = c
        text, color = GLYPH[c.state]
        self.glyph.setText(text)
        self.glyph.setStyleSheet(f"color:{color};font-weight:600;border:0")
        self.title.setText(c.title)
        self.sentence.setText(c.sentence)
        self.button.setText(c.button)
        self.button.setVisible(c.state != FR.OK and c.action is not None)


class FirstRunPage(QWidget):
    done = Signal()

    def __init__(self, env_fn: Callable[[], FR.Env] = FR.Env, choose_folder: Callable[[QWidget], str] | None = None,
                 open_url: Callable[[str], object] = ctl.open_url) -> None:
        super().__init__()
        self.env_fn = env_fn
        self.choose_folder = choose_folder or (lambda parent: QFileDialog.getExistingDirectory(parent, "Pasta do projeto"))
        self.open_url = open_url
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 12, 4)
        lay.setSpacing(8)
        title = QLabel("Vamos preparar o P2S Studio")
        title.setStyleSheet("color:#ffffff;font-size:16px;font-weight:600")
        lay.addWidget(title)
        intro = QLabel("Confira o que o Studio usa nesta máquina. O que diz \"Falta\" precisa de você; o opcional "
                       "pode ficar para depois.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#bdbdc4")
        lay.addWidget(intro)
        self.rows: dict[str, _Row] = {}
        self.rows_box = QVBoxLayout()
        lay.addLayout(self.rows_box)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#7ee2a8")
        lay.addWidget(self.note)
        bottom = QHBoxLayout()
        self.recheck_btn = QPushButton("Verificar de novo")
        self.start_btn = QPushButton("Começar")
        for b in (self.recheck_btn, self.start_btn):
            b.setStyleSheet(BTN_CSS)
        bottom.addStretch(1)
        bottom.addWidget(self.recheck_btn)
        bottom.addWidget(self.start_btn)
        lay.addLayout(bottom)
        lay.addStretch(1)
        self.recheck_btn.clicked.connect(self.recheck)
        self.start_btn.clicked.connect(self._start)
        self.recheck()

    def recheck(self) -> None:
        self.checks = FR.collect(self.env_fn())
        for c in self.checks:
            row = self.rows.get(c.key)
            if row is None:
                row = self.rows[c.key] = _Row()
                row.button.clicked.connect(lambda _=False, key=c.key: self._act(key))
                self.rows_box.addWidget(row)
            row.show_check(c)
        missing = FR.missing_essential(self.checks)
        self.start_btn.setEnabled(not missing)
        self.start_btn.setToolTip("" if not missing else "Falta: " + ", ".join(c.title for c in missing))

    def _act(self, key: str) -> None:
        c = self.rows[key].check
        if c is None or c.action is None:
            return
        kind, value = c.action
        if kind == "copy":
            QApplication.clipboard().setText(value)
            self.note.setText("Comando copiado. Abra o PowerShell, cole (Ctrl+V), siga as instruções e clique em "
                              "Verificar de novo.")
        elif kind == "url":
            self.open_url(value)
            self.note.setText("Página aberta no navegador. Depois de instalar, clique em Verificar de novo.")
        elif kind == "folder":
            folder = self.choose_folder(self)
            if folder and Path(folder).is_dir():
                from agent_bar.cockpit import project_folders as PF
                from agent_bar.cockpit.store import data_dir
                PF.remember(data_dir() / "preferences.json", Path(folder))
                self.note.setText(f"Pasta escolhida: {Path(folder).name}.")
            self.recheck()

    def _start(self) -> None:
        FR.mark_done()
        self.done.emit()


__all__ = ["FirstRunPage"]
