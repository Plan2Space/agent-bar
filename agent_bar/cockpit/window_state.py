"""window_state.py — onde a janela principal reabre (UX-04 do P2S-STUDIO-UX-CONSOLIDATION-F0). Sem Qt.

Regras:
  - reabre com a posição, o tamanho e o estado (maximizada ou não) de quando foi fechada;
  - se o monitor onde ela estava não existe mais (monitor externo desconectado, resolução mudou), volta para um monitor
    visível: o que tiver a maior parte dela, ou o principal centralizada;
  - nunca maior que a área útil do monitor, nunca menor que o mínimo, a barra de título sempre visível;
  - gravado fora do repositório (dados do Cockpit), escrita atômica; arquivo ilegível = padrão, nunca erro.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_bar.cockpit.store import atomic_write_json, data_dir
from agent_bar.geometry import MARGIN, Rect, clamp_window

MIN_W, MIN_H = 900, 600
DEFAULT_W, DEFAULT_H = 1440, 900
# Fração mínima da janela que precisa estar num monitor para ela ficar onde estava; abaixo disso, volta ao principal.
VISIBLE_FRACTION = 0.25
# (x, y) é a posição da MOLDURA e (w, h) a área do conteúdo: a barra de título fica acima do conteúdo.
FRAME_H = 40


@dataclass(frozen=True)
class WindowState:
    x: int
    y: int
    w: int
    h: int
    maximized: bool = False


def default_rect(primary: Rect) -> WindowState:
    w = min(DEFAULT_W, primary.width - 2 * MARGIN)
    h = min(DEFAULT_H, primary.height - 2 * MARGIN - FRAME_H)
    return WindowState(primary.left + (primary.width - w) // 2, primary.top + (primary.height - h) // 2, w, h)


def restore(saved: WindowState | None, screens: list[Rect]) -> WindowState:
    """Onde reabrir. `screens[0]` é o monitor principal (área útil, coordenadas lógicas)."""
    if not screens:
        raise ValueError("nenhum monitor disponivel")
    primary = screens[0]
    if saved is None:
        return default_rect(primary)
    area_w = max(1, saved.w * saved.h)
    best = max(screens, key=lambda s: s.overlap(saved.x, saved.y, saved.w, saved.h))
    if best.overlap(saved.x, saved.y, saved.w, saved.h) < VISIBLE_FRACTION * area_w:
        return WindowState(**{**asdict(default_rect(primary)), "maximized": saved.maximized})   # monitor sumiu
    w = max(MIN_W, min(saved.w, best.width - 2 * MARGIN))
    h = max(MIN_H, min(saved.h, best.height - 2 * MARGIN - FRAME_H))
    x, y = clamp_window(saved.x, saved.y, w, h + FRAME_H, best)                # a moldura inteira dentro da área útil
    return WindowState(x, y, w, h, saved.maximized)


class WindowStateStore:
    """`<dados do Cockpit>\\window.json`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "window.json"

    def load(self) -> WindowState | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            st = WindowState(int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"]), bool(raw.get("maximized", False)))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return st if st.w > 0 and st.h > 0 else None

    def save(self, st: WindowState) -> None:
        atomic_write_json(self.path, asdict(st))


__all__ = ["DEFAULT_H", "DEFAULT_W", "FRAME_H", "MIN_H", "MIN_W", "VISIBLE_FRACTION", "WindowState", "WindowStateStore",
           "default_rect", "restore"]
