"""approval_panel.py — "Aprovar" do PR selecionado na área PRs (UX-07, §2.4 "Confirmar aprovação de PR").

Sai da Central da barra sem mudar a política: `approvals.approve` continua dono da regra (só linha aprovável, releitura
de HEAD e CI antes de postar, a mesma linha postada pela conta do gh). Aqui só a tela: o estado da aprovação do PR
selecionado, o botão no HEAD exato e o diálogo de risco — modal, título "Confirmar", o mesmo `confirmation_text`, "Não"
como padrão e fechar = recusar. A releitura e o comentário rodam fora da thread da UI. Instância somente leitura
(`AGENT_BAR_OBSERVE_ONLY=1`) nunca aprova.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from agent_bar import approvals as ap

BTN_CSS = ("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:#34343c}QPushButton:disabled{color:#666}")


def confirm_approval(text: str, parent: QWidget | None) -> bool:
    """Diálogo de risco: modal, "Não" como padrão; só "Sim" aprova (fechar = recusar)."""
    box = QMessageBox(QMessageBox.Icon.Question, "Confirmar", text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.setModal(True)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class _Bridge(QObject):
    done = Signal(bool, str)


class ApprovalPanel(QWidget):
    """Linha "Aprovação: <estado> · <risco> · HEAD <sha7>" + [Aprovar no HEAD <sha7>] para o PR selecionado."""

    def __init__(self, approvals_fn: Callable[[], ap.ApprovalBoard | None] = lambda: None,
                 approve_fn: Callable = ap.approve, gh_runner: Callable | None = None,
                 report: Callable[[str], None] = lambda _m: None,
                 confirm: Callable[[str, QWidget | None], bool] = confirm_approval) -> None:
        super().__init__()
        self.approvals_fn = approvals_fn
        self.approve_fn = approve_fn
        self.gh_runner = gh_runner
        self.report = report
        self.confirm = confirm
        self.row: ap.PrApproval | None = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-approve")
        self.bridge = _Bridge()
        self.bridge.done.connect(self._done)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("Aprovação: selecione um PR")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setStyleSheet("color:#bdbdc4;font-size:12px")
        self.button = QPushButton("Aprovar")
        self.button.setStyleSheet(BTN_CSS)
        self.button.setEnabled(False)
        self.button.clicked.connect(self.approve)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.button)

    @staticmethod
    def observe_only() -> bool:
        return os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1"

    def show_for(self, repo: str, number: int) -> None:
        board = self.approvals_fn()
        self.row = None
        self.button.setText("Aprovar")
        if board is None:
            self.label.setText("Aprovação: a coleta ainda não chegou")
            self.button.setEnabled(False)
            return
        row = next((r for r in board.rows if r.repo == repo and r.number == number), None)
        if row is None:
            self.label.setText("Aprovação: este PR não está na fila de aprovação")
            self.button.setEnabled(False)
            return
        self.row = row
        sha7 = row.head_sha[:7]
        text = f"Aprovação: {row.state} · {row.risk or 'risco ?'} · HEAD {sha7} · CI {row.ci_state}"
        if self.observe_only():
            text += " · instância somente leitura: aprovação desativada"
        elif not row.can_approve and row.blocked_reason:
            text += f" · {row.blocked_reason}"
        self.label.setText(text)
        self.button.setText(f"Aprovar no HEAD {sha7}")
        self.button.setEnabled(row.can_approve and not self.observe_only())

    def approve(self) -> None:
        row = self.row
        if self.observe_only():
            self.report("instância somente leitura: aprovação desativada")
            return
        if row is None or not row.can_approve:
            self.report(f"aprovação indisponível: {row.blocked_reason if row else 'nenhum PR selecionado'}")
            return
        if not self.confirm(ap.confirmation_text(row), self):
            self.report("aprovação cancelada: nada foi postado")
            return
        self.button.setEnabled(False)
        self.report(f"conferindo o HEAD de {row.pr_ref} antes de postar...")
        gh = self.gh_runner
        if gh is None:
            from agent_bar import collectors as col
            gh = col.run_gh

        def go() -> None:
            try:
                ok, msg = self.approve_fn(gh, row, confirm=lambda _text: True)   # já confirmado acima, na thread da UI
            except Exception as exc:                                           # noqa: BLE001 - falha vira mensagem
                ok, msg = False, f"erro inesperado: {type(exc).__name__}: {exc}"
            self.bridge.done.emit(ok, msg)
        self.pool.submit(go)

    def _done(self, ok: bool, msg: str) -> None:
        self.report(msg)
        self.button.setEnabled(bool(self.row and self.row.can_approve and not ok and not self.observe_only()))

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


__all__ = ["ApprovalPanel", "confirm_approval"]
