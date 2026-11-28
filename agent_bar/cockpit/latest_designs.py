"""latest_designs.py — F2 (Felipe, 2026-11-28): "o design, pra eu ver as últimas plantas, as últimas renderizações".

Somente leitura. Varre as pastas de saída do motor e da evidência e devolve as últimas plantas (.skp) e os últimos
renders, os mais recentes primeiro. Nada é escrito, copiado ou movido. Sem Qt.

Regras (no código, não no nome do arquivo):
- **Planta** = `*.skp`, fora das cópias de trabalho (`work/`, `render_copy.skp`), de backups do SketchUp (`*~.skp`) e
  de arquivo morto (`_archive`).
- **Render** = imagem (`.png`/`.jpg`) dentro de uma pasta `renders/` de uma execução. Texturas, comparações e capturas
  de debug ficam de fora.
- Data = mtime do arquivo (o que o Windows mostra). Raiz ausente não é erro: só não contribui, e o motivo fica em
  `missing`.

Raízes: `AGENT_BAR_DESIGN_ROOTS` (separadas por ';') ou o motor (`AGENT_BAR_ENGINE_DIR`/artifacts), a evidência
(`AGENT_BAR_EVIDENCE_DIR`) e os artifacts do sketchup-mcp.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SKETCHUP_ARTIFACTS = Path(r"E:\Claude\apps\sketchup-mcp\artifacts")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
SKIP_DIRS = {"_archive", "work", ".git", "__pycache__", "node_modules", ".venv"}
LIMIT = 60
PATH_ROLE = 0x0100                                                           # Qt.ItemDataRole.UserRole: o caminho do item


@dataclass(frozen=True)
class DesignFile:
    kind: str                    # "planta" | "render"
    path: Path
    mtime: float
    root: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def run(self) -> str:
        """A pasta da execução: a do .skp, ou a de cima de `renders/`."""
        parent = self.path.parent
        return parent.parent.name if parent.name.lower() == "renders" else parent.name


@dataclass
class LatestDesigns:
    plants: list[DesignFile] = field(default_factory=list)
    renders: list[DesignFile] = field(default_factory=list)
    roots: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def default_roots() -> list[Path]:
    env = os.environ.get("AGENT_BAR_DESIGN_ROOTS", "").strip()
    if env:
        return [Path(p.strip()) for p in env.split(";") if p.strip()]
    from agent_bar.cockpit.evidence import evidence_dir
    from agent_bar.cockpit.gallery import engine_dir
    return [engine_dir() / "artifacts", evidence_dir(), DEFAULT_SKETCHUP_ARTIFACTS]


def _walk(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for f in filenames:
            yield Path(dirpath) / f


def is_plant(p: Path) -> bool:
    n = p.name.lower()
    return n.endswith(".skp") and not n.endswith("~.skp") and n != "render_copy.skp"


def is_render(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_SUFFIXES and p.parent.name.lower() == "renders"


def scan(roots: Iterable[Path] | None = None, limit: int = LIMIT) -> LatestDesigns:
    out = LatestDesigns(roots=list(roots if roots is not None else default_roots()))
    seen: set[str] = set()
    for root in out.roots:
        if not root.is_dir():
            out.missing.append(f"{root}: pasta não encontrada")
            continue
        for p in _walk(root):
            kind = "planta" if is_plant(p) else ("render" if is_render(p) else "")
            if not kind:
                continue
            key = os.path.normcase(str(p.resolve()))
            if key in seen:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            seen.add(key)
            (out.plants if kind == "planta" else out.renders).append(DesignFile(kind, p, mtime, root))
    for items in (out.plants, out.renders):
        items.sort(key=lambda d: (-d.mtime, str(d.path).lower()))
        del items[limit:]
    return out


SKP_THUMBNAILS = ("meta/preview_thumbnail.png", "meta/model_thumbnail.png")


def skp_thumbnail(path: Path) -> bytes | None:
    """A miniatura que o próprio SketchUp grava dentro do .skp (o arquivo é um zip com `meta/*_thumbnail.png`). Só
    leitura; arquivo antigo, corrompido ou sem miniatura = None (a tela mostra só o nome)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for name in SKP_THUMBNAILS:
                if name in names:
                    return z.read(name)
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    return None


__all__ = ["DesignFile", "LatestDesigns", "default_roots", "is_plant", "is_render", "scan", "skp_thumbnail"]
