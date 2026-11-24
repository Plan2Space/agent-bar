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
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import evaluations as V
from agent_bar.cockpit import gallery as G
from agent_bar.cockpit.ui.github_tabs import BTN_CSS

NOT_EVALUATED = "Ainda não avaliado"
THUMB = QSize(300, 169)
# Área MÍNIMA da miniatura nos cartões (#102): a imagem cabe nela mantendo a proporção (16:10 → 216×135).
CARD_THUMB = QSize(240, 135)
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


def tech_text(item: G.GalleryItem) -> str:
    """Estado técnico para a tela principal: só o que foi VERIFICADO; senão "não verificado" com o motivo (nunca o
    PASS do manifest de um run cujas provas não fecham)."""
    gaps = item.verification_gaps()
    if gaps:
        return f"não verificado: {gaps[0]}"
    return f"render {item.render_verdict or '—'} · geometria {item.build.get('geometria', '—')}"


def source_text(item: G.GalleryItem) -> str:
    """Fonte do item; run do motor leva sempre o texto do VS-R1a."""
    if item.source == G.ENGINE:
        return f"motor: {G.publication_note(item)}"
    if item.source == G.REFERENCE:
        return f"referência: {item.provenance_text()}"
    return item.source_label


def best_headline(reason: str) -> str:
    """Título do cartão MELHOR conforme o motivo real (sem avaliação ≠ nenhum elegível ≠ regra pendente)."""
    if reason == G.NO_EVALUATION:
        return NOT_EVALUATED
    if reason.startswith("nenhum render elegível"):
        return "Nenhum render elegível"
    return "Melhor ainda não escolhido"


def compare_label(a: G.GalleryItem, b: G.GalleryItem, img_a: G.GalleryImage, img_b: G.GalleryImage) -> str:
    """O que a comparação lado a lado vale (VS-R7, VS-R1a, R38): nunca chama de "mesma câmera" sem prova."""
    if b.source == G.REFERENCE or a.source == G.REFERENCE:
        return ("comparação qualitativa com a referência: câmera diferente (qualitative-different-camera, VS-R7); "
                "a referência é estética e sintética, não geometria")
    if (a.source, b.source) == (G.PUBLISHED, G.PUBLISHED) and a.camera_file and a.camera_file == b.camera_file \
            and img_a.key == img_b.key:
        return f"mesma câmera {img_a.key} no mesmo arquivo de câmeras ({a.camera_file})"
    engine = [i for i in (a, b) if i.source == G.ENGINE]
    if any(not i.publication_checked for i in engine):                          # #105: sem o evidence não dá para saber
        why = f"{G.UNCHECKED_NOTE}: câmera não conferida pelo verify.py (VS-R1a)"
    elif engine:
        why = "run não publicado: câmera não conferida pelo verify.py (VS-R1a)"
    else:
        why = "arquivo de câmeras ou quadro diferente"
    same = f"mesmo nome de quadro {img_a.key}" if img_a.key == img_b.key else f"quadros {img_a.key} × {img_b.key}"
    return f"lado a lado, {same}; não é par de câmera conferido ({why})"


def visual_line(evaluations) -> str:
    """Nota visual na tela principal: a avaliação mais recente por critério (1–5, sem soma), ou "sem avaliação".
    Avaliação é opinião consultiva: nunca vira MELHOR por si (S01C-F1-07)."""
    evs = [e for e in evaluations if isinstance(e, dict) and e.get("scores")]
    if not evs:
        return G.visual_text(evaluations)
    latest = max(evs, key=lambda e: str(e.get("created_at") or ""))
    more = f" (+{len(evs) - 1} anterior(es))" if len(evs) > 1 else ""
    return V.summary(latest) + more


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
        self.thumb = Thumb(CARD_THUMB)                                          # cede altura para a legenda
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
        self._fit_info()

    def _fit_info(self) -> None:
        """A legenda quebra em várias linhas e o QLabel informa ao layout o mínimo de uma linha só: o cartão ficava
        menor que imagem + legenda e a legenda era desenhada por cima da imagem. O mínimo passa a ser a altura real."""
        width = self.info.width() if self.info.width() > 50 else max(self.width() - 20, 200)
        need = self.info.heightForWidth(width)
        if need > 0 and self.info.minimumHeight() != need:
            self.info.setMinimumHeight(need)

    def resizeEvent(self, e) -> None:                                           # noqa: N802
        super().resizeEvent(e)
        self._fit_info()


class DesignView(QWidget):
    open_technical = Signal(str)                                                # item_id: abrir em Detalhes técnicos
    comment_requested = Signal(str)                                             # texto: comentar o que está na tela

    def __init__(self) -> None:
        super().__init__()
        self.items: list[G.GalleryItem] = []
        self.renders: list[G.GalleryItem] = []
        self.reference: G.GalleryItem | None = None
        self.evaluations_for: Callable[[G.GalleryItem], Sequence] = lambda _i: []
        self.enlarged: QDialog | None = None
        self.compared: tuple[G.GalleryItem, G.GalleryItem] | None = None
        self.compared_images: tuple[G.GalleryImage, G.GalleryImage] | None = None
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
        row = QHBoxLayout()                                                       # S01C-F1-11: comentário → Chat
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("Comente o que está na tela (ex.: gostei mais da iluminação anterior, mas quero "
                                        "o edredom mais natural)")
        self.comment.setStyleSheet("QLineEdit{background:#17171a;color:#e6e6ea;border:1px solid #2a2a30;padding:6px;"
                                   "font-size:12px}")
        self.comment_btn = QPushButton("Levar ao Chat (sem enviar)")
        self.comment_btn.setStyleSheet(BTN_CSS)
        row.addWidget(self.comment, 1)
        row.addWidget(self.comment_btn)
        bl.addLayout(row)
        self.comment_status = QLabel("")
        self.comment_status.setWordWrap(True)
        self.comment_status.setStyleSheet(MUTED_CSS)
        bl.addWidget(self.comment_status)
        self.comment_btn.clicked.connect(lambda: self.comment_requested.emit(self.comment.text()))
        self.comment.returnPressed.connect(lambda: self.comment_requested.emit(self.comment.text()))
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
        self.compared_images = None

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
            state = (f"Geometria: {last.build.get('geometria', '—')} · Render: {last.render_verdict or '—'}"
                     if last.verified else f"Estado técnico: {tech_text(last)}")
            self.cards["último"].fill(last, [
                f"{last.when()} · {last.item_id}", G.camera_text(img), source_text(last), state,
                f"Nota visual: {visual_line(self.evaluations_for(last))}"])
        best = G.cards(self.items, self.evaluations_for)[2]
        if best.item is not None and not best.item.eligible(self.evaluations_for(best.item)):
            best = G.Card(best.role, None, "nenhum render elegível: candidato inelegível recusado")
        if best.item is None:                                                     # nunca um inválido para preencher
            head = best_headline(best.text)
            self.cards["melhor"].fill(None, [head, best.text], head)
        else:                                                                     # (S01C-F1-07 decide; hoje não ocorre)
            self.cards["melhor"].fill(best.item, [f"{best.item.when()} · {best.item.item_id}", best.text])

    def _fill_history(self) -> None:
        self.history.clear()
        for it in self.renders:
            img = G.primary_image(it)
            mark = (" · FAIL, não elegível" if it.verified and it.render_verdict == "FAIL"
                    else " · não verificado, não elegível" if not it.verified else "")
            text = "\n".join([
                f"{it.when()} · {it.item_id.split('-', 1)[-1]}",
                f"{G.camera_text(img)} · especialista —",
                f"técnico: {tech_text(it)}",
                f"visual: {visual_line(self.evaluations_for(it))}",
                f"{source_text(it)}{mark}",
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
        img_a, img_b, note = G.comparison_pair(a, b)                           # mesma câmera dos dois lados, se houver
        if img_a is None or img_b is None:
            return
        self.compared = (a, b)
        self.compared_images = (img_a, img_b)
        self.left_cap.setText(f"{a.when()} · {a.item_id}\n{G.camera_text(img_a)} · {source_text(a)}")
        right = f"referência {img_b.key}" if b.source == G.REFERENCE else G.camera_text(img_b)
        self.right_cap.setText(f"{b.when()} · {b.item_id}\n{right} · {source_text(b)}")
        self.left.set_image(img_a.path)
        self.right.set_image(img_b.path)
        self.compare_label.setText("; ".join(t for t in (note, compare_label(a, b, img_a, img_b)) if t))
        self.compare.show()

    def visible(self) -> tuple[G.GalleryItem, G.GalleryImage, tuple[G.GalleryItem, G.GalleryImage] | None] | None:
        """O que está na tela: o run selecionado, o quadro mostrado e, se a comparação dele estiver aberta, o outro lado."""
        it = self.selected()
        if it is None:
            return None
        if self.compared and self.compared_images and self.compare.isVisibleTo(self) \
                and self.compared[0].item_id == it.item_id:
            return it, self.compared_images[0], (self.compared[1], self.compared_images[1])
        img = G.primary_image(it)
        return (it, img, None) if img is not None else None

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


__all__ = ["visual_line", "NOT_EVALUATED", "BigCard", "DesignView", "best_headline", "compare_label", "source_text", "tech_text",
           "thumbnail"]
