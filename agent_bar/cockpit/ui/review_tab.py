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
from agent_bar.cockpit import gallery as G
from agent_bar.cockpit import review_comments as RC
from agent_bar.cockpit.ui.design_view import DesignView
from agent_bar.cockpit.ui.diff_panel import DiffPanel
from agent_bar.cockpit.ui.github_tabs import BTN_CSS, COMBO_CSS, TEXT_CSS, TREE_CSS
from agent_bar.cockpit.ui.human_panel import HumanPanel
from agent_bar.cockpit.ui.packages_panel import PackagesPanel

STATE_COLOR = {"PASS": "#3ddc84", "APPROVED": "#3ddc84", "FAIL": "#ff6b6d", "CHANGES_REQUESTED": "#ff6b6d", "BLOCKED": "#ffc233"}
STATE_KEYS = ("GEOMETRY_PASS", "FUNCTIONAL_PASS", "VISUAL_REVIEW", "HUMAN_APPROVAL")
HISTORY_COLUMNS = ["Data", "Etapa", "Run", "Fonte", "Situação", "Geometria", "Funcional", "Visual", "Tendência",
                   "Humano"]
GALLERY_COLUMNS = ["Data", "Fonte", "Tipo", "Run / referência", "Render", "Geometria", "Funcional", "Construção",
                   "Imagens", "Humano", "Verificado", "Elegível a melhor"]


class _Bridge(QObject):
    loaded = Signal(int, object)
    gallery = Signal(int, object)


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
    request_chat = Signal(str, str)                                             # workspace, pedido (o envio é manual)

    def __init__(self, root_fn: Callable[[], Path] = E.evidence_dir,
                 load_fn: Callable[[Path], E.Catalog] = E.load_catalog,
                 executor: ThreadPoolExecutor | None = None, report_store=None, package_writer=None,
                 gallery_fn: Callable[[E.Catalog | None], G.Gallery] | None = G.build_gallery,
                 comment_store: RC.CommentStore | None = None) -> None:
        super().__init__()
        self.comment_store = comment_store or RC.CommentStore(
            protected=(E.evidence_dir(), G.engine_dir(), G.library_dir()))
        self.last_comment: RC.ReviewComment | None = None
        self.gallery_fn = gallery_fn
        self.evaluations_for: Callable[[G.GalleryItem], list] = lambda _item: []   # S01C-F1-05 preenche
        self.gallery: G.Gallery | None = None
        self.gallery_error = ""
        self.report_store = report_store
        self.package_writer = package_writer
        self.root_fn = root_fn
        self.load_fn = load_fn
        self.pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-review")
        self._own_pool = executor is None
        self.catalog: E.Catalog | None = None
        self.error = ""
        self.req = 0
        self.bridge = _Bridge()
        self.bridge.loaded.connect(self._loaded)
        self.bridge.gallery.connect(self._gallery_loaded)
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
        lb = QLabel("Cômodo")
        lb.setStyleSheet("color:#8f8f98;font-size:12px")
        head.addWidget(lb)
        head.addWidget(self.room_box, 3)
        head.addStretch(4)
        head.addWidget(self.reload_btn)
        lay.addLayout(head)
        # S01C-F1-04: a tela principal é a Galeria de design; o resto fica em Detalhes técnicos
        self.pages = QTabWidget()
        self.design = DesignView()
        self.pages.addTab(self.design, "Galeria de design")
        tech = QWidget()
        lay_tech = QVBoxLayout(tech)
        lay_tech.setContentsMargins(0, 4, 0, 0)
        self.pages.addTab(tech, "Detalhes técnicos")
        lay.addWidget(self.pages, 1)
        pair = QHBoxLayout()
        for cap, w in (("Run A", self.a_box), ("Run B", self.b_box)):
            lbl = QLabel(cap)
            lbl.setStyleSheet("color:#8f8f98;font-size:11px")
            pair.addWidget(lbl)
            pair.addWidget(w, 1)
        lay_tech.addLayout(pair)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        self.source.setStyleSheet("color:#8f8f98;font-size:11px")
        lay_tech.addWidget(self.source)
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
        self.history.setHeaderLabels(HISTORY_COLUMNS)
        self.history.setRootIsDecorated(False)
        self.history.setColumnWidth(0, 150)
        self.history.setColumnWidth(2, 300)
        self.history.setColumnWidth(4, 160)
        self.absent = QTextBrowser()
        self.absent.setStyleSheet(TEXT_CSS)
        self.diff = DiffPanel(self.report_store)
        self.packages = PackagesPanel(self.package_writer)
        self.packages.report_fn = lambda: self.diff.report
        self.human = HumanPanel(lambda: self.catalog.root if self.catalog else self.root_fn())
        self.human.report_fn = lambda: self.diff.report
        self.gallery_view = self._build_gallery()
        for w, title in ((self.gallery_view, "Galeria (todas as fontes)"), (self.states, "Estados (separados)"),
                         (self.reports, "Relatórios técnicos"),
                         (self.history, "Histórico do cômodo"), (self.absent, "Evidências ausentes"),
                         (self.diff, "Relatório de diferenças"), (self.packages, "Pacotes (Claude / GPT)"),
                         (self.human, "Revisão humana")):
            self.details.addTab(w, title)
        vsplit.addWidget(self.details)
        vsplit.setSizes([470, 230])
        lay_tech.addWidget(vsplit, 1)
        self.design.open_technical.connect(self.show_technical)
        self.design.comment_requested.connect(self.comment_to_chat)
        self.reload_btn.clicked.connect(self.reload)
        self.room_box.activated.connect(lambda _i: self._fill_runs())
        self.a_box.activated.connect(lambda _i: self._compare())
        self.b_box.activated.connect(lambda _i: self._compare())
        self.cams.currentItemChanged.connect(lambda cur, _p: self._show_camera(cur))

    def _build_gallery(self) -> QWidget:
        """Galeria multi-fonte (S01C-F1-03): todo item com a fonte; imagem só com sha256 conferido (VS-R2)."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        self.gallery_note = QLabel("")
        self.gallery_note.setWordWrap(True)
        self.gallery_note.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.gallery_note)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.gallery_tree = QTreeWidget()
        self.gallery_tree.setStyleSheet(TREE_CSS)
        self.gallery_tree.setHeaderLabels(GALLERY_COLUMNS)
        self.gallery_tree.setRootIsDecorated(False)
        self.gallery_tree.setColumnWidth(0, 150)
        self.gallery_tree.setColumnWidth(1, 150)
        self.gallery_tree.setColumnWidth(3, 300)
        split.addWidget(self.gallery_tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.gallery_detail = QLabel("")
        self.gallery_detail.setWordWrap(True)
        self.gallery_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.gallery_detail.setStyleSheet("color:#bdbdc4;font-size:11px")
        self.gallery_images = QListWidget()
        self.gallery_images.setStyleSheet("QListWidget{background:#17171a;color:#cfcfd4;border:1px solid #2a2a30;font-size:11px}")
        self.gallery_pane = ImagePane()
        rl.addWidget(self.gallery_detail)
        rl.addWidget(self.gallery_images, 1)
        rl.addWidget(self.gallery_pane, 2)
        split.addWidget(right)
        split.setSizes([700, 420])
        lay.addWidget(split, 1)
        self.gallery_tree.currentItemChanged.connect(lambda cur, _p: self._show_gallery_item(cur))
        self.gallery_images.currentItemChanged.connect(lambda cur, _p: self._show_gallery_image(cur))
        return box

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
            self.history.clear()
            self._clear_pair()                                                # nada do par anterior fica na tela (F0 #43)
            self._load_gallery(rid, None)                                     # o motor e a design-library ainda valem
            return
        self._load_gallery(rid, result)  # type: ignore[arg-type]
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
        self._fill_gallery()
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
            self._clear_pair()
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
        self.diff.set_pair(a, b, pairs + engine)
        self.packages.set_pair(a, b, pairs)
        self.human.set_pair(a, b)
        if self.cams.count():
            self.cams.setCurrentRow(0)
        else:
            self._show_camera(None)
        self.run_selected.emit()

    def _clear_pair(self) -> None:
        """Sem par selecionavel: limpa tudo que descreve um par (estados, relatorios, ausencias, diff, pacotes, humano,
        imagens). Mostrar o veredito de outro dado como se fosse deste e pior que nao mostrar nada."""
        self.cams.clear()
        self.states.clear()
        self.reports.clear()
        self.absent.clear()
        self.diff.set_pair(None, None, [])
        self.packages.set_pair(None, None, [])
        self.human.set_pair(None, None)
        self._show_camera(None)

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

    def _cards(self) -> None:
        self.design.set_items(self.gallery_items(), self.evaluations_for)

    def comment_to_chat(self, text: str) -> None:
        """S01C-F1-11: o comentário vira item R41 (sem triagem) com o run, a câmera e a comparação visíveis, e o Chat
        abre preenchido com esse contexto estruturado. Nada é enviado sozinho."""
        d = self.design
        vis = d.visible()
        if vis is None:
            d.comment_status.setText("escolha um render no histórico antes de comentar")
            return
        item, image, compared = vis
        try:
            c = RC.new_comment(text, item, image, reference=d.reference, compared=compared)
            request = RC.chat_request(c)
            self.comment_store.save(c)
        except (RC.CommentRejected, E.EvidenceError, OSError) as exc:
            d.comment_status.setText(f"comentário não registrado: {exc}")
            return
        self.last_comment = c
        engine = G.engine_dir()
        ws = str(engine) if (engine / ".git").exists() else ""
        d.comment.clear()
        d.comment_status.setText(f"comentário {c.id} registrado (R41, sem triagem) sobre {item.item_id} · câmera "
                                 f"{image.key}. O Chat abriu com o pedido; envie quando quiser.")
        self.request_chat.emit(ws, request)

    def show_technical(self, item_id: str) -> None:
        """Do item da galeria de design para os Detalhes técnicos dele (relatórios, manifest, hashes)."""
        self.pages.setCurrentIndex(1)
        self.details.setCurrentWidget(self.gallery_view)
        t = self.gallery_tree
        row = next((t.topLevelItem(i) for i in range(t.topLevelItemCount())
                    if t.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == item_id), None)
        if row is not None:
            t.setCurrentItem(row)
            t.scrollToItem(row)

    def _history(self) -> None:
        """Histórico do cômodo, mais recente primeiro: runs da galeria (todas as fontes) e, depois, as etapas do evidence
        que não têm run publicado (ausências ditas, nunca omitidas)."""
        self.history.clear()
        room = self.room_box.currentData()
        runs = self.catalog.runs_of(*room) if self.catalog is not None and room is not None else []
        items = [i for i in self.gallery_items() if i.kind != "reference"]
        shown = {i.item_id for i in items}
        for i in items:
            evals = self.evaluations_for(i)
            status = (f"inválido: {len(i.errors)} divergência(s)" if i.errors
                      else f"render {i.render_verdict or 'sem veredito'}" if i.kind == "render" else "build")
            self.history.addTopLevelItem(QTreeWidgetItem([
                i.when(), i.stage or "—", i.item_id, i.source_label, status, i.build.get("geometria", "—"),
                i.build.get("funcional", "—"), G.visual_text(evals), G.trend_text(i, evals), i.human_approval]))
        for r in runs:
            if r.run_id and r.run_id in shown:
                continue
            s = r.states()
            self.history.addTopLevelItem(QTreeWidgetItem([
                "—", r.stage, r.run_id or "—", "publicado" if r.published else "—", r.status(), s["GEOMETRY_PASS"],
                s["FUNCTIONAL_PASS"], G.NO_EVALUATION, "—", s["HUMAN_APPROVAL"]]))

    def _absent(self, a: E.Run, b: E.Run) -> None:
        e = html.escape
        out = []
        for r in (a, b):
            items = r.missing()
            out.append(f"<h4>{e(r.label)}</h4>" + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"
                                                   if items else "<p>nada ausente</p>"))
        self.absent.setHtml("".join(out))

    # ------------------------------------------------------------------------------------------------- galeria
    def _load_gallery(self, rid: int, catalog: E.Catalog | None) -> None:
        if self.gallery_fn is None:
            return
        self.gallery_note.setText("lendo o motor e a design-library (somente leitura)…")
        fut = self.pool.submit(self.gallery_fn, catalog)
        fut.add_done_callback(lambda f, r=rid: self.bridge.gallery.emit(r, f.exception() or f.result()))

    def _gallery_loaded(self, rid: int, result: object) -> None:
        if rid != self.req:
            return
        if isinstance(result, BaseException):
            self.gallery, self.gallery_error = None, f"{type(result).__name__}: {result}"
        else:
            self.gallery, self.gallery_error = result, ""  # type: ignore[assignment]
        if self.catalog is None and self.gallery is not None and self.room_box.count() == 0:
            for plant, room in sorted({(i.plant, i.room) for i in self.gallery.items}):
                self.room_box.addItem(f"{plant} / {room}", (plant, room))
        self._fill_gallery()

    def gallery_items(self) -> list[G.GalleryItem]:
        if self.gallery is None or self.room_box.currentData() is None:
            return []
        return self.gallery.of(*self.room_box.currentData())

    def _fill_gallery(self) -> None:
        from PySide6.QtGui import QBrush, QColor
        self.gallery_tree.clear()
        self._show_gallery_item(None)
        self._history()
        self._cards()
        if self.gallery is None:
            self.gallery_note.setText(f"galeria indisponível: {self.gallery_error}" if self.gallery_error else "")
            return
        items = self.gallery_items()
        counts = {s: sum(1 for i in items if i.source == s) for s in G.SOURCES}
        errs = (f" · {len(self.gallery.errors)} aviso(s): " + "; ".join(self.gallery.errors[:3])
                if self.gallery.errors else "")
        self.gallery_note.setText(
            "Do mais recente para o mais antigo. "
            + " · ".join(f"{G.SOURCE_LABEL[s]}: {n}" for s, n in counts.items() if n)
            + ". Itens do motor: " + G.UNPUBLISHED_NOTE + ". Visível, verificado, elegível e melhor são estados "
            "separados; elegível = candidato a melhor render (F0 §4), não é veredito visual." + errs)
        for i in items:
            st = i.states()                                                    # VISÍVEL sempre; os outros com motivo
            row = QTreeWidgetItem([i.when(), i.source, i.kind, i.item_id, i.render_verdict or "—",
                                   *(i.build.get(k, "—") for k in G.BUILD_KEYS), str(len(i.images)),
                                   i.human_approval, st["VERIFICADO"], st["ELEGÍVEL"]])
            row.setData(0, Qt.ItemDataRole.UserRole, i.item_id)
            for col in (4, 5, 6, 7):
                row.setForeground(col, QBrush(QColor(STATE_COLOR.get(row.text(col), "#cfcfd4"))))
            self.gallery_tree.addTopLevelItem(row)

    def _gallery_item(self, row: QTreeWidgetItem | None) -> G.GalleryItem | None:
        if row is None or self.gallery is None:
            return None
        return self.gallery.get(row.data(0, Qt.ItemDataRole.UserRole))

    def _show_gallery_item(self, row: QTreeWidgetItem | None) -> None:
        self.gallery_images.clear()
        it = self._gallery_item(row)
        if it is None:
            self.gallery_detail.setText("")
            self.gallery_pane.show_image("", None, "selecione um item da galeria")
            return
        st = it.states()
        lines = [f"{it.item_id} · fonte {it.source} ({it.source_label})", *it.notes,
                 *(f"{k}: {v}" for k, v in st.items())]
        if it.camera_file:
            lines.append(f"câmeras/render spec: {it.camera_file}")
        lines += [f"erro: {e}" for e in it.errors[:4]] + [f"imagem recusada: {r}" for r in it.rejected[:6]]
        self.gallery_detail.setText("\n".join(lines))
        for img in it.images:
            li = QListWidgetItem(f"{img.key} — {img.ident}")
            li.setData(Qt.ItemDataRole.UserRole, str(img.path))
            self.gallery_images.addItem(li)
        if self.gallery_images.count():
            self.gallery_images.setCurrentRow(0)
        else:
            self.gallery_pane.show_image(it.item_id, None, "nenhuma imagem com sha256 conferido")

    def _show_gallery_image(self, li: QListWidgetItem | None) -> None:
        if li is None:
            return
        self.gallery_pane.show_image(li.text(), Path(li.data(Qt.ItemDataRole.UserRole)))

    def shutdown(self) -> None:
        self.design.shutdown()
        if self._own_pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
