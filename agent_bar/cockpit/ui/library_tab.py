"""library_tab.py — aba Biblioteca (F4): marcar skills/agentes/regras e ver o texto EXATO que vai junto do prompt."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import library as lib
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, TEXT_CSS, TREE_CSS

SOURCE_LABEL = {"hub": "Hub claude-engineering-skills", "workspace": "Workspace ativo (.claude)", "raiz": "Raiz E:\\Claude (.claude)"}


class _Bridge(QObject):
    loaded = Signal(int, object)


class LibraryTab(QWidget):
    changed = Signal()

    def __init__(self, workspace_fn: Callable[[], str], discover_fn: Callable[[Path | None], lib.Library] = lib.discover,
                 executor: ThreadPoolExecutor | None = None) -> None:
        super().__init__()
        self.workspace_fn = workspace_fn
        self.discover_fn = discover_fn
        self.pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-lib")
        self._own_pool = executor is None
        self.library = lib.Library()
        self.selected: list[str] = []
        self.req = 0
        self.loaded_for: str | None = None
        self.bridge = _Bridge()
        self.bridge.loaded.connect(self._loaded)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.info = QLabel("Marque o que deve ir junto de cada prompt do Chat. Só leitura.")
        self.info.setStyleSheet("color:#8f8f98;font-size:11px")
        self.reload_btn = QPushButton("Recarregar")
        self.clear_btn = QPushButton("Limpar seleção")
        for b in (self.reload_btn, self.clear_btn):
            b.setStyleSheet(BTN_CSS)
        head.addWidget(self.info, 1)
        head.addWidget(self.clear_btn)
        head.addWidget(self.reload_btn)
        lay.addLayout(head)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet(TREE_CSS)
        self.tree.setHeaderLabels(["Item", "Descrição"])
        self.tree.setColumnWidth(0, 260)
        split.addWidget(self.tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.count = QLabel("nada selecionado: o prompt vai sem contexto")
        self.count.setStyleSheet("color:#bdbdc4;font-size:11px")
        rl.addWidget(self.count)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet("QPlainTextEdit{" + TEXT_CSS + ";font-family:Consolas,monospace;font-size:11px}")
        rl.addWidget(self.preview, 1)
        split.addWidget(right)
        split.setSizes([420, 520])
        lay.addWidget(split, 1)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#ffc233;font-size:11px")
        lay.addWidget(self.warn)
        self.reload_btn.clicked.connect(self.reload)
        self.clear_btn.clicked.connect(self.clear)
        self.tree.itemChanged.connect(lambda _it, _col: self._sync_selection())

    # ------------------------------------------------------------------------------------------------- carga
    def ensure_loaded(self) -> None:
        if self.loaded_for != self.workspace_fn():
            self.reload()

    def reload(self) -> None:
        ws = self.workspace_fn()
        self.req += 1
        rid = self.req
        self.info.setText(f"lendo a biblioteca para {Path(ws).name if ws else '(sem workspace)'}…")
        fut = self.pool.submit(self.discover_fn, Path(ws) if ws else None)
        fut.add_done_callback(lambda f, r=rid, w=ws: self.bridge.loaded.emit(r, (w, f.result() if not f.exception() else f.exception())))

    def _loaded(self, rid: int, payload: object) -> None:
        if rid != self.req:
            return
        ws, result = payload  # type: ignore[misc]
        if isinstance(result, BaseException):
            self.info.setText(f"falha ao ler a biblioteca: {result}")
            return
        self.library = result
        self.loaded_for = ws
        keep = set(self.selected)
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for it in self.library.items:
            g = groups.get(it.source)
            if g is None:
                sha = f" @ {it.sha[:7]}" if it.sha else ""
                g = QTreeWidgetItem([SOURCE_LABEL.get(it.source, it.source) + sha, ""])
                g.setFlags(g.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                groups[it.source] = g
                self.tree.addTopLevelItem(g)
            row = QTreeWidgetItem([f"[{it.kind}] {it.name}", it.description[:200]])
            row.setToolTip(1, it.description)
            row.setData(0, Qt.ItemDataRole.UserRole, it.key)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked if it.key in keep else Qt.CheckState.Unchecked)
            g.addChild(row)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.warn.setText("\n".join(f"aviso: {w}" for w in self.library.warnings[:8]))
        self.info.setText(f"{len(self.library.items)} item(ns) · workspace {Path(ws).name if ws else '—'}")
        self._sync_selection()

    # --------------------------------------------------------------------------------------------- selecao
    def _rows(self):
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                yield g.child(j)

    def _sync_selection(self) -> None:
        self.selected = [str(r.data(0, Qt.ItemDataRole.UserRole)) for r in self._rows()
                         if r.checkState(0) == Qt.CheckState.Checked]
        text = self.context()
        self.preview.setPlainText(text)
        n = len(self.selected)
        self.count.setText(f"{n} item(ns) · {len(text)} de {lib.CONTEXT_LIMIT} caracteres"
                           + (" · TRUNCADO" if "[contexto truncado" in text else "") if n else
                           "nada selecionado: o prompt vai sem contexto")
        self.changed.emit()

    def select_keys(self, keys: list[str]) -> None:
        wanted = set(keys)
        self.tree.blockSignals(True)
        for r in self._rows():
            r.setCheckState(0, Qt.CheckState.Checked if r.data(0, Qt.ItemDataRole.UserRole) in wanted else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._sync_selection()

    def clear(self) -> None:
        self.select_keys([])

    def context(self) -> str:
        """O texto exato que o Chat anexa ao proximo prompt (mesma funcao do preview)."""
        return lib.build_context(self.library.by_key(self.selected))

    def shutdown(self) -> None:
        if self._own_pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
