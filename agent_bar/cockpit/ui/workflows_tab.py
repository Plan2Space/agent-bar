"""workflows_tab.py — aba Fluxos (F5): 5 etapas sobre uma SPEC, um agente por etapa, paralelas quando marcadas."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import profiles as prof
from agent_bar.cockpit import workflows as W
from agent_bar.cockpit.acp import AsyncRuntime, PermissionRequest
from agent_bar.cockpit.store import data_dir
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, COMBO_CSS, TEXT_CSS, TREE_CSS
from agent_bar.cockpit.ui.widgets import ask_permission_dialog

STATUS_COLOR = {"pending": "#8a8f98", "running": "#4aa8ff", "done": "#3ddc84", "error": "#ff6b6d", "cancelled": "#ffc233"}
STATUS_LABEL = {"pending": "pendente", "running": "rodando", "done": "concluída", "error": "erro", "cancelled": "cancelada"}


def _one_line(text: str, limit: int = 160) -> str:
    """Resumo numa linha so (a tabela nao cresce)."""
    return " ".join(text.split())[-limit:]



def confirm_risky_stage_modes(risky: list[tuple[str, str]], parent: QWidget) -> bool:
    lines = "\n".join(f"• {name}: {mode}" for name, mode in risky)
    box = QMessageBox(QMessageBox.Icon.Warning, "Etapas sem pedir permissão",
                      "Estas etapas rodam num modo em que o agente decide sozinho e não pede permissão antes de agir "
                      f"(editar arquivos, rodar comandos):\n\n{lines}\n\nRodar o fluxo assim?",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


class _Bridge(QObject):
    event = Signal(object)
    permission = Signal(int, object, object)


class WorkflowsTab(QWidget):
    def __init__(self, runtime: AsyncRuntime, workspace_fn: Callable[[], str], *,
                 context_fn: Callable[[], str] = lambda: "", store: W.WorkflowStore | None = None,
                 profiles_fn: Callable[[], list[prof.AgentProfile]] = prof.profiles,
                 specs_root: Path | None = None,
                 ask_permission: Callable[[PermissionRequest, QWidget], str | None] = ask_permission_dialog,
                 confirm_modes: Callable[[list[tuple[str, str]], QWidget], bool] | None = None) -> None:
        super().__init__()
        self.confirm_modes = confirm_modes or confirm_risky_stage_modes
        self.rt = runtime
        self.workspace_fn = workspace_fn
        self.context_fn = context_fn
        self.store = store or W.WorkflowStore()
        self.profiles = profiles_fn()
        self.specs_root = specs_root
        self.ask_permission = ask_permission
        self.runner: W.WorkflowRunner | None = None
        self.run_future = None
        self.wf: W.Workflow = W.Workflow.new("Novo fluxo", workspace_fn(), self._default_agent())
        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.permission.connect(self._on_permission)
        self._build()
        self._load_list()
        self._render()

    def _default_agent(self) -> str:
        return next((p.key for p in self.profiles if p.available()[0]), self.profiles[0].key if self.profiles else "")

    # ------------------------------------------------------------------------------------------------- layout
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        row1 = QHBoxLayout()
        self.flow_box = QComboBox()
        self.title = QLineEdit()
        self.new_btn, self.save_btn = QPushButton("Novo fluxo"), QPushButton("Salvar")
        for w in (self.flow_box, self.title):
            w.setStyleSheet(COMBO_CSS)
        for b in (self.new_btn, self.save_btn):
            b.setStyleSheet(BTN_CSS)
        row1.addWidget(self.flow_box, 2)
        row1.addWidget(self.title, 3)
        row1.addWidget(self.new_btn)
        row1.addWidget(self.save_btn)
        lay.addLayout(row1)
        row2 = QHBoxLayout()
        self.spec_box = QComboBox()
        self.spec_box.setStyleSheet(COMBO_CSS)
        for f in W.spec_files(self.specs_root):
            self.spec_box.addItem(f, f)
        self.agent_box = QComboBox()
        self.agent_box.setStyleSheet(COMBO_CSS)
        for p in self.profiles:
            ok, why = p.available()
            self.agent_box.addItem(p.label if ok else f"{p.label} — indisponível", p.key)
        self.auto = QCheckBox("aprovar permissões sozinho (só allow_once)")
        self.auto.setStyleSheet("color:#ffc233")
        self.auto.setToolTip("Desligado por padrão. Ligado, cada pedido de permissão é aprovado uma vez e registrado no log.")
        self.run_btn, self.stop_btn = QPushButton("Executar"), QPushButton("Parar")
        for b in (self.run_btn, self.stop_btn):
            b.setStyleSheet(BTN_CSS)
        self.stop_btn.setEnabled(False)
        for cap, w, s in (("SPEC", self.spec_box, 3), ("Agente", self.agent_box, 2)):
            lb = QLabel(cap)
            lb.setStyleSheet("color:#8f8f98;font-size:11px")
            row2.addWidget(lb)
            row2.addWidget(w, s)
        row2.addWidget(self.auto)
        row2.addWidget(self.run_btn)
        row2.addWidget(self.stop_btn)
        lay.addLayout(row2)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet(TREE_CSS)
        self.tree.setHeaderLabels(["Etapa", "Paralela c/ próxima", "Status", "Resumo"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 100)
        split.addWidget(self.tree)
        mid = QWidget()
        ml = QHBoxLayout(mid)
        ml.setContentsMargins(0, 4, 0, 0)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText("instruções da etapa selecionada")
        self.instructions.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + "}")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + ";font-family:Consolas,monospace;font-size:11px}")
        ml.addWidget(self.instructions, 1)
        ml.addWidget(self.log, 1)
        split.addWidget(mid)
        split.setSizes([230, 330])
        lay.addWidget(split, 1)
        self.status = QLabel("o app nunca faz commit, push nem abre PR: a etapa pr só prepara o texto")
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.status)
        self.new_btn.clicked.connect(self.new_flow)
        self.save_btn.clicked.connect(self.save)
        self.run_btn.clicked.connect(self.run)
        self.stop_btn.clicked.connect(self.stop)
        self.flow_box.activated.connect(self._flow_activated)
        self.tree.currentItemChanged.connect(lambda cur, prev: self._select(cur, prev))
        self.tree.itemChanged.connect(self._item_changed)

    # ------------------------------------------------------------------------------------------------ estado
    def _load_list(self) -> None:
        self.flow_box.blockSignals(True)
        self.flow_box.clear()
        self.flow_box.addItem("(fluxo atual)", "")
        for wf in self.store.load_all():
            if wf.id != self.wf.id:
                self.flow_box.addItem(wf.title, wf.id)
        self.flow_box.blockSignals(False)

    def _render(self) -> None:
        self.title.setText(self.wf.title)
        if self.wf.spec_path:
            i = self.spec_box.findData(self.wf.spec_path)
            if i >= 0:
                self.spec_box.setCurrentIndex(i)
        i = self.agent_box.findData(self.wf.agent)
        if i >= 0:
            self.agent_box.setCurrentIndex(i)
        self.auto.setChecked(self.wf.auto_approve)
        self.tree.blockSignals(True)
        self.tree.clear()
        for idx, st in enumerate(self.wf.stages):
            it = QTreeWidgetItem([f"{st.name} — {st.desc}", "", "", _one_line(st.summary)])
            it.setData(0, Qt.ItemDataRole.UserRole, idx)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(1, Qt.CheckState.Checked if st.parallel_with_next else Qt.CheckState.Unchecked)
            self._paint(it, st.status)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _paint(self, it: QTreeWidgetItem, status: str) -> None:
        from PySide6.QtGui import QBrush, QColor
        it.setText(2, STATUS_LABEL.get(status, status))
        it.setForeground(2, QBrush(QColor(STATUS_COLOR.get(status, "#ccc"))))

    def _select(self, cur: QTreeWidgetItem | None, prev: QTreeWidgetItem | None) -> None:
        if prev is not None:
            self.wf.stages[int(prev.data(0, Qt.ItemDataRole.UserRole))].instructions = self.instructions.toPlainText()
        if cur is not None:
            self.instructions.setPlainText(self.wf.stages[int(cur.data(0, Qt.ItemDataRole.UserRole))].instructions)

    def _item_changed(self, it: QTreeWidgetItem, col: int) -> None:
        if col == 1:
            self.wf.stages[int(it.data(0, Qt.ItemDataRole.UserRole))].parallel_with_next = it.checkState(1) == Qt.CheckState.Checked

    def _collect(self) -> None:
        cur = self.tree.currentItem()
        if cur is not None:
            self.wf.stages[int(cur.data(0, Qt.ItemDataRole.UserRole))].instructions = self.instructions.toPlainText()
        self.wf.title = self.title.text().strip() or self.wf.title
        self.wf.spec_path = str(self.spec_box.currentData() or "")
        self.wf.agent = str(self.agent_box.currentData() or "")
        self.wf.auto_approve = self.auto.isChecked()
        self.wf.workspace = self.workspace_fn() or self.wf.workspace

    def new_flow(self) -> None:
        if self.runner is not None:
            return
        self.wf = W.Workflow.new("Novo fluxo", self.workspace_fn(), self._default_agent())
        self.log.clear()
        self._load_list()
        self._render()

    def save(self) -> None:
        self._collect()
        self.store.save(self.wf)
        self._load_list()
        self.status.setText(f"fluxo salvo: {self.wf.title}")

    def _flow_activated(self, idx: int) -> None:
        fid = self.flow_box.itemData(idx)
        if not fid or self.runner is not None:
            return
        found = next((w for w in self.store.load_all() if w.id == fid), None)
        if found is not None:
            self.wf = found
            self.log.setPlainText("\n".join(f"[{s.name}] {ln}" for s in found.stages for ln in s.log))
            self._load_list()
            self._render()

    # ------------------------------------------------------------------------------------------------ execucao
    def _argv_for(self, st: W.Stage) -> list[str]:
        key = st.agent or self.wf.agent
        p = next((p for p in self.profiles if p.key == key), self.profiles[0])
        return list(p.argv)

    def run(self) -> None:
        if self.runner is not None:
            return
        self._collect()
        if not self.wf.spec_path:
            self.status.setText("escolha uma SPEC do engineering-specs")
            return
        if not self.wf.workspace:
            self.status.setText("escolha um workspace no Chat")
            return
        try:
            spec_text, sha = W.load_spec(self.wf.spec_path, self.specs_root)
        except (OSError, ValueError) as exc:
            self.status.setText(f"SPEC ilegível: {exc}")
            return
        self.wf.spec_sha = sha
        risky = W.risky_stage_modes(self.wf)                                 # H-011: modo que decide sozinho so com sim
        if risky and not self.confirm_modes(risky, self):
            self.status.setText("não iniciado: modo sem pedir permissão recusado")
            self.log.appendPlainText("— não iniciado: " + ", ".join(f"{n} em {m}" for n, m in risky)
                                     + " decide sem pedir permissão e não foi confirmado —")
            return
        for st in self.wf.stages:
            st.status, st.summary, st.log = "pending", "", []
        self._render()
        self.log.appendPlainText(f"— execução: SPEC {self.wf.spec_path} @ {sha or '?'} · workspace {self.wf.workspace} · "
                                 f"auto_approve {'LIGADO' if self.wf.auto_approve else 'desligado'} —")
        context = self.context_fn()
        self.runner = W.WorkflowRunner(self.wf, runtime=self.rt, argv_for=self._argv_for, spec_text=spec_text,
                                       context_for=lambda st: context, on_event=self.bridge.event.emit,
                                       on_permission=lambda i, req, respond: self.bridge.permission.emit(i, req, respond),
                                       stderr_dir=data_dir() / "logs", confirmed_modes=risky)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.run_future = self.runner.start()

    def stop(self):
        if self.runner is None:
            return None
        self.status.setText("parando todas as etapas…")
        return self.runner.stop()

    def _on_event(self, e: dict) -> None:
        if e.get("type") == "stage":
            i = int(e["stage"])
            it = self.tree.topLevelItem(i)
            if it is not None:
                self._paint(it, str(e["status"]))
                it.setText(3, _one_line(self.wf.stages[i].summary))
            self.log.appendPlainText(f"[{self.wf.stages[i].name}] {STATUS_LABEL.get(e['status'], e['status'])}")
        elif e.get("type") == "log":
            self.log.appendPlainText(f"[{self.wf.stages[int(e['stage'])].name}] {e['line']}")
        elif e.get("type") == "workflow":
            self.runner = None
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.store.save(self.wf)
            self._load_list()
            self.status.setText(f"fluxo terminou: {e['status']} · nada foi commitado nem enviado pelo app")

    def _on_permission(self, i: int, req: PermissionRequest, respond) -> None:
        choice = self.ask_permission(req, self)
        respond(choice)
        title = (req.tool_call or {}).get("title", "ferramenta")
        self.log.appendPlainText(f"[{self.wf.stages[i].name}] permissão para “{title}”: {choice or 'recusada'}")

    def shutdown(self) -> None:
        if self.runner is not None:
            try:
                self.runner.stop().result(10)
                if self.run_future is not None:
                    self.run_future.result(10)
            except Exception:                                                  # noqa: BLE001 - encerrando de qualquer jeito
                pass
            self.runner = None
