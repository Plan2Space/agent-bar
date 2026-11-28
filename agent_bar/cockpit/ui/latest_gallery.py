"""latest_gallery.py — F2: a primeira página do Design. "As últimas plantas e as últimas renderizações", mais recentes
primeiro, com miniatura, data e "Abrir no SketchUp" / "Abrir pasta".

Somente leitura: a lista vem de `latest_designs.scan` e abrir usa o programa padrão do Windows (o .skp abre no SketchUp).
Nada é escrito, copiado ou movido. A primeira leitura acontece quando a página aparece; "Atualizar" lê de novo.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import latest_designs as L
from agent_bar.cockpit.ui.theme.qt import style as theme_style

THUMB = QSize(220, 150)
TITLE_CSS = "QLabel{color:@text;font-size:14px;font-weight:600}"
MUTED_CSS = "QLabel{color:@text_muted;font-size:12px}"
LIST_CSS = ("QListWidget{background:@surface;border:1px solid @border;border-radius:@radius;color:@text}"
            "QListWidget::item{padding:6px;border-radius:@radius}"
            "QListWidget::item:selected{background:@selection;color:@text;border:2px solid @accent}")
BTN_CSS = ("QPushButton{color:@text;background:@surface_alt;border:1px solid @border;border-radius:@radius;"
           "padding:4px 12px}QPushButton:hover{background:@hover}QPushButton:disabled{color:@disabled_text}")


def _open_file(path: Path) -> None:
    os.startfile(str(path))                                                  # noqa: S606 - programa padrão do Windows


def _open_folder(path: Path) -> None:
    subprocess.Popen(["explorer", "/select,", str(path)])                    # noqa: S603,S607 - mostra o arquivo


def _when(mtime: float) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(mtime))


def _thumb(item: L.DesignFile) -> QIcon:
    if item.kind == "planta":
        data = L.skp_thumbnail(item.path)
        img = QImage.fromData(data) if data else QImage()
    else:
        reader = QImageReader(str(item.path))
        size = reader.size()
        if size.isValid():                                                   # decodifica já reduzida (render é grande)
            reader.setScaledSize(size.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
    if img.isNull():
        return QIcon()
    return QIcon(QPixmap.fromImage(img.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)))


class LatestGallery(QWidget):
    def __init__(self, scan_fn: Callable[[], L.LatestDesigns] = L.scan,
                 open_file: Callable[[Path], None] = _open_file,
                 open_folder: Callable[[Path], None] = _open_folder) -> None:
        super().__init__()
        self.scan_fn, self.open_file, self.open_folder = scan_fn, open_file, open_folder
        self._loaded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.summary = theme_style(QLabel(""), MUTED_CSS)
        self.summary.setWordWrap(True)
        self.refresh_btn = theme_style(QPushButton("Atualizar"), BTN_CSS)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.summary, 1)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)
        self.plants, self.open_plant_btn, self.plant_folder_btn = self._section(lay, "Últimas plantas", "Abrir no SketchUp")
        self.renders, self.open_render_btn, self.render_folder_btn = self._section(lay, "Últimos renders", "Abrir imagem")

    def _section(self, lay: QVBoxLayout, title: str, open_text: str):
        row = QHBoxLayout()
        row.addWidget(theme_style(QLabel(title), TITLE_CSS))
        row.addStretch(1)
        open_btn = theme_style(QPushButton(open_text), BTN_CSS)
        folder_btn = theme_style(QPushButton("Abrir pasta"), BTN_CSS)
        row.addWidget(open_btn)
        row.addWidget(folder_btn)
        lay.addLayout(row)
        lst = theme_style(QListWidget(), LIST_CSS)
        lst.setAccessibleName(title)
        lst.setViewMode(QListView.ViewMode.IconMode)
        lst.setIconSize(THUMB)
        lst.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 64))
        lst.setResizeMode(QListView.ResizeMode.Adjust)
        lst.setMovement(QListView.Movement.Static)
        lst.setWordWrap(True)
        lst.setMinimumHeight(THUMB.height() + 80)
        lay.addWidget(lst, 1)
        open_btn.clicked.connect(lambda: self._act(lst, self.open_file))
        folder_btn.clicked.connect(lambda: self._act(lst, self.open_folder))
        lst.itemDoubleClicked.connect(lambda it: self.open_file(Path(it.data(L.PATH_ROLE))))
        lst.currentItemChanged.connect(lambda cur, _p: (open_btn.setEnabled(cur is not None),
                                                        folder_btn.setEnabled(cur is not None)))
        open_btn.setEnabled(False)
        folder_btn.setEnabled(False)
        return lst, open_btn, folder_btn

    @staticmethod
    def _act(lst: QListWidget, fn: Callable[[Path], None]) -> None:
        it = lst.currentItem()
        if it is not None:
            fn(Path(it.data(L.PATH_ROLE)))

    def showEvent(self, event) -> None:                                      # noqa: N802 - API do Qt
        super().showEvent(event)
        if not self._loaded:                                                 # lê na primeira vez que aparece
            self.refresh()

    def refresh(self) -> None:
        self._loaded = True
        found = self.scan_fn()
        for lst, items in ((self.plants, found.plants), (self.renders, found.renders)):
            lst.clear()
            for d in items:
                it = QListWidgetItem(_thumb(d), f"{_when(d.mtime)}\n{d.run}")       # data primeiro: nunca cortada
                it.setData(L.PATH_ROLE, str(d.path))
                it.setToolTip(f"{d.name}\n{d.path}")
                lst.addItem(it)
        parts = [f"{len(found.plants)} planta(s)" if found.plants else "Nenhuma planta encontrada",
                 f"{len(found.renders)} render(s)" if found.renders else "nenhum render encontrado"]
        where = "; ".join(str(r) for r in found.roots) or "nenhuma pasta configurada"
        text = f"{', '.join(parts)}, os mais recentes primeiro. Lido de: {where}."
        if found.missing:
            text += " Não encontrado: " + "; ".join(found.missing) + "."
        self.summary.setText(text)


__all__ = ["LatestGallery"]
