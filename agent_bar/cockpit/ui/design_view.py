"""design_view.py — "Galeria de design" da Revisão visual (S01C-F1-04): o que uma pessoa precisa para saber, em segundos,
se a SUÍTE 01 está chegando mais perto da referência.

  - três cartões grandes, com miniaturas reais: REFERÊNCIA (a imagem do ChatGPT, sintética, nunca geometria),
    ÚLTIMO RENDER (o quadro V-Ray de maior `started_at`, com geometria, render e nota visual quando houver) e
    MELHOR RENDER (só um run ELEGÍVEL pela regra registrada; sem isso, "Ainda não avaliado" — nunca um render
    inválido para preencher o cartão);
  - o histórico cronológico (mais recente primeiro) com miniaturas grandes, data e hora reais, run, câmera, estado
    técnico e nota visual quando houver;
  - comparar com a referência e com a versão anterior, lado a lado, com o rótulo do que a comparação vale;
  - relatórios, manifests e hashes ficam em "Detalhes técnicos" (botão leva ao item lá).
Tudo vem da galeria (gallery.py); nada aqui inventa imagem, data ou nota.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import gallery as G
from agent_bar.cockpit.ui.github_tabs import BTN_CSS

NOT_EVALUATED = "Ainda não avaliado"
THUMB = QSize(300, 169)
CARD_CSS = "QFrame#card{background:#17171a;border:1px solid #2a2a30;border-radius:6px}"
TITLE_CSS = "color:#e6e6ea;font-size:14px;font-weight:700"
INFO_CSS = "color:#cfcfd4;font-size:12px"
MUTED_CSS = "color:#9a9aa3;font-size:11px"


def thumbnail(path: Path, size: QSize = THUMB) -> QPixmap:
    """Miniatura lida já reduzida (QImageReader), sem carregar o PNG inteiro na tela."""
    reader = QImageReader(str(path))
    full = reader.size()
    if full.isValid():
        reader.setScaledSize(full.scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()


def compare_label(a: G.GalleryItem, b: G.GalleryItem, img_a: G.GalleryImage, img_b: G.GalleryImage) -> str:
    """O que a comparação lado a lado vale (VS-R7, VS-R1a, R38): nunca chama de "mesma câmera" sem prova."""
    if b.source == G.REFERENCE or a.source == G.REFERENCE:
        return ("comparação qualitativa com a referência: câmera diferente (qualitative-different-camera, VS-R7); "
                "a referência é estética e sintética, não geometria")
    if (a.source, b.source) == (G.PUBLISHED, G.PUBLISHED) and a.camera_file and a.camera_file == b.camera_file \
            and img_a.key == img_b.key:
        return f"mesma câmera {img_a.key} no mesmo arquivo de câmeras ({a.camera_file})"
    why = ("run não publicado: câmera não conferida pelo verify.py (VS-R1a)" if G.ENGINE in (a.source, b.source)
           else "arquivo de câmeras ou quadro diferente")
    same = f"mesmo nome de quadro {img_a.key}" if img_a.key == img_b.key else f"quadros {img_a.key} × {img_b.key}"
    return f"lado a lado, {same}; não é par de câmera conferido ({why})"


class Thumb(QLabel):
    """Imagem clicável (amplia)."""
    clicked = Signal()

    def __init__(self, min_size: QSize = THUMB) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(min_size)
        self.setStyleSheet("background:#0e0e11;border:1px solid #2a2a30;color:#ff9a9b")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.path: Path | None = None
        self._pix = QPixmap()

    def set_image(self, path: Path | None, why: str = "") -> None:
        self.path = path
        self._pix = QPixmap(str(path)) if path is not None else QPixmap()
        if self._pix.isNull():
            self.setPixmap(QPixmap())
            self.setText(why or "sem imagem")
        else:
            self.setText("")
            self._fit()

    def _fit(self) -> None:
        if not self._pix.isNull():
            self.setPixmap(self._pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, e) -> None:                                           # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def mousePressEvent(self, e) -> None:                                       # noqa: N802
        if self.path is not None:
            self.clicked.emit()
        super().mousePressEvent(e)


class BigCard(QFrame):
    """Cartão grande: título, miniatura real, linhas de informação e "Ampliar"."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(CARD_CSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.title = QLabel(title)
        self.title.setStyleSheet(TITLE_CSS)
        self.thumb = Thumb(QSize(320, 180))
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet(INFO_CSS)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.enlarge_btn = QPushButton("Ampliar")
        self.enlarge_btn.setStyleSheet(BTN_CSS)
        lay.addWidget(self.title)
        lay.addWidget(self.thumb, 1)
        lay.addWidget(self.info)
        lay.addWidget(self.enlarge_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.item: G.GalleryItem | None = None

    def fill(self, item: G.GalleryItem | None, lines: Sequence[str], empty: str = "") -> None:
        self.item = item
        img = G.primary_image(item) if item is not None else None
        self.thumb.set_image(img.path if img else None, empty or "sem imagem com sha256 conferido")
        self.info.setText("\n".join(lines))
        self.enlarge_btn.setEnabled(img is not None)


class DesignView(QWidget):
    open_technical = Signal(str)                                                # item_id: abrir em Detalhes técnicos

    def __init__(self) -> None:
        super().__init__()
        self.items: list[G.GalleryItem] = []
        self.renders: list[G.GalleryItem] = []
        self.reference: G.GalleryItem | None = None
        self.evaluations_for: Callable[[G.GalleryItem], Sequence] = lambda _i: []
        self.enlarged: QDialog | None = None
        self.compared: tuple[G.GalleryItem, G.GalleryItem] | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        cards = QHBoxLayout()
        self.cards = {"referência": BigCard("REFERÊNCIA"), "último": BigCard("ÚLTIMO RENDER"),
                      "melhor": BigCard("MELHOR RENDER")}
        for c in self.cards.values():
            cards.addWidget(c, 1)
            c.enlarge_btn.clicked.connect(lambda _=False, card=c: self._enlarge_item(card.item))
            c.thumb.clicked.connect(lambda card=c: self._enlarge_item(card.item))
        top = QWidget()
        top.setLayout(cards)
        cards.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 4, 0, 0)
        head = QHBoxLayout()
        self.history_title = QLabel("Histórico de evolução (mais recente primeiro)")
        self.history_title.setStyleSheet(TITLE_CSS)
        head.addWidget(self.history_title, 1)
        self.cmp_ref_btn = QPushButton("Comparar com a referência")
        self.cmp_prev_btn = QPushButton("Comparar com a versão anterior")
        self.enlarge_btn = QPushButton("Ampliar")
        self.tech_btn = QPushButton("Detalhes técnicos")
        for b in (self.cmp_ref_btn, self.cmp_prev_btn, self.enlarge_btn, self.tech_btn):
            b.setStyleSheet(BTN_CSS)
            head.addWidget(b)
        bl.addLayout(head)
        self.history = QListWidget()
        self.history.setViewMode(QListView.ViewMode.IconMode)
        self.history.setIconSize(THUMB)
        self.history.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 118))
        self.history.setWordWrap(True)
        self.history.setResizeMode(QListView.ResizeMode.Adjust)
        self.history.setMovement(QListView.Movement.Static)
        self.history.setStyleSheet("QListWidget{background:#121215;color:#cfcfd4;border:1px solid #2a2a30;"
                                   "font-size:11px}")
        bl.addWidget(self.history, 1)
        self.compare = QWidget()
        cl = QVBoxLayout(self.compare)
        cl.setContentsMargins(0, 4, 0, 0)
        self.compare_label = QLabel("")
        self.compare_label.setWordWrap(True)
        self.compare_label.setStyleSheet(MUTED_CSS)
        panes = QHBoxLayout()
        self.left, self.right = Thumb(QSize(360, 200)), Thumb(QSize(360, 200))
        self.left_cap, self.right_cap = QLabel(""), QLabel("")
        for cap in (self.left_cap, self.right_cap):
            cap.setWordWrap(True)
            cap.setStyleSheet(INFO_CSS)
        for thumb, cap in ((self.left, self.left_cap), (self.right, self.right_cap)):
            col = QVBoxLayout()
            col.addWidget(cap)
            col.addWidget(thumb, 1)
            panes.addLayout(col, 1)
        cl.addWidget(self.compare_label)
        cl.addLayout(panes, 1)
        self.compare.hide()
        bl.addWidget(self.compare, 1)
        split.addWidget(bottom)
        split.setSizes([330, 520])
        lay.addWidget(split, 1)
        self.cmp_ref_btn.clicked.connect(self.compare_with_reference)
        self.cmp_prev_btn.clicked.connect(self.compare_with_previous)
        self.enlarge_btn.clicked.connect(lambda: self._enlarge_item(self.selected()))
        self.tech_btn.clicked.connect(lambda: self.selected() and self.open_technical.emit(self.selected().item_id))
        self.history.currentItemChanged.connect(lambda *_: self._buttons())
        self._buttons()

    # ------------------------------------------------------------------------------------------------ dados
    def set_items(self, items: Sequence[G.GalleryItem],
                  evaluations_for: Callable[[G.GalleryItem], Sequence] = lambda _i: []) -> None:
        self.items = list(items)
        self.evaluations_for = evaluations_for
        self.renders = [i for i in self.items if i.kind == "render"]               # já em ordem: mais recente primeiro
        self.reference = G.reference_item(self.items)
        self._fill_cards()
        self._fill_history()
        self.compare.hide()
        self.compared = None

    def _fill_cards(self) -> None:
        ref = self.reference
        if ref is None:
            self.cards["referência"].fill(None, ["sem referência registrada para o cômodo"], "sem referência")
        else:
            self.cards["referência"].fill(ref, [ref.title or ref.item_id, ref.item_id,
                                                "Referência estética: não é geometria validada.",
                                                ref.provenance_text(), f"curadoria {ref.curation or '—'}"])
        last = G.latest_render(self.renders)
        if last is None:
            self.cards["último"].fill(None, ["nenhum render do cômodo"], "nenhum render")
        else:
            img = G.primary_image(last)
            self.cards["último"].fill(last, [
                f"{last.when()} · {last.item_id}", f"câmera {img.key if img else '—'} · {last.source_label}",
                f"Geometria: {last.build.get('geometria', '—')} · Render: {last.render_verdict or '—'}",
                f"Nota visual: {G.visual_text(self.evaluations_for(last))}"])
        best = G.cards(self.items, self.evaluations_for)[2]
        if best.item is None:                                                     # nunca um inválido para preencher
            self.cards["melhor"].fill(None, [NOT_EVALUATED, best.text], NOT_EVALUATED)
        else:                                                                     # (S01C-F1-07 decide; hoje não ocorre)
            self.cards["melhor"].fill(best.item, [f"{best.item.when()} · {best.item.item_id}", best.text])

    def _fill_history(self) -> None:
        self.history.clear()
        for it in self.renders:
            img = G.primary_image(it)
            text = "\n".join([
                f"{it.when()} · {it.item_id.split('-', 1)[-1]}",
                f"câmera {img.key if img else '—'} · especialista —",
                f"técnico: render {it.render_verdict or '—'} · geometria {it.build.get('geometria', '—')}",
                f"visual: {G.visual_text(self.evaluations_for(it))}",
                f"{it.source_label}{' · FAIL, não elegível' if it.render_verdict == 'FAIL' else ''}",
            ])
            li = QListWidgetItem(text)
            if img is not None:
                pix = thumbnail(img.path)
                if not pix.isNull():
                    li.setIcon(QIcon(pix))
            li.setData(Qt.ItemDataRole.UserRole, it.item_id)
            li.setToolTip(it.item_id)
            self.history.addItem(li)
        self.history_title.setText(f"Histórico de evolução: {len(self.renders)} render(s), mais recente primeiro")
        if self.history.count():
            self.history.setCurrentRow(0)
        self._buttons()

    # ------------------------------------------------------------------------------------------------ ações
    def selected(self) -> G.GalleryItem | None:
        li = self.history.currentItem()
        if li is None:
            return None
        return next((i for i in self.renders if i.item_id == li.data(Qt.ItemDataRole.UserRole)), None)

    def previous_of(self, item: G.GalleryItem) -> G.GalleryItem | None:
        idx = next((n for n, i in enumerate(self.renders) if i.item_id == item.item_id), None)
        rest = self.renders[idx + 1:] if idx is not None else []
        return next((i for i in rest if i.images), None)

    def _buttons(self) -> None:
        it = self.selected()
        ok = it is not None and bool(it.images)
        self.cmp_ref_btn.setEnabled(ok and self.reference is not None and bool(self.reference.images))
        self.cmp_prev_btn.setEnabled(ok and self.previous_of(it) is not None)  # type: ignore[arg-type]
        self.enlarge_btn.setEnabled(ok)
        self.tech_btn.setEnabled(it is not None)

    def compare_with_reference(self) -> None:
        it = self.selected()
        if it is not None and self.reference is not None:
            self._compare(it, self.reference)

    def compare_with_previous(self) -> None:
        it = self.selected()
        prev = self.previous_of(it) if it is not None else None
        if it is not None and prev is not None:
            self._compare(it, prev)

    def _compare(self, a: G.GalleryItem, b: G.GalleryItem) -> None:
        img_a = G.primary_image(a)
        same = next((i for i in b.images if img_a and i.key == img_a.key), None)
        img_b = same or G.primary_image(b)
        if img_a is None or img_b is None:
            return
        self.compared = (a, b)
        self.left_cap.setText(f"{a.when()} · {a.item_id}\ncâmera {img_a.key}")
        self.right_cap.setText(f"{b.when()} · {b.item_id}\n{'referência' if b.source == G.REFERENCE else 'câmera'} "
                               f"{img_b.key}")
        self.left.set_image(img_a.path)
        self.right.set_image(img_b.path)
        self.compare_label.setText(compare_label(a, b, img_a, img_b))
        self.compare.show()

    def _enlarge_item(self, item: G.GalleryItem | None) -> None:
        img = G.primary_image(item) if item is not None else None
        if img is None:
            return
        if self.enlarged is not None:
            self.enlarged.close()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{item.item_id} · {img.key}")
        lay = QVBoxLayout(dlg)
        view = Thumb(QSize(900, 520))
        view.set_image(img.path)
        cap = QLabel(f"{item.item_id} · {img.key} · {item.when()}")
        cap.setStyleSheet(MUTED_CSS)
        lay.addWidget(view, 1)
        lay.addWidget(cap)
        dlg.resize(1280, 800)
        self.enlarged = dlg
        dlg.show()

    def shutdown(self) -> None:
        if self.enlarged is not None:
            self.enlarged.close()
            self.enlarged.deleteLater()
            self.enlarged = None


__all__ = ["NOT_EVALUATED", "BigCard", "DesignView", "compare_label", "thumbnail"]
