"""run_vision.py — "avaliar este run": imagens de um run da galeria para um modelo com visão, pelo caminho único da
plataforma (SessionManager → send_with_images → ImageLedger → adaptador). Sem Qt.

Ordem, e o que cada passo recusa (nada é enviado quando algo é recusado):
  1. o modelo precisa anunciar `images = SUPPORTED` no provedor (VS-R10); senão, recusa com o motivo;
  2. cada imagem é relida do disco e o sha256 dos BYTES tem de ser o que a galeria conferiu (VS-R2); divergiu, recusa;
  3. os mesmos bytes vão como `ImagePayload` (sem segunda leitura): o ledger grava antes do envio e marca SENT/NOT_SENT,
     a Notificação sai pelo `on_change` do ledger da janela e o evento de uso do turno volta junto do texto.
Este caminho só entrega imagem e devolve o texto do modelo: não grava nota, veredito nem aprovação (a nota é do
S01C-F1-05, com os campos e as recusas dele).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit.gallery import GalleryItem
from agent_bar.platform.adapter import (
    IMAGE_LIMIT,
    IMAGE_TYPES,
    AgentEvent,
    ImagePayload,
    SessionRequest,
)
from agent_bar.platform.image_ledger import ImageDelivery, ImageLedger, ReferenceLink
from agent_bar.platform.sessions import SessionManager
from agent_bar.platform.usage import UsageSnapshot

PROVIDER = "ollama"
REQUESTED_BY = "studio:review"
PURPOSE = "EVALUATION"
DEFAULT_PROMPT = ("Descreva de forma objetiva o que aparece nesta imagem de render de um quarto: cama, cabeceira, "
                  "enxoval, materiais, iluminação e mobiliário. Cite só elementos visíveis. Não dê nota nem veredito.")


class VisionRefused(Exception):
    """Pedido recusado ANTES de qualquer envio; `reasons` diz por quê."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


@dataclass
class VisionResult:
    run_id: str
    model_id: str
    session_id: str
    text: str = ""
    usage: UsageSnapshot | None = None
    deliveries: list[ImageDelivery] = field(default_factory=list)
    stop_reason: str = ""

    def delivery_line(self) -> str:
        """O que foi entregue, como o ledger registrou (vai junto da linha de uso)."""
        if not self.deliveries:
            return "nenhuma imagem registrada"
        first = self.deliveries[0]
        shas = ", ".join(f"{Path(d.path).name} sha256 {d.sha256[:12]}…" for d in self.deliveries[:3])
        return (f"entrega: {len(self.deliveries)} imagem(ns) {first.status} para {first.model_id} "
                f"(registro {first.batch_id[:8]}…: {shas})")


def verified_payloads(item: GalleryItem, keys: Sequence[str]) -> list[ImagePayload]:
    """Relê cada imagem pedida e confere o sha256 dos bytes contra o da galeria. Qualquer falha recusa o lote todo."""
    by_key = {i.key: i for i in item.images}
    out: list[ImagePayload] = []
    reasons: list[str] = []
    if not keys:
        reasons.append("nenhuma imagem escolhida")
    for k in keys:
        img = by_key.get(k)
        if img is None:
            reasons.append(f"{k}: não é imagem conferida deste run")
            continue
        mime = IMAGE_TYPES.get(img.path.suffix.lower())
        try:
            data = img.path.read_bytes()
        except OSError as exc:
            reasons.append(f"{k}: não foi possível ler ({type(exc).__name__})")
            continue
        sha = hashlib.sha256(data).hexdigest()
        if sha != img.sha256:
            reasons.append(f"{k}: sha256 do arquivo ({sha[:12]}…) diverge do registrado ({img.sha256[:12]}…)")
        elif mime is None:
            reasons.append(f"{k}: tipo não suportado ({img.path.suffix})")
        elif not data or len(data) > IMAGE_LIMIT:
            reasons.append(f"{k}: {len(data)} bytes (limite {IMAGE_LIMIT})")
        else:
            out.append(ImagePayload(str(img.path), mime, data, sha))
    if reasons:
        raise VisionRefused(reasons)
    return out


def reference_link(ref: GalleryItem | None) -> ReferenceLink | None:
    if ref is None:
        return None
    rid, _, version = ref.item_id.partition("@")
    # sem source_hash: o arquivo entregue e o render, nao a referencia (o ledger confere source_hash contra a entrega)
    return ReferenceLink(rid, reference_version=ref.images[0].key if ref.images else version,
                         project_id=ref.plant, room_id=ref.room)


class RunVision:
    """Usa as instâncias da janela: o SessionManager da plataforma e o MESMO ImageLedger (sem segundo registro)."""

    def __init__(self, sessions: SessionManager, ledger: ImageLedger, provider_id: str = PROVIDER) -> None:
        self.sessions = sessions
        self.ledger = ledger
        self.provider_id = provider_id

    def models(self) -> list[dict]:
        return self.sessions.models(self.provider_id)

    def ask(self, item: GalleryItem, image_keys: Sequence[str], model_id: str, prompt: str = DEFAULT_PROMPT, *,
            reference: GalleryItem | None = None,
            on_event: Callable[[AgentEvent], None] = lambda _e: None) -> cf.Future:
        """Future[VisionResult]. Recusa (VisionRefused) antes de abrir sessão ou gravar qualquer coisa."""
        model = next((m for m in self.models() if m.get("model_id") == model_id), None)
        if model is None:
            raise VisionRefused([f"modelo {model_id!r} não está disponível em {self.provider_id}"])
        if model.get("images") != "SUPPORTED":
            raise VisionRefused([f"{model_id} sem visão (images={model.get('images')}): nenhuma imagem enviada"])
        if not prompt.strip():
            raise VisionRefused(["pedido sem texto"])
        payloads = verified_payloads(item, image_keys)
        sid = self.sessions.open(self.provider_id, SessionRequest(model_id=model_id), title=f"avaliar {item.item_id}")["id"]
        result = VisionResult(item.item_id, model_id, sid)
        parts: list[str] = []

        def sink(ev: AgentEvent) -> None:
            if ev.kind == "text":
                parts.append(ev.text)
            elif ev.kind == "usage":
                snap = UsageSnapshot.from_dict(ev.data)
                result.usage = snap if result.usage is None else result.usage.merged_with(snap)
            on_event(ev)
        try:
            inner = self.sessions.send(sid, prompt, sink, images=payloads, purpose=PURPOSE, requested_by=REQUESTED_BY,
                                       reference=reference_link(reference))
        except Exception:
            self._finish(result)
            raise
        out: cf.Future = cf.Future()

        def done(f: cf.Future) -> None:
            self._finish(result)
            exc = f.exception()
            if exc is not None:
                out.set_exception(exc)
                return
            result.text = "".join(parts)
            result.stop_reason = str(getattr(f.result(), "stop_reason", "") or "")
            out.set_result(result)
        inner.add_done_callback(done)
        return out

    def _finish(self, result: VisionResult) -> None:
        result.deliveries = self.ledger.list(session_id=result.session_id)
        try:
            self.sessions.close(result.session_id)
        except Exception:                                                     # noqa: BLE001 - fechar nao desfaz a entrega
            pass


__all__ = ["DEFAULT_PROMPT", "PURPOSE", "REQUESTED_BY", "RunVision", "VisionRefused", "VisionResult", "reference_link",
           "verified_payloads"]
