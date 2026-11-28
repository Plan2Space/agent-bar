"""design_context.py — o contexto do Chat a partir da seleção do Design (UX-13; WORKSPACE-UX F11). Sem Qt.

A seleção é planta ▸ cômodo ▸ câmera ▸ run (e, se aberta, a comparação e a referência vigente). O texto é determinístico,
curto e só com fatos lidos (ids, câmera e luz do quadro mostrado, data do run): nada de avaliação ou veredito. Ele só vai
ao agente quando o Felipe marca "Anexar ao próximo prompt"; o que vai é exatamente o texto do preview (mesma função).
"""
from __future__ import annotations

from agent_bar.cockpit import gallery as G

HEADER = "[Contexto do Design — anexado pelo Felipe]"


def breadcrumb(item: G.GalleryItem | None, image: G.GalleryImage | None) -> str:
    if item is None:
        return "nenhum render selecionado"
    return f"{item.plant} ▸ {item.room} ▸ {G.camera_text(image)} ▸ run {item.item_id}"


def context_text(item: G.GalleryItem | None, image: G.GalleryImage | None, *,
                 reference: G.GalleryItem | None = None,
                 compared: tuple[G.GalleryItem, G.GalleryImage] | None = None) -> str:
    """O texto exato que o Chat anexa (vazio sem seleção)."""
    if item is None or image is None:
        return ""
    lines = [HEADER, f"planta: {item.plant}", f"cômodo: {item.room}",
             f"câmera: {G.camera_text(image)} (quadro {image.key})", f"run: {item.item_id} · {item.when()}"]
    if reference is not None:
        cat = f" (categoria {reference.category})" if reference.category else ""
        lines.append(f"referência vigente: {reference.item_id}{cat}")
    if compared is not None:
        other, other_img = compared
        lines.append(f"comparando com: {other.item_id} · {G.camera_text(other_img)}")
    return "\n".join(lines)


__all__ = ["HEADER", "breadcrumb", "context_text"]
