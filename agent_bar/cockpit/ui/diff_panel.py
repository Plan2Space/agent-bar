"""diff_panel.py — painel "Relatório de diferenças" da aba Revisão visual (F7.3)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import review_report as RR
from agent_bar.cockpit.evidence import Run
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, COMBO_CSS, TREE_CSS


class DiffPanel(QWidget):
    saved = Signal(str)

    def __init__(self, store: RR.ReportStore | None = None) -> None:
        super().__init__()
        self.store = store or RR.ReportStore()
        self.report: RR.DiffReport | None = None
        self.a: Run | None = None
        self.b: Run | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.header = QLabel("selecione dois runs válidos")
        self.header.setWordWrap(True)
        self.header.setStyleSheet("color:#bdbdc4;font-size:11px")
        lay.addWidget(self.header)
        form = QGridLayout()
        self.category, self.nature, self.fix, self.camera, self.source = (QComboBox() for _ in range(5))
        for k, v in RR.CATEGORIES.items():
            self.category.addItem(v, k)
        for k, v in RR.NATURES.items():
            self.nature.addItem(v, k)
        for k, v in RR.FIXES.items():
            self.fix.addItem(v, k)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("diferença observável (o que se vê, onde)")
        self.value = QDoubleSpinBox()
        self.value.setDecimals(4)
        self.value.setRange(-1e6, 1e6)
        self.unit = QLineEdit()
        self.unit.setPlaceholderText("unidade (m, m², °)")
        self.use_measure = QComboBox()
        self.use_measure.addItem("sem medida", False)
        self.use_measure.addItem("com medida (fonte obrigatória)", True)
        for w in (self.category, self.nature, self.fix, self.camera, self.source, self.desc, self.unit, self.use_measure):
            w.setStyleSheet(COMBO_CSS)
        cells = [("Categoria", self.category), ("Natureza", self.nature), ("Correção", self.fix), ("Câmera", self.camera),
                 ("Descrição", self.desc), ("Medida", self.use_measure), ("Valor", self.value), ("Unidade", self.unit),
                 ("Fonte", self.source)]
        for i, (cap, w) in enumerate(cells):
            lb = QLabel(cap)
            lb.setStyleSheet("color:#8f8f98;font-size:11px")
            form.addWidget(lb, i // 3, (i % 3) * 2)
            form.addWidget(w, i // 3, (i % 3) * 2 + 1)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.add_btn, self.remove_btn, self.save_btn = QPushButton("Adicionar achado"), QPushButton("Remover"), QPushButton("Salvar relatório")
        for b in (self.add_btn, self.remove_btn, self.save_btn):
            b.setStyleSheet(BTN_CSS)
            row.addWidget(b)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        self.table = QTreeWidget()
        self.table.setStyleSheet(TREE_CSS)
        self.table.setHeaderLabels(["Natureza", "Categoria", "Câmera", "Descrição", "Medida (fonte)", "Correção"])
        self.table.setRootIsDecorated(False)
        self.table.setColumnWidth(3, 340)
        lay.addWidget(self.table, 1)
        self.add_btn.clicked.connect(self.add_from_form)
        self.remove_btn.clicked.connect(self.remove_selected)
        self.save_btn.clicked.connect(self.save)
        self.category.currentIndexChanged.connect(self._category_changed)
        self._enable(False)

    def _enable(self, on: bool) -> None:
        for w in (self.add_btn, self.remove_btn, self.save_btn):
            w.setEnabled(on)

    def _category_changed(self, _i: int) -> None:
        if self.category.currentData() == "geometria_medida":                  # medida geométrica é falha física e exige fonte
            self.nature.setCurrentIndex(self.nature.findData("fisico"))
            self.use_measure.setCurrentIndex(1)

    def set_pair(self, a: Run | None, b: Run | None, cameras: list[str]) -> None:
        self.a, self.b = a, b
        self.camera.clear()
        self.camera.addItem("(sem câmera)", "")
        for c in cameras:
            self.camera.addItem(c, c)
        self.source.clear()
        if a is None or b is None or not (a.valid and b.valid):
            self.report = None
            self.header.setText("relatório só entre dois runs válidos (verify.py)")
            self._enable(False)
            self._render()
            return
        for side, r in (("A", a), ("B", b)):
            for path, sha in RR.measure_sources(r):
                self.source.addItem(f"{side} · {r.stage} · {path} · {sha[:12]}…", (side, path, sha))
        self.report = self.store.latest(a, b) or RR.DiffReport.new(a, b)
        self.header.setText(f"{a.stage} ({a.run_id}) × {b.stage} ({b.run_id}) · relatório {self.report.id} · "
                            "diferenças observáveis, sem similaridade percentual")
        self._enable(True)
        self._render()

    def add_from_form(self) -> list[str]:
        if self.report is None:
            return ["sem par de runs válido"]
        measure = None
        if self.use_measure.currentData():
            src = self.source.currentData()
            side, path, sha = src if src else ("B", "", "")
            measure = RR.Measure(self.value.value(), self.unit.text().strip(), path, sha, side)
        f = RR.Finding(str(self.category.currentData()), str(self.nature.currentData()), self.desc.text(),
                       str(self.camera.currentData() or ""), str(self.fix.currentData()), measure)
        errs = self.report.add(f)
        if errs:
            self.status.setText("<span style='color:#ff9a9b'>recusado: " + "; ".join(errs) + "</span>")
            self.status.setTextFormat(Qt.TextFormat.RichText)
        else:
            self.desc.clear()
            self._render()
        return errs

    def remove_selected(self) -> None:
        it = self.table.currentItem()
        if self.report is not None and it is not None:
            del self.report.findings[self.table.indexOfTopLevelItem(it)]
            self._render()

    def save(self) -> str:
        if self.report is None:
            return ""
        try:
            path = self.store.save(self.report)
        except ValueError as exc:
            self.status.setText(f"não salvo: {exc}")
            return ""
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setText(f"salvo: {path}")
        self.saved.emit(str(path))
        return str(path)

    def _render(self) -> None:
        self.table.clear()
        rep = self.report
        if rep is None:
            self.status.setText("")
            return
        for f in rep.findings:
            m = f.measure
            meas = f"{m.value:g} {m.unit} ({m.run}: {m.source_path} {m.source_sha256[:10]}…)" if m else "—"
            self.table.addTopLevelItem(QTreeWidgetItem([RR.NATURES[f.nature], RR.CATEGORIES[f.category], f.camera or "—",
                                                        f.description, meas, RR.FIXES[f.fix]]))
        c = rep.counts()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setText(f"{c['fisico']} físico(s) · {c['estetico']} estético(s) — falha física vence nota estética")
