"""run_vision.py — "avaliar este run": a REFERÊNCIA do cômodo e o RENDER do run, no mesmo pedido, para um modelo com
visão, pelo caminho único da plataforma (SessionManager → send_with_images → ImageLedger → adaptador). Sem Qt.

Ordem, e o que cada passo recusa (nada é enviado quando algo é recusado):
  1. o modelo precisa anunciar `images = SUPPORTED` no provedor (VS-R10); senão, recusa com o motivo;
  2. o cômodo precisa ter referência com imagem conferida (pedido do Felipe: "Enviar a referência e o render real");
  3. cada imagem (a da referência e as do render) é relida do disco e o sha256 dos BYTES tem de ser o que a galeria
     conferiu contra o catálogo/manifest (VS-R2); divergiu, recusa;
  4. as imagens vão no MESMO pedido, referência primeiro, como `ImagePayload` (sem segunda leitura); o ledger grava uma
     linha por imagem com o papel (`reference`/`render`) antes do envio e marca SENT quando o adaptador aceita;
  5. confirmação: SENT não prova que o modelo recebeu. Depois do turno, mede-se (sessões novas, mesmo texto) a linha
     de base só de texto e o custo de CADA imagem sozinha (essas imagens também passam pelo ledger, finalidade CALIBRATION).
     Confirma só se o acréscimo medido do pedido com todas as imagens >= soma dos custos - metade do menor: faltando
     qualquer imagem, não fecha. Sem contagem medida: "entregue ao adaptador; contagem do provedor indisponível".
     No Ollama 0.40 o prompt_eval_count conta o prompt inteiro mesmo com cache (medido: par 4029 = 41 + 2042 + 1946).
Este caminho só entrega imagem e devolve o texto do modelo: não grava nota, veredito nem aprovação (a nota é do
S01C-F1-05, com os campos e as recusas dele).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit.gallery import GalleryItem, primary_image
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
CALIBRATION_BY = "studio:review:calibracao"          # turnos que medem o custo de cada imagem sozinha
PURPOSE = "EVALUATION"
CALIBRATION_PURPOSE = "CALIBRATION"                      # ledger: medida de tokens, nunca imagem avaliada
DEFAULT_PROMPT = ("Compare o render com a referência: descreva de forma objetiva o que cada imagem mostra (cama, "
                  "cabeceira, enxoval, materiais, iluminação, mobiliário) e o que difere entre elas. Cite só elementos "
                  "visíveis. Não dê nota nem veredito.")
# Abaixo disto por imagem, a contagem do provedor não confirma que a imagem entrou no prompt (o fake conta 256; o
# qwen2.5vl:7b real contou ~2000 para uma imagem, #90).
MIN_IMAGE_TOKENS = 64
# Contexto pedido ao provedor: a referência e um render em resolução cheia passam dos 4096 do padrão do Ollama
# (medido: 4128 tokens com o rc5 r2_canto, #94). Nunca acima do limite que o modelo anuncia (o adaptador recusa).
VISION_CONTEXT = 16384


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
    baseline: UsageSnapshot | None = None        # mesmo texto, sem imagem, sessão nova
    baseline_error: str = ""
    # calibração por imagem: mesmo texto + SÓ aquela imagem, sessão nova; (papel, sha256, tokens medidos ou None, erro)
    calibration: list[tuple[str, str, int | None, str]] = field(default_factory=list)

    def delivery_line(self) -> str:
        """O que foi entregue ao adaptador, como o ledger registrou (vai junto da linha de uso)."""
        if not self.deliveries:
            return "nenhuma imagem registrada"
        first = self.deliveries[0]
        rows = sorted(self.deliveries, key=lambda d: d.role != "reference")
        shas = ", ".join(f"{d.role or 'imagem'} {Path(d.path).name} sha256 {d.sha256[:12]}…" for d in rows[:4])
        return (f"entregue ao adaptador: {len(self.deliveries)} imagem(ns) {first.status} para {first.model_id} "
                f"(registro {first.batch_id[:8]}…: {shas})")

    def confirmation(self) -> tuple[bool, str]:
        """(confirmado, texto). Confirma só quando o acréscimo MEDIDO do pedido com todas as imagens fecha com a soma
        do custo de CADA imagem medida sozinha, com folga de metade da menor (CONFIRM_RULE). Assim, faltando qualquer
        uma das imagens, o acréscimo fica abaixo do exigido. Sem medir cada imagem, não confirma."""
        n = len(self.deliveries)
        got = self.usage.input_tokens if self.usage is not None else None
        if got is None or got.state != "MEASURED" or got.value is None:
            return False, "entregue ao adaptador; contagem do provedor indisponível: não confirmado que o modelo recebeu"
        base = self.baseline.input_tokens if self.baseline is not None else None
        if base is None or base.state != "MEASURED" or base.value is None:
            why = self.baseline_error or "sem contagem medida"
            return False, (f"o provedor contou {got.value} tokens de entrada; linha de base só de texto indisponível "
                           f"({why}): não confirmado que as imagens chegaram")
        delta = int(got.value) - int(base.value)
        head = f"o provedor contou {got.value} tokens de entrada contra {base.value} só de texto (+{delta})"
        if not n or len(self.calibration) != n or any(t is None for _r, _s, t, _e in self.calibration):
            why = "; ".join(e for *_x, e in self.calibration if e) or "custo de cada imagem não medido"
            return False, f"{head}; não dá para separar por imagem ({why}): não confirmado que as {n} chegaram"
        costs = [(role or "imagem", int(t) - int(base.value)) for role, _s, t, _e in self.calibration]  # type: ignore[arg-type]
        parts = " + ".join(f"{c} ({r})" for r, c in costs)
        small = min(c for _r, c in costs)
        if small < MIN_IMAGE_TOKENS:
            return False, f"{head}; uma imagem sozinha custou só {small} (mínimo {MIN_IMAGE_TOKENS}): não confirmado"
        need = sum(c for _r, c in costs) - small // 2
        if delta >= need:
            return True, (f"{head}; cada imagem sozinha custa {parts}: o acréscimo só fecha com as {n} imagens "
                          f"(exigido ≥ {need})")
        return False, (f"{head}; cada imagem sozinha custa {parts}: acréscimo abaixo de {need}, falta pelo menos uma "
                       f"das {n} imagens: não confirmado")


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


def reference_link(ref: GalleryItem | None, image_sha256: str = "") -> ReferenceLink | None:
    """Liga as linhas do ledger à referência do catálogo. `image_sha256`: o da imagem da referência que vai no pedido
    (o ledger confere contra a linha de papel "reference")."""
    if ref is None:
        return None
    rid, _, version = ref.item_id.partition("@")
    img = primary_image(ref)
    return ReferenceLink(rid, reference_version=version or (img.key if img else ""), source_hash=image_sha256,
                         project_id=ref.plant, room_id=ref.room)


def framing(ref: GalleryItem, ref_key: str, item: GalleryItem, keys: Sequence[str]) -> str:
    """Diz ao modelo qual imagem é qual, na ordem em que vão (sem caminho de arquivo)."""
    lines = ["Imagens deste pedido, na ordem:",
             f"1. referência do cômodo {ref.item_id} (quadro {ref_key}): estética e sintética, não é geometria nem medida;"]
    lines += [f"{n}. render do run {item.item_id}, quadro {k};" for n, k in enumerate(keys, 2)]
    return "\n".join(lines)


class RunVision:
    """Usa as instâncias da janela: o SessionManager da plataforma e o MESMO ImageLedger (sem segundo registro)."""

    def __init__(self, sessions: SessionManager, ledger: ImageLedger, provider_id: str = PROVIDER, *,
                 baseline: bool = True, timeout: float = 600.0) -> None:
        self.sessions = sessions
        self.ledger = ledger
        self.provider_id = provider_id
        self.baseline = baseline
        self.timeout = timeout

    def models(self) -> list[dict]:
        return self.sessions.models(self.provider_id)

    def digest(self, model_id: str) -> str:
        """Digest do modelo instalado (para o registro da avaliação); CapabilityUnsupported sem fonte."""
        return self.sessions.model_digest(self.provider_id, model_id)

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
        ref_img = primary_image(reference) if reference is not None else None
        if reference is None or ref_img is None:
            raise VisionRefused(["cômodo sem referência com imagem conferida: a avaliação manda a referência e o "
                                 "render juntos; nada enviado"])
        reasons: list[str] = []
        payloads: list[ImagePayload] = []
        for it, keys in ((reference, [ref_img.key]), (item, list(image_keys))):
            try:
                payloads += verified_payloads(it, keys)
            except VisionRefused as exc:
                reasons += [f"{'referência' if it is reference else 'render'} {r}" for r in exc.reasons]
        if reasons:
            raise VisionRefused(reasons)
        roles = ["reference"] + ["render"] * (len(payloads) - 1)
        text = framing(reference, ref_img.key, item, image_keys) + "\n\n" + prompt
        link = reference_link(reference, payloads[0].sha256)
        req = SessionRequest(model_id=model_id, context_tokens=VISION_CONTEXT)
        sid = self.sessions.open(self.provider_id, req, title=f"avaliar {item.item_id}")["id"]
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
            inner = self.sessions.send(sid, text, sink, images=payloads, purpose=PURPOSE, requested_by=REQUESTED_BY,
                                       reference=link, roles=roles)
        except Exception:
            self._finish(result)
            raise
        out: cf.Future = cf.Future()

        def run() -> None:                                                    # fora da thread do provedor
            try:
                res = inner.result(self.timeout)
                result.text = "".join(parts)
                result.stop_reason = str(getattr(res, "stop_reason", "") or "")
            except BaseException as exc:                                      # noqa: BLE001 - vai para o future
                self._finish(result)
                out.set_exception(exc)
                return
            self._finish(result)
            if self.baseline:
                result.baseline, result.baseline_error = self._measure(model_id, text)
                for p, role in zip(payloads, roles, strict=True):            # cada imagem sozinha, também registrada
                    snap, err = self._measure(model_id, text, [p], [role], link if role == "reference" else None)
                    m = snap.input_tokens if snap is not None else None
                    val = int(m.value) if m is not None and m.state == "MEASURED" and m.value is not None else None
                    result.calibration.append((role, p.sha256, val, err or ("" if val is not None else "sem contagem")))
            out.set_result(result)
        threading.Thread(target=run, name="run-vision", daemon=True).start()
        return out

    def _measure(self, model_id: str, text: str, images: Sequence[ImagePayload] = (), roles: Sequence[str] = (),
                 link: ReferenceLink | None = None) -> tuple[UsageSnapshot | None, str]:
        """Um turno de medida numa sessão nova: o mesmo texto, sem imagem (linha de base) ou com UMA imagem
        (calibração). Imagem de calibração passa pelo ledger como qualquer outra (CALIBRATION_BY)."""
        try:
            req = SessionRequest(model_id=model_id, context_tokens=VISION_CONTEXT)
            sid = self.sessions.open(self.provider_id, req, title="medida de tokens")["id"]
        except Exception as exc:                                              # noqa: BLE001 - sem medida, não confirma
            return None, f"{type(exc).__name__}: {exc}"
        got: list[UsageSnapshot] = []

        def sink(ev: AgentEvent) -> None:
            if ev.kind == "usage":
                got.append(UsageSnapshot.from_dict(ev.data))
        err = ""
        try:
            if images:
                fut = self.sessions.send(sid, text, sink, images=list(images), purpose=CALIBRATION_PURPOSE,
                                         requested_by=CALIBRATION_BY, reference=link, roles=list(roles))
            else:
                fut = self.sessions.send(sid, text, sink)
            fut.result(self.timeout)
        except Exception as exc:                                              # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                self.sessions.close(sid)
            except Exception:                                                 # noqa: BLE001
                pass
        snap = None
        for s in got:
            snap = s if snap is None else snap.merged_with(s)
        return snap, err

    def _finish(self, result: VisionResult) -> None:
        result.deliveries = self.ledger.list(session_id=result.session_id)
        try:
            self.sessions.close(result.session_id)
        except Exception:                                                     # noqa: BLE001 - fechar nao desfaz a entrega
            pass


__all__ = ["CALIBRATION_BY", "CALIBRATION_PURPOSE", "DEFAULT_PROMPT", "MIN_IMAGE_TOKENS", "PURPOSE", "REQUESTED_BY", "RunVision", "VisionRefused", "VisionResult",
           "VISION_CONTEXT", "framing", "reference_link", "verified_payloads"]
