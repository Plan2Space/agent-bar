"""Gera launchers/agent_bar.ico (16..256 px) desenhado aqui com Qt: barra arredondada com 3 pontos de estado. Sem logo de terceiros.

Uso (a partir do repo):  .venv\\Scripts\\python launchers\\make_icon.py
Deterministico: mesmas cores e geometria; cada tamanho vai como PNG dentro do ICO (formato aceito pelo Windows Vista+).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parent / "agent_bar.ico"


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(22, 22, 26))                                          # fundo escuro da barra
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.2, s * 0.2)
    p.setBrush(QColor(44, 48, 58))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.36, s * 0.72, s * 0.28), s * 0.14, s * 0.14)   # a barra
    for i, color in enumerate((QColor(61, 220, 132), QColor(74, 168, 255), QColor(255, 194, 51))):   # conectado, trabalhando, atencao
        r = s * 0.075
        cx = s * (0.30 + 0.20 * i)
        p.setBrush(color)
        p.drawEllipse(QRectF(cx - r, s * 0.5 - r, 2 * r, 2 * r))
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    pngs = [render(n) for n in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, blobs = b"", b""
    for n, png in zip(SIZES, pngs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    OUT.write_bytes(header + entries + blobs)
    print(f"{OUT} ({len(SIZES)} tamanhos, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
