"""review_tab.py — aba "Revisão visual" (F7.2): dois runs reais do evidence lado a lado por câmera compatível.

Tudo vem do leitor (`cockpit/evidence.py`, validado pelo verify.py do checkout). Nada é inventado: run inválido ou
ausente não mostra imagem; câmera incompatível aparece com o motivo; a única medida de imagem é a fração de pixels do
compare/ do run (não é veredito). Os 4 estados ficam separados e HUMAN_APPROVAL é PENDING_HUMAN sem human.json válido.
"""
from __future__ import annotations

import html
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import evidence as E
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, COMBO_CSS, TEXT_CSS, TREE_CSS

STATE_COLOR = {"PASS": "#3ddc84", "APPROVED": "#3ddc84", "FAIL": "#ff6b6d", "CHANGES_REQUESTED": "#ff6b6d", "BLOCKED": "#ffc233"}
STATE_KEYS = ("GEOMETRY_PASS", "FUNCTIONAL_PASS", "VISUAL_REVIEW", "HUMAN_APPROVAL")


class _Bridge(QObject):
    loaded = Signal(int, object)


class ImagePane(QWidget):
    """Uma imagem do run, com legenda. Sem imagem: diz por quê (nunca um placeholder que pareça render)."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet("color:#bdbdc4;font-size:11px")
        self.image = QLabel("")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(320, 200)
        self.image.setStyleSheet("background:#0e0e11;border:1px solid #2a2a30;color:#ff9a9b")
        lay.addWidget(self.caption)
        lay.addWidget(self.image, 1)
        self.path: Path | None = None
        self._pix: QPixmap | None = None

    def show_image(self, caption: str, path: Path | None, why_missing: str = "") -> None:
        self.caption.setText(caption)
        self.path = path
        if path is None:
            self._pix = None
            self.image.setPixmap(QPixmap())
            self.image.setText(why_missing or "sem imagem")
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            self._pix = None
            self.image.setPixmap(QPixmap())
            self.image.setText(f"imagem ilegível: {path.name}")
            return
        self._pix = pix
        self.image.setText("")
        self._fit()

    def _fit(self) -> None:
        if self._pix is not None:
            self.image.setPixmap(self._pix.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e) -> None:                                           # noqa: N802
        super().resizeEvent(e)
        self._fit()


class ReviewTab(QWidget):
    run_selected = Signal()                                                     # (a, b) mudou: relatório/pacotes ouvem

    def __init__(self, root_fn: Callable[[], Path] = E.evidence_dir,
                 load_fn: Callable[[Path], E.Catalog] = E.load_catalog,
                 executor: ThreadPoolExecutor | None = None) -> None:
        super().__init__()
        self.root_fn = root_fn
        self.load_fn = load_fn
        self.pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-review")
        self._own_pool = executor is None
        self.catalog: E.Catalog | None = None
        self.error = ""
        self.req = 0
        self.bridge = _Bridge()
        self.bridge.loaded.connect(self._loaded)
        self._build()

    # ------------------------------------------------------------------------------------------------- layout
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.room_box, self.a_box, self.b_box = QComboBox(), QComboBox(), QComboBox()
        for w in (self.room_box, self.a_box, self.b_box):
            w.setStyleSheet(COMBO_CSS)
        self.reload_btn = QPushButton("Recarregar")
        self.reload_btn.setStyleSheet(BTN_CSS)
        for cap, w, s in (("Cômodo", self.room_box, 2), ("Run A", self.a_box, 3), ("Run B", self.b_box, 3)):
            lb = QLabel(cap)
            lb.setStyleSheet("color:#8f8f98;font-size:11px")
            head.addWidget(lb)
            head.addWidget(w, s)
        head.addWidget(self.reload_btn)
        lay.addLayout(head)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        self.source.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.source)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        cap = QLabel("Câmeras (compatíveis primeiro) e pranchas")
        cap.setStyleSheet("color:#bdbdc4;font-size:11px")
        ll.addWidget(cap)
        self.cams = QListWidget()
        self.cams.setStyleSheet("QListWidget{background:#17171a;color:#cfcfd4;border:1px solid #2a2a30;font-size:12px}")
        ll.addWidget(self.cams, 1)
        split.addWidget(left)
        right = QWidget()
        grid = QGridLayout(right)
        grid.setContentsMargins(0, 0, 0, 0)
        self.pane_a, self.pane_b = ImagePane(), ImagePane()
        grid.addWidget(self.pane_a, 0, 0)
        grid.addWidget(self.pane_b, 0, 1)
        self.measure = QLabel("")
        self.measure.setWordWrap(True)
        self.measure.setStyleSheet("color:#8f8f98;font-size:11px")
        grid.addWidget(self.measure, 1, 0, 1, 2)
        split.addWidget(right)
        split.setSizes([240, 900])
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(split)
        self.details = QTabWidget()
        self.states = QTreeWidget()
        self.states.setStyleSheet(TREE_CSS)
        self.states.setHeaderLabels(["Estado", "Run A", "Run B"])
        self.states.setRootIsDecorated(False)
        self.states.setColumnWidth(0, 160)
        self.states.setColumnWidth(1, 360)
        self.reports = QTextBrowser()
        self.reports.setStyleSheet(TEXT_CSS)
        self.history = QTreeWidget()
        self.history.setStyleSheet(TREE_CSS)
        self.history.setHeaderLabels(["Etapa", "Run", "Situação", "Geometria", "Funcional", "Revisão LLM", "Humano"])
        self.history.setRootIsDecorated(False)
        self.history.setColumnWidth(1, 280)
        self.history.setColumnWidth(2, 180)
        self.absent = QTextBrowser()
        self.absent.setStyleSheet(TEXT_CSS)
        for w, title in ((self.states, "Estados (separados)"), (self.reports, "Relatórios técnicos"),
                         (self.history, "Histórico do cômodo"), (self.absent, "Evidências ausentes")):
            self.details.addTab(w, title)
        vsplit.addWidget(self.details)
        vsplit.setSizes([470, 230])
        lay.addWidget(vsplit, 1)
        self.reload_btn.clicked.connect(self.reload)
        self.room_box.activated.connect(lambda _i: self._fill_runs())
        self.a_box.activated.connect(lambda _i: self._compare())
        self.b_box.activated.connect(lambda _i: self._compare())
        self.cams.currentItemChanged.connect(lambda cur, _p: self._show_camera(cur))

    # ------------------------------------------------------------------------------------------------- carga
    def reload(self) -> None:
        root = self.root_fn()
        self.req += 1
        rid = self.req
        self.source.setText(f"lendo {root} (validação pelo verify.py do checkout)…")
        fut = self.pool.submit(self.load_fn, root)
        fut.add_done_callback(lambda f, r=rid: self.bridge.loaded.emit(r, f.exception() or f.result()))

    def _loaded(self, rid: int, result: object) -> None:
        if rid != self.req:
            return
        if isinstance(result, BaseException):
            self.catalog, self.error = None, str(result)
            self.source.setText(f"<span style='color:#ff9a9b'>evidence indisponível: {html.escape(self.error)}</span>")
            self.source.setTextFormat(Qt.TextFormat.RichText)
            for box in (self.room_box, self.a_box, self.b_box):
                box.clear()
            self.cams.clear()
            return
        self.catalog, self.error = result, ""  # type: ignore[assignment]
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        n_ok = sum(1 for r in self.catalog.runs if r.valid)
        repo_err = f" · {len(self.catalog.errors)} divergência(s) no índice" if self.catalog.errors else ""
        self.source.setText(f"evidence: {self.catalog.root} · {n_ok} run(s) válidos de {len(self.catalog.runs)}{repo_err} "
                            "· somente leitura · contrato: tools/verify.py do checkout")
        self.room_box.clear()
        for plant, room in self.catalog.rooms():
            self.room_box.addItem(f"{plant} / {room}", (plant, room))
        self._fill_runs()

    def _fill_runs(self) -> None:
        if self.catalog is None or self.room_box.currentData() is None:
            return
        plant, room = self.room_box.currentData()
        runs = self.catalog.runs_of(plant, room)
        for box in (self.a_box, self.b_box):
            box.clear()
            for r in runs:
                box.addItem(f"{r.label} — {r.status()}", (r.stage, r.run_id))
        valid = [i for i, r in enumerate(runs) if r.valid]
        if len(valid) >= 2:                                                     # padrão: os dois últimos runs válidos
            self.a_box.setCurrentIndex(valid[-2])
            self.b_box.setCurrentIndex(valid[-1])
        self._history(runs)
        self._compare()

    def run(self, box: QComboBox) -> E.Run | None:
        if self.catalog is None or box.currentData() is None or self.room_box.currentData() is None:
            return None
        plant, room = self.room_box.currentData()
        stage, run_id = box.currentData()
        return self.catalog.get(plant, room, stage, run_id)

    def selected_runs(self) -> tuple[E.Run | None, E.Run | None]:
        return self.run(self.a_box), self.run(self.b_box)

    # ---------------------------------------------------------------------------------------------- comparação
    def _compare(self) -> None:
        a, b = self.selected_runs()
        self.cams.clear()
        if a is None or b is None:
            return
        pairs, refused = E.compatible_cameras(a, b)
        for name in pairs:
            it = QListWidgetItem(f"● {name}")
            it.setData(Qt.ItemDataRole.UserRole, ("camera", name))
            self.cams.addItem(it)
        for name, why in refused:
            it = QListWidgetItem(f"⊘ {name} — {why}")
            it.setData(Qt.ItemDataRole.UserRole, ("refused", name, why))
            it.setForeground(Qt.GlobalColor.gray)
            self.cams.addItem(it)
        engine = sorted({v["name"] for r in (a, b) if r.valid for v in r.views("engine")})
        for name in engine:
            it = QListWidgetItem(f"▣ {name} (prancha do motor)")
            it.setData(Qt.ItemDataRole.UserRole, ("engine", name))
            self.cams.addItem(it)
        self._states(a, b)
        self._reports(a, b)
        self._absent(a, b)
        if self.cams.count():
            self.cams.setCurrentRow(0)
        else:
            self._show_camera(None)
        self.run_selected.emit()

    def _show_camera(self, item: QListWidgetItem | None) -> None:
        a, b = self.selected_runs()
        if item is None or a is None or b is None:
            self.pane_a.show_image("", None, "selecione dois runs")
            self.pane_b.show_image("", None, "selecione dois runs")
            self.measure.setText("")
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        kind, name = data[0], data[1]
        for pane, r in ((self.pane_a, a), (self.pane_b, b)):
            if not r.valid:
                pane.show_image(f"{r.label}", None, f"sem imagem: {r.status()}")
                continue
            path = r.view_path(name)
            angle = next((v.get("angle", "") for v in r.views() if v.get("name") == name), "")
            pane.show_image(f"{r.stage} · {name} · {angle}", path, f"{name}: ausente neste run")
        if kind == "refused":
            self.measure.setText(f"comparação recusada para {name}: {data[2]}. As imagens aparecem, mas não são pares.")
        elif kind == "engine":
            self.measure.setText("prancha do motor (source: engine): mostrada lado a lado, não é câmera fixa.")
        else:
            frac = b.pixel_fraction(name, a)
            frac = a.pixel_fraction(name, b) if frac is None else frac
            self.measure.setText(f"mesma câmera e enquadramento ({a.cameras_rel} × {b.cameras_rel}). "
                                 + (f"Fração de pixels alterados (compare.py do evidence): {frac:.2%} — medida de imagem, não veredito."
                                    if frac is not None else "Sem compare/ registrado entre estes dois runs."))

    # ------------------------------------------------------------------------------------------------ painéis
    def _states(self, a: E.Run, b: E.Run) -> None:
        from PySide6.QtGui import QBrush, QColor
        self.states.clear()
        sa, sb = a.states(), b.states()
        for k in STATE_KEYS:
            it = QTreeWidgetItem([k, sa[k], sb[k]])
            for col, val in ((1, sa[k]), (2, sb[k])):
                it.setForeground(col, QBrush(QColor(STATE_COLOR.get(val, "#cfcfd4"))))
            self.states.addTopLevelItem(it)
        note = QTreeWidgetItem(["", "HUMAN_APPROVAL só com verdicts/human.json aceito pelo verify.py; autoria conferida no CI do evidence.",
                                "O cockpit nunca escreve veredito."])
        note.setForeground(1, QBrush(QColor("#8f8f98")))
        note.setForeground(2, QBrush(QColor("#8f8f98")))
        self.states.addTopLevelItem(note)

    @staticmethod
    def _verdict_html(r: E.Run) -> str:
        e = html.escape
        if not r.present:
            return f"<h4>{e(r.label)}</h4><p style='color:#ff9a9b'>{e(r.status())}</p>"
        parts = [f"<h4>{e(r.label)} — {e(r.status())}</h4>"]
        if r.errors:
            parts.append("<p style='color:#ff9a9b'>" + "<br>".join(e(x) for x in r.errors[:12]) + "</p>")
        for kind in ("geometry", "functional", "tests"):
            v = r.verdict(kind)
            if v is None:
                parts.append(f"<p><b>{kind}</b>: ausente</p>")
                continue
            failed = v.get("failed") or []
            src = v.get("source") or {}
            parts.append(f"<p><b>{kind}</b>: {e(str(v.get('verdict')))} · {e(str(v.get('checks_total', '?')))} checagens · "
                         f"{len(failed)} falha(s) · por {e(str(v.get('by', '?')))}"
                         + (f"<br><span style='color:#8f8f98'>fonte {e(str(src.get('path', '')))} sha256 {e(str(src.get('sha256', ''))[:16])}…</span>"
                            if src else "")
                         + ("<br>" + e(json.dumps(failed, ensure_ascii=False)[:600]) if failed else "") + "</p>")
        reviews = r.llm_reviews()
        if reviews:
            parts.append("<p><b>reviews/llm (consultivo)</b>: " + ", ".join(e(p.name) for p in reviews) + "</p>")
        return "".join(parts)

    def _reports(self, a: E.Run, b: E.Run) -> None:
        self.reports.setHtml(self._verdict_html(a) + "<hr>" + self._verdict_html(b))

    def _history(self, runs: list[E.Run]) -> None:
        self.history.clear()
        for r in runs:
            s = r.states()
            self.history.addTopLevelItem(QTreeWidgetItem([r.stage, r.run_id or "—", r.status(), s["GEOMETRY_PASS"],
                                                          s["FUNCTIONAL_PASS"], s["VISUAL_REVIEW"], s["HUMAN_APPROVAL"]]))

    def _absent(self, a: E.Run, b: E.Run) -> None:
        e = html.escape
        out = []
        for r in (a, b):
            items = r.missing()
            out.append(f"<h4>{e(r.label)}</h4>" + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"
                                                   if items else "<p>nada ausente</p>"))
        self.absent.setHtml("".join(out))

    def shutdown(self) -> None:
        if self._own_pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
