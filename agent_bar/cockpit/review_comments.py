"""review_comments.py — comentário humano na Galeria de design → item R41 → pedido no Chat (S01C-F1-11, S01C-R8). Sem Qt.

O comentário é um item R41 SEM triagem (F0 §6): quem escreveu e quando (UTC), o cômodo, o run e a câmera VISÍVEIS no
momento, a comparação que estava na tela (se havia), a referência do cômodo e o texto livre. Categoria, natureza e
`acao` ficam vazias: quem faz a triagem é o agente, no Chat. O comentário não é aprovação nem reprovação e não bloqueia
nada (S01C-R8).

O pedido que vai para o Chat é CONTEXTO ESTRUTURADO (cômodo, run, câmera, referência, comparação, id do item), nunca
nome ou caminho de arquivo: o agente não precisa adivinhar qual imagem o Felipe estava olhando. O Chat só é preenchido;
o envio é sempre manual. Guardado fora do evidence, com escrita atômica.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_bar.cockpit import gallery as G
from agent_bar.cockpit.evidence import ensure_outside
from agent_bar.cockpit.store import atomic_write_json, data_dir

SCHEMA = "p2s.review-comment.v1"
TRIAGE_PENDING = "sem triagem"
_FILEISH = re.compile(r"[A-Za-z]:[\\/]|[A-Za-z_][\\/]\w|\w[\\/][A-Za-z_]|\.(png|webp|jpe?g|json|skp|vrscene|md|log)\b",
                      re.I)                                                   # "23/11" (data) não é caminho


class CommentRejected(ValueError):
    pass


def operator() -> str:
    """Autor do comentário: `human:<AGENT_BAR_OPERATOR>` se configurado, senão `human`. Nunca um agente."""
    name = os.environ.get("AGENT_BAR_OPERATOR", "").strip()
    return f"human:{name}" if name else "human"


@dataclass
class ReviewComment:
    id: str
    schema: str
    created_at: str                  # UTC
    author: str                      # human[:nome]
    plant: str
    room: str
    run: dict                        # run_id, source, when, render, geometry
    camera: str                      # nome do quadro visível (não é arquivo)
    image_sha256: str                # o quadro exato que estava na tela
    compared_with: dict | None       # o outro lado da comparação visível, se havia
    reference: dict | None           # referência do cômodo: id, provenance, usage
    text: str
    category: str = ""               # R41: o agente atribui na triagem
    nature: str = ""                 # estetico | fisico, na triagem
    acao: str = ""                   # VS-R14: NENHUMA_PLANTA_VENCE | CORRIGIVEL_POR_CODIGO | EXIGE_CAPACIDADE_NOVA
    triage: str = TRIAGE_PENDING
    note: str = "comentário humano: não é aprovação nem reprovação e não bloqueia nada (S01C-R8)"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_EXT = re.compile(r"\.(png|webp|jpe?g|json|skp|vrscene|md|log)\b", re.I)


def technical(item: G.GalleryItem) -> str:
    """Estado técnico que pode ir para o Chat: o veredito só se o run estiver VERIFICADO; senão o motivo (sem extensão
    de arquivo, para o contexto continuar estruturado)."""
    gaps = item.verification_gaps()
    if gaps:
        return "não verificado: " + _EXT.sub("", gaps[0])
    return f"render {item.render_verdict or '—'}; geometria {item.build.get('geometria', '—')}"


def _source(item: G.GalleryItem) -> str:
    return f"motor: {G.UNPUBLISHED_NOTE}" if item.source == G.ENGINE else G.SOURCE_LABEL.get(item.source, item.source)


def _run_ref(item: G.GalleryItem) -> dict:
    return {"run_id": item.item_id, "source": item.source, "source_text": _source(item), "when": item.when(),
            "kind": item.kind, "verified": item.verified, "technical": technical(item),
            "render": item.render_verdict or "", "geometry": item.build.get("geometria", "")}


def new_comment(text: str, item: G.GalleryItem, image: G.GalleryImage, *, reference: G.GalleryItem | None = None,
                compared: tuple[G.GalleryItem, G.GalleryImage] | None = None, author: str | None = None,
                clock: Callable[[], float] = time.time) -> ReviewComment:
    """Comentário sobre o que está VISÍVEL: o run e o quadro da tela. Texto vazio ou quadro que não é do run: recusa."""
    if not text.strip():
        raise CommentRejected("comentário vazio")
    if image not in item.images:
        raise CommentRejected(f"o quadro {image.key} não é imagem conferida de {item.item_id}")
    who = author or operator()
    if not who.startswith("human"):
        raise CommentRejected("comentário da galeria é humano: autor precisa ser human[:nome]")
    ref = None
    if reference is not None:
        ref = {"id": reference.item_id, "provenance": reference.provenance_text(),
               "usage": reference.usage or "", "note": "referência estética, não geometria"}
    cmp = None
    if compared is not None:
        other, other_img = compared
        cmp = {**_run_ref(other), "camera": other_img.key, "image_sha256": other_img.sha256,
               "is_reference": other.source == G.REFERENCE}
    return ReviewComment(id=uuid.uuid4().hex[:12], schema=SCHEMA,
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock())), author=who,
                         plant=item.plant, room=item.room, run=_run_ref(item), camera=image.key,
                         image_sha256=image.sha256, compared_with=cmp, reference=ref, text=text.strip())


def chat_request(c: ReviewComment) -> str:
    """Pedido para o Chat: só contexto estruturado (sem nome/caminho de arquivo). O envio é manual."""
    r = c.run
    lines = [
        "Pedido de alteração vindo da Revisão visual (comentário humano; não é aprovação nem reprovação).",
        "",
        f"Comentário: \"{c.text}\"",
        "",
        "Contexto (o que estava na tela):",
        f"- cômodo: {c.plant} / {c.room}",
        f"- run visível: {r['run_id']} ({r['when']}; {r['source_text']}; {r['technical']})",
        f"- câmera: {c.camera} (quadro sha256 {c.image_sha256[:12]})",
    ]
    if c.compared_with:
        o = c.compared_with
        what = "a referência" if o["is_reference"] else "a versão anterior"
        lines.append(f"- comparado com {what}: {o['run_id']} ({o['when']}), câmera {o['camera']}")
    if c.reference:
        lines.append(f"- referência do cômodo: {c.reference['id']} ({c.reference['provenance']}; "
                     f"{c.reference['note']})")
    lines += [
        f"- item de revisão: comentário {c.id} (R41, {c.triage}: categoria, natureza e ação a definir)",
        "",
        "Faça a triagem (categoria R41, natureza estético ou físico e ação: NENHUMA_PLANTA_VENCE, CORRIGIVEL_POR_CODIGO "
        "ou EXIGE_CAPACIDADE_NOVA) e proponha a mudança para o próximo run. Nada vai para o motor sem confirmação.",
    ]
    text = "\n".join(lines)
    if mentions_files(text):                                                    # trava do contrato
        raise CommentRejected("o pedido do Chat citaria arquivo ou caminho em vez de contexto estruturado")
    return text


def mentions_files(text: str) -> bool:
    """O pedido cita arquivo ou caminho (o que o contexto estruturado substitui)?"""
    return bool(_FILEISH.search(text))


class CommentStore:
    """`%LOCALAPPDATA%\\agent_bar\\cockpit\\review\\comments\\<planta>\\<comodo>\\<id>.json`, fora das fontes."""

    def __init__(self, root: Path | None = None, protected: Iterable[Path] = ()) -> None:
        self.root = (root or data_dir()) / "review" / "comments"
        self.protected = [Path(p) for p in protected]

    def save(self, c: ReviewComment) -> Path:
        d = self.root / c.plant / c.room
        for p in self.protected:
            ensure_outside(d, p)
        path = d / f"{c.created_at.replace(':', '')}-{c.id}.json"
        atomic_write_json(path, c.to_dict())
        return path

    def list(self, plant: str, room: str) -> list[dict]:
        d = self.root / plant / room
        out = []
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out


__all__ = ["SCHEMA", "TRIAGE_PENDING", "CommentRejected", "CommentStore", "ReviewComment", "chat_request",
           "mentions_files", "new_comment", "operator"]
