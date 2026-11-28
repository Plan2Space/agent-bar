"""gh_connect.py — o componente "GitHub não conectado" (FR-04), o mesmo em PRs, Actions e Saúde.

Substitui a tabela vazia e a linha vermelha "[auth] gh sem login: rode `gh auth login` num terminal" por uma frase e uma
ação: **Conectar** abre um terminal já com `gh auth login` (sem terminal, copia o comando e diz onde colar) e
**Instalar** abre a página do GitHub CLI quando ele nem existe. **Verificar de novo** relê.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from agent_bar import controls as ctl
from agent_bar.cockpit.ui.theme.qt import style

BTN_CSS = ("QPushButton{background:@surface_alt;color:@text;border:1px solid @border;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:@hover}")                          # tokens do Design System (segue o tema)
INSTALL_URL = "https://cli.github.com/"
LOGIN_CMD = "gh auth login"
SETUP_KINDS = ("auth", "not_found")                                           # falhas que o usuário resolve sozinho


def open_login_terminal() -> bool:
    """Abre uma janela de console com `gh auth login`. False = não deu (quem chama copia o comando)."""
    if os.name != "nt":
        return False
    from agent_bar.cockpit.github import _default_exe
    exe = _default_exe()[0]
    try:
        subprocess.Popen(["cmd.exe", "/c", "start", "GitHub: entrar na conta", "cmd.exe", "/k", exe, "auth", "login"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return False
    return True


class GhConnect(QFrame):
    retry = Signal()

    def __init__(self, what: str = "os PRs", open_terminal: Callable[[], bool] = open_login_terminal,
                 open_url: Callable[[str], object] = ctl.open_url) -> None:
        super().__init__()
        self.what = what
        self.kind = ""
        self.open_terminal = open_terminal
        self.open_url = open_url
        self.setObjectName("gh_connect")
        style(self, "QFrame#gh_connect{background:@surface_alt;border:1px solid @accent;border-radius:6px}")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)   # não estica no lugar da tabela
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        style(self.label, "color:@text;font-size:12px;border:0")
        self.primary = QPushButton("Conectar")
        self.retry_btn = QPushButton("Verificar de novo")
        for b in (self.primary, self.retry_btn):
            style(b, BTN_CSS)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.primary)
        lay.addWidget(self.retry_btn)
        self.primary.clicked.connect(self._act)
        self.retry_btn.clicked.connect(self.retry.emit)
        self.setVisible(False)

    def show_kind(self, kind: str) -> None:
        self.kind = kind
        if kind == "not_found":
            self.label.setText(f"GitHub CLI não instalado. Instale o GitHub CLI para ver {self.what}; depois clique em "
                               "Verificar de novo.")
            self.primary.setText("Instalar")
        else:
            self.label.setText(f"Conecte sua conta do GitHub para ver {self.what}.")
            self.primary.setText("Conectar")
        self.setVisible(True)

    def _act(self) -> None:
        if self.kind == "not_found":
            self.open_url(INSTALL_URL)
            return
        if self.open_terminal():
            self.label.setText("Siga as instruções na janela que abriu e, ao terminar, clique em Verificar de novo.")
            return
        QApplication.clipboard().setText(LOGIN_CMD)
        self.label.setText("Comando copiado. Abra o PowerShell, cole (Ctrl+V), siga as instruções e clique em "
                           "Verificar de novo.")
