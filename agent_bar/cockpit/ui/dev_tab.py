"""dev_tab.py — aba Dev (F6): sandbox -> proposta -> validacao -> aprovacao de uso unico -> promocao com backup.

Opt-in (AGENT_BAR_COCKPIT_DEVMODE=1). O agente edita so o sandbox (o Chat passa a usar o sandbox como workspace). Nada
aqui faz commit, push ou deploy: depois de promovido, o Felipe revisa o diff no checkout e decide.
"""
from __future__ import annotations

import html
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import devmode as D
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, TEXT_CSS, TREE_CSS

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_LABEL = {"idle": "parado", "proposed": "proposta pronta", "applied_sandbox": "sandbox criado", "validated": "validada",
               "promoted": "promovida", "blocked": "BLOQUEADA"}


def _ask(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


class _Bridge(QObject):
    validated = Signal(object)


class DevTab(QWidget):
    def __init__(self, *, repo: Path | None = None, enabled: bool | None = None,
                 use_workspace: Callable[[str], None] = lambda ws: None,
                 confirm: Callable[[QWidget, str, str], bool] = _ask,
                 validator: D.Validator = D.default_validator, root: Path | None = None) -> None:
        super().__init__()
        self.repo = Path(repo or REPO_ROOT).resolve()
        self.enabled = D.enabled() if enabled is None else enabled
        self.use_workspace = use_workspace
        self.confirm = confirm
        self.validator = validator
        self.root = root
        self.session: D.DevSession | None = None
        self.token = ""
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-dev")
        self.bridge = _Bridge()
        self.bridge.validated.connect(self._validated)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.state = QLabel()
        self.state.setTextFormat(Qt.TextFormat.RichText)
        self.state.setWordWrap(True)
        self.state.setStyleSheet("color:#e6e6e6;font-size:12px")
        lay.addWidget(self.state)
        row = QHBoxLayout()
        self.create_btn = QPushButton("Criar sandbox (Chat passa a usá-lo)")
        self.refresh_btn = QPushButton("Atualizar proposta")
        self.validate_btn = QPushButton("Validar (ruff + pytest)")
        self.approve_btn = QPushButton("Aprovar (uso único)")
        self.promote_btn = QPushButton("Promover com backup")
        self.buttons = [self.create_btn, self.refresh_btn, self.validate_btn, self.approve_btn, self.promote_btn]
        for b in self.buttons:
            b.setStyleSheet(BTN_CSS)
            row.addWidget(b)
        lay.addLayout(row)
        self.files = QTreeWidget()
        self.files.setStyleSheet(TREE_CSS)
        self.files.setHeaderLabels(["Arquivo", "Antes (sha256)", "Depois (sha256)"])
        self.files.setRootIsDecorated(False)
        self.files.setColumnWidth(0, 340)
        lay.addWidget(self.files, 1)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + ";font-family:Consolas,monospace;font-size:11px}")
        lay.addWidget(self.output, 1)
        foot = QLabel("Lista permitida: agent_bar/**, tests/**, docs/**. Comandos irreversíveis pedem código de confirmação. "
                      "O app nunca faz commit, push nem deploy.")
        foot.setWordWrap(True)
        foot.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(foot)
        self.create_btn.clicked.connect(self.create_sandbox)
        self.refresh_btn.clicked.connect(self.refresh)
        self.validate_btn.clicked.connect(self.validate)
        self.approve_btn.clicked.connect(self.approve)
        self.promote_btn.clicked.connect(self.promote)
        self._render()

    # ------------------------------------------------------------------------------------------------ render
    def _render(self) -> None:
        if not self.enabled:
            self.state.setText("<b>Modo desenvolvedor desligado.</b> Para usar, defina <code>AGENT_BAR_COCKPIT_DEVMODE=1</code> "
                               "e reabra a barra. Ele deixa o agente editar uma cópia do Agent Bar e promover a mudança com backup.")
            for b in self.buttons:
                b.setEnabled(False)
            return
        s = self.session
        st = s.state if s else "idle"
        color = "#ff6b6d" if st == "blocked" else "#3ddc84" if st in ("validated", "promoted") else "#ffc233"
        extra = f" · motivo: {html.escape(s.reason)}" if s and s.reason else ""
        where = f"<br>repo {html.escape(str(self.repo))}" + (f" · sandbox {html.escape(str(s.sandbox))}" if s else "")
        if s and s.backup_dir:
            where += f"<br>backup + MANIFEST.json em {html.escape(str(s.backup_dir))}"
        self.state.setText(f"Estado: <b style='color:{color}'>{STATE_LABEL.get(st, st)}</b>{extra}{where}")
        self.create_btn.setEnabled(s is None or st in ("promoted", "blocked"))
        self.refresh_btn.setEnabled(s is not None and st not in ("promoted",))
        self.validate_btn.setEnabled(st == "proposed")
        self.approve_btn.setEnabled(st == "validated" and not self.token)
        self.promote_btn.setEnabled(st == "validated" and bool(self.token))
        self.files.clear()
        for c in (s.proposal.changes if s and s.proposal else []):
            self.files.addTopLevelItem(QTreeWidgetItem([c.path, c.before[:12] or "(novo)", c.after[:12] or "(removido)"]))
        for path, why in (s.proposal.blocked if s and s.proposal else []):
            it = QTreeWidgetItem([path, "BLOQUEADO", why])
            self.files.addTopLevelItem(it)

    # ------------------------------------------------------------------------------------------------ acoes
    def create_sandbox(self) -> None:
        kwargs = {"root": self.root} if self.root else {}
        self.session = D.DevSession(self.repo, **kwargs)
        self.token = ""
        try:
            sb = self.session.create_sandbox()
        except (OSError, D.DevModeError) as exc:
            self.output.setPlainText(f"falha ao criar o sandbox: {exc}")
            self.session = None
            self._render()
            return
        self.use_workspace(str(sb))
        self.output.setPlainText(f"sandbox criado em {sb}. O Chat passou a usar o sandbox como workspace.")
        self._render()

    def refresh(self) -> None:
        if self.session is None:
            return
        self.token = ""                                           # proposta (re)lida: aprovacao anterior nao vale
        self.session.refresh_proposal()
        self._render()

    def validate(self) -> None:
        if self.session is None:
            return
        self.validate_btn.setEnabled(False)
        self.output.setPlainText("validando no sandbox (ruff + pytest)…")
        s = self.session
        fut = self.pool.submit(lambda: s.validate(self.validator))
        fut.add_done_callback(lambda f: self.bridge.validated.emit(f.exception()))

    def _validated(self, exc: object) -> None:
        if exc is not None:
            self.output.setPlainText(f"validação não rodou: {exc}")
        elif self.session is not None:
            self.output.setPlainText(self.session.validation or "(sem saída)")
        self._render()

    def approve(self) -> None:
        s = self.session
        if s is None or s.proposal is None:
            return
        files = "\n".join(c.path for c in s.proposal.changes)
        if not self.confirm(self, "Aprovar promoção (uso único)",
                            f"Promover {len(s.proposal.changes)} arquivo(s) validados para {self.repo}?\n\n{files}\n\n"
                            f"digest {s.proposal.digest[:16]}… · sessão {s.session_id}"):
            return
        try:
            self.token = s.issue_token(s.session_id)
        except D.DevModeError as exc:
            self.output.setPlainText(str(exc))
        self._render()

    def promote(self) -> None:
        s = self.session
        if s is None or not self.token:
            return
        if not self.confirm(self, "Promover agora", f"Copiar os arquivos validados para {self.repo} com backup?\n"
                                                    "Nada é commitado nem enviado: você revisa o diff depois."):
            return
        token, self.token = self.token, ""
        try:
            backup = s.promote(token, s.session_id)
            self.output.setPlainText(f"promovido. Backup e MANIFEST.json em {backup}\nRevise com `git diff` antes de commitar.")
        except D.DevModeError as exc:
            self.output.setPlainText(f"promoção recusada: {exc}")
        self._render()

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
