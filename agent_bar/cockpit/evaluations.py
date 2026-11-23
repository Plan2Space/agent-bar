"""evaluations.py — registro de avaliação visual por rubrica v1 (S01C-F1-05; D-S01C-2, S01C-R10, VS-R10..R13). Sem Qt.

Uma avaliação é a OPINIÃO de um avaliador (agente com visão comprovada) sobre as imagens de um run, critério a
critério, com nota inteira de 1 a 5 e justificativa. Regras travadas aqui (e por teste):
  - recusada sem justificativa em algum critério, sem digest do modelo, ou com avaliador sem visão comprovada
    (`images` diferente de SUPPORTED para aquele modelo; VS-R10);
  - recusada se citar imagem que não está no run (sha256 conferido pela galeria; VS-R2/VS-R10a) ou sem imagem;
  - nenhuma nota 0–100, soma, média ou "similaridade" é gravada nem aceita (o 0–100 depende da calibração aprovada,
    S01C-F1-06/15); os pesos da rubrica são só metadado;
  - avaliação do `qwen2.5vl` sai com confiança baixa até a calibração (D-S01C-2);
  - o runtime nunca grava HUMAN_APPROVAL nem veredito: avaliador humano ou campo de veredito são recusados (VS-R13);
  - gravada FORA do evidence, do motor e da design-library (VS-R12), com escrita atômica.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_bar.cockpit.evidence import ensure_outside
from agent_bar.cockpit.gallery import GalleryItem
from agent_bar.cockpit.store import atomic_write_json, data_dir

SCHEMA = "p2s.visual-evaluation.v1"
LABEL = "opinião do avaliador (consultiva); não é veredito nem aprovação"
LOW_CONFIDENCE_PREFIXES = ("qwen2.5vl",)                    # D-S01C-2: até a calibração S01C-F1-06 aprovada
LOW_CONFIDENCE_NOTE = "confiança baixa: modelo local sem calibração aprovada (D-S01C-2, S01C-F1-06)"
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEY = re.compile(r"0_?100|total|soma|sum|media|m[eé]dia|average|weighted|ponderad|similar|percent|"
                            r"verdict|veredito|human_approval|aprova", re.I)


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight_pct: int                                          # metadado; nunca vira soma (D-S01C-2)


@dataclass(frozen=True)
class Rubric:
    version: str
    source: str
    criteria: tuple[Criterion, ...]

    def keys(self) -> list[str]:
        return [c.key for c in self.criteria]


RUBRIC_V1 = Rubric(
    "rubric-v1", "P2S-SUITE01-VISUAL-CONVERGENCE Anexo A §8.2 (D-S01C-2)",
    (Criterion("fidelidade_referencia", "Fidelidade à referência", 25),
     Criterion("cama_cabeceira_enxoval", "Cama, cabeceira e enxoval", 25),
     Criterion("materiais_texturas", "Materiais e texturas", 20),
     Criterion("iluminacao", "Iluminação", 15),
     Criterion("mobiliario_detalhes", "Mobiliário e detalhes", 15)))
RUBRICS = {RUBRIC_V1.version: RUBRIC_V1}
CURRENT_RUBRIC = RUBRIC_V1


class EvaluationRejected(ValueError):
    """Avaliação recusada; `reasons` lista todos os motivos (nada é gravado)."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


@dataclass(frozen=True)
class Evaluator:
    kind: str                    # sempre "agent": o runtime não grava avaliação humana
    provider: str                # ex.: ollama, claude-acp
    model: str                   # ex.: qwen2.5vl:7b
    digest: str                  # digest/versão imutável do modelo
    version: str                 # versão do provedor/adaptador
    images: str                  # capacidade `images` do modelo: SUPPORTED | UNSUPPORTED | UNKNOWN
    vision_proof: str            # VS-R10: registro de capacidade com evidência, ou prova §8.1 do caminho por ferramenta


@dataclass
class Evaluation:
    id: str
    schema: str
    plant: str
    room: str
    run_id: str
    source: str                  # fonte do run na galeria (PUBLICADO / MOTOR_NAO_PUBLICADO)
    evaluator: dict
    reference: dict              # {"id": ..., "sha256": ...} da referência usada
    camera: str
    rubric_version: str
    scores: dict[str, dict]      # criterio -> {"score": 1..5, "justification": str}
    images: list[dict]           # [{"ident": repo:caminho@sha, "sha256": ...}]
    limitations: list[str]
    created_at: str              # UTC ISO
    confidence: str              # normal | baixa
    label: str = LABEL

    def to_dict(self) -> dict:
        return asdict(self)


def low_confidence(model: str) -> bool:
    return model.lower().startswith(LOW_CONFIDENCE_PREFIXES)


def _utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _forbidden_keys(obj: object, path: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if _FORBIDDEN_KEY.search(str(k)):
                out.append(f"campo proibido: {here} (sem 0–100, soma, média, veredito ou aprovação)")
            out += _forbidden_keys(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _forbidden_keys(v, f"{path}[{i}]")
    return out


def check(d: Mapping, item: GalleryItem | None = None) -> list[str]:
    """Todos os motivos de recusa de uma avaliação (dicionário gravável). Vazio = aceita."""
    errs = _forbidden_keys(d)
    if d.get("schema") != SCHEMA:
        errs.append(f"schema diferente de {SCHEMA}")
    for k in ("plant", "room", "run_id", "camera", "created_at"):
        if not str(d.get(k) or "").strip():
            errs.append(f"sem {k}")
    ev = d.get("evaluator") or {}
    if ev.get("kind") != "agent":
        errs.append("avaliador precisa ser agente: o runtime não grava avaliação humana nem HUMAN_APPROVAL")
    if not str(ev.get("model") or "").strip():
        errs.append("sem modelo do avaliador")
    if not str(ev.get("digest") or "").strip():
        errs.append("sem digest do modelo")
    if ev.get("images") != "SUPPORTED":
        errs.append(f"modelo sem visão comprovada (images={ev.get('images')!r}; VS-R10)")
    elif not str(ev.get("vision_proof") or "").strip():
        errs.append("sem a prova de visão do avaliador (VS-R10)")
    ref = d.get("reference") or {}
    if not str(ref.get("id") or "").strip() or not _SHA64.match(str(ref.get("sha256") or "")):
        errs.append("sem referência (id e sha256)")
    rubric = RUBRICS.get(str(d.get("rubric_version")))
    if rubric is None:
        errs.append(f"rubrica desconhecida: {d.get('rubric_version')!r}")
    else:
        scores = d.get("scores") or {}
        extra = sorted(set(scores) - set(rubric.keys()))
        if extra:
            errs.append(f"critério fora da rubrica {rubric.version}: {', '.join(extra)}")
        for c in rubric.criteria:
            s = scores.get(c.key)
            if not isinstance(s, Mapping):
                errs.append(f"{c.key}: sem nota")
                continue
            v = s.get("score")
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
                errs.append(f"{c.key}: nota precisa ser inteira de 1 a 5 (veio {v!r})")
            if not str(s.get("justification") or "").strip():
                errs.append(f"{c.key}: sem justificativa")
    imgs = d.get("images") or []
    if not imgs:
        errs.append("nenhuma imagem citada")
    for im in imgs:
        if not _SHA64.match(str((im or {}).get("sha256") or "")):
            errs.append(f"imagem sem sha256: {im!r}")
    if item is not None:
        if (item.plant, item.room, item.item_id) != (d.get("plant"), d.get("room"), d.get("run_id")):
            errs.append("avaliação não é deste run")
        known = {i.sha256 for i in item.images}
        for im in imgs:
            if (im or {}).get("sha256") and im["sha256"] not in known:
                errs.append(f"imagem fora do run (sha256 não conferido pela galeria): {im.get('ident') or im['sha256']}")
        if item.kind != "render":
            errs.append("só run de render é avaliado")
    if not isinstance(d.get("limitations"), list):
        errs.append("sem a lista de limitações")
    if d.get("confidence") not in ("normal", "baixa"):
        errs.append("confiança inválida")
    elif low_confidence(str(ev.get("model") or "")) and d.get("confidence") != "baixa":
        errs.append("qwen2.5vl precisa sair com confiança baixa até a calibração (D-S01C-2)")
    return errs


def new_evaluation(item: GalleryItem, evaluator: Evaluator, *, reference: GalleryItem, camera: str,
                   scores: Mapping[str, tuple[int, str]], image_shas: Iterable[str], limitations: Sequence[str] = (),
                   rubric: Rubric = CURRENT_RUBRIC, clock: Callable[[], float] = time.time) -> Evaluation:
    """Monta e confere a avaliação. Qualquer motivo de recusa -> EvaluationRejected (nada é gravado)."""
    by_sha = {i.sha256: i for i in item.images}
    ref_img = reference.images[0] if reference.images else None
    lims = list(limitations)
    low = low_confidence(evaluator.model)
    if low and LOW_CONFIDENCE_NOTE not in lims:
        lims.append(LOW_CONFIDENCE_NOTE)
    ev = Evaluation(
        id=uuid.uuid4().hex[:12], schema=SCHEMA, plant=item.plant, room=item.room, run_id=item.item_id,
        source=item.source, evaluator=asdict(evaluator),
        reference={"id": reference.item_id, "sha256": ref_img.sha256 if ref_img else ""}, camera=camera,
        rubric_version=rubric.version,
        scores={k: {"score": s, "justification": j} for k, (s, j) in scores.items()},
        images=[{"ident": by_sha[s].ident if s in by_sha else "", "sha256": s} for s in image_shas],
        limitations=lims, created_at=_utc(clock()), confidence="baixa" if low else "normal")
    errs = check(ev.to_dict(), item)
    if errs:
        raise EvaluationRejected(errs)
    return ev


class EvaluationStore:
    """Avaliações em `%LOCALAPPDATA%\\agent_bar\\cockpit\\evaluations\\<planta>\\<comodo>\\<run>\\` (VS-R12)."""

    def __init__(self, root: Path | None = None, protected: Iterable[Path] = ()) -> None:
        self.root = (root or data_dir()) / "evaluations"
        self.protected = [Path(p) for p in protected]

    def _dir(self, plant: str, room: str, run_id: str) -> Path:
        d = self.root / plant / room / run_id
        for p in self.protected:                               # evidence, motor e design-library: nunca
            ensure_outside(d, p)
        return d

    def save(self, ev: Evaluation, item: GalleryItem) -> Path:
        d = ev.to_dict()
        errs = check(d, item)
        if errs:
            raise EvaluationRejected(errs)
        path = self._dir(ev.plant, ev.room, ev.run_id) / f"{ev.created_at.replace(':', '')}-{ev.id}.json"
        atomic_write_json(path, d)
        return path

    def for_item(self, item: GalleryItem, rubric_version: str = CURRENT_RUBRIC.version) -> tuple[list[dict], list[str]]:
        """Avaliações VÁLIDAS deste run na rubrica pedida, mais antigas primeiro, e os arquivos recusados com o motivo.
        Arquivo inválido nunca entra na galeria (S01C-R10); rubricas diferentes nunca se misturam."""
        d = self.root / item.plant / item.room / item.item_id
        ok: list[dict] = []
        bad: list[str] = []
        if not d.is_dir():
            return ok, bad
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                bad.append(f"{p.name}: ilegível ({type(exc).__name__})")
                continue
            if not isinstance(data, dict):
                bad.append(f"{p.name}: não é objeto")
                continue
            errs = check(data, item)
            if errs:
                bad.append(f"{p.name}: {'; '.join(errs[:3])}")
            elif data.get("rubric_version") == rubric_version:
                ok.append(data)
        return ok, bad


__all__ = ["CURRENT_RUBRIC", "LABEL", "LOW_CONFIDENCE_NOTE", "RUBRIC_V1", "SCHEMA", "Criterion", "Evaluation",
           "EvaluationRejected", "EvaluationStore", "Evaluator", "Rubric", "check", "low_confidence", "new_evaluation"]
