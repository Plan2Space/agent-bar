"""gallery.py — galeria multi-fonte da Revisão visual (S01C-F1-03 = VS-F1-04..06; VS-R1..R5, VS-R1a). Sem Qt.

Fontes, todas lidas em modo SOMENTE LEITURA, cada item rotulado com a sua:
  - `PUBLICADO`: run do evidence, validado pelo `tools/verify.py` do checkout (o `Catalog` de `evidence.py`);
  - `MOTOR_NAO_PUBLICADO`: run em `artifacts/<planta>_<comodo>/` do motor, lido do `render_manifest.json` (render) ou do
    `run_manifest.json` (build), com o sha256 de cada imagem conferido contra o manifest. Mostrar e filtrar, nada mais
    (VS-R1a): o sha256 só prova que o arquivo é o que o motor gravou; não substitui o verify.py;
  - `REFERENCIA`: referência da design-library registrada para o cômodo (`registration.project_id/room_id`), com o
    sha256 do arquivo conferido contra o manifest.

Regras: imagem sem sha256 conferido não entra (VS-R2) e aparece em `rejected` com o motivo; ordem por `started_at` do
manifest do motor, e "dia, sem hora" quando só houver o dia (VS-R3); run do motor já publicado aparece uma vez, como
`PUBLICADO` (VS-R4); `HUMAN_APPROVAL = PENDING_HUMAN` em todo item que não seja run publicado com human.json aceito
(VS-R5). O veredito técnico de render fica separado da elegibilidade a "melhor render" (F0 §4): FAIL nunca é elegível,
e sem avaliação válida nada é elegível. Nada aqui é veredito visual.

Quatro estados SEPARADOS (nenhum infere o outro):
  - VISÍVEL: todo item lido aparece, inclusive inválido, FAIL e não publicado, sempre com os motivos;
  - VERIFICADO: sem erro de leitura, com imagem de sha256 conferido e, num render, o vínculo render → build conferido
    pelo sha256 do .skp e o render_report presente. Falta de qualquer prova = não verificado (fail-closed);
  - ELEGÍVEL: verificado, render PASS, build com geometria, funcional e construção PASS, PUBLICADO e com avaliação válida.
    Run não publicado aparece e é comparável, mas não pode ser o melhor render validado (VS-R1a) até um contrato e uma
    evidência autorizarem;
  - MELHOR: escolhido entre os elegíveis pela regra registrada (S01C-F1-07). Até a regra existir, nenhum é escolhido.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit import evidence as E

PUBLISHED = "PUBLICADO"
ENGINE = "MOTOR_NAO_PUBLICADO"
REFERENCE = "REFERENCIA"
IMPORTED = "IMPORTADA_SEM_PROVENIENCIA"         # VS-R8: rótulo reservado; nenhum leitor o produz ainda (importação
                                                # sem manifest depende do C-DL01 na design-library)
SOURCES = (PUBLISHED, ENGINE, REFERENCE, IMPORTED)
SOURCE_LABEL = {PUBLISHED: "publicado", ENGINE: "motor, não publicado", REFERENCE: "referência",
                IMPORTED: "importada, sem proveniência"}
UNPUBLISHED_NOTE = "não publicado, não validado pelo verify.py"
UNPUBLISHED_BLOCK = "não publicado: aparece e pode ser comparado, mas não pode ser o melhor render validado (VS-R1a)"
SKP_UNCHECKED = "skp não conferido"
BUILD_KEYS = ("geometria", "funcional", "construção")
ENGINE_REPO = "Plan2Space/sketchup-mcp-next"
EVIDENCE_REPO = "Plan2Space/evidence"
LIBRARY_REPO = "Plan2Space/design-library"
DEFAULT_ENGINE = Path(r"E:\Claude\apps\sketchup-mcp-next")
DEFAULT_LIBRARY = Path(r"E:\Claude\apps\design-library")
_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})-")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})")


def engine_dir() -> Path:
    return Path(os.environ.get("AGENT_BAR_ENGINE_DIR") or DEFAULT_ENGINE)


def library_dir() -> Path:
    return Path(os.environ.get("AGENT_BAR_DESIGN_LIBRARY_DIR") or DEFAULT_LIBRARY)


# ------------------------------------------------------------------------------------------------------- hash
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def file_sha256(path: Path) -> str | None:
    """sha256 do arquivo (cache por caminho, tamanho e mtime). Ilegível -> None."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _HASH_CACHE:
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
        except OSError:
            return None
        _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def _read_json(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------------------------------------------ modelo
@dataclass(frozen=True)
class GalleryImage:
    key: str                     # nome do quadro/vista (ex.: dia_cama_3q)
    path: Path
    sha256: str
    repo: str
    rel: str                     # caminho relativo ao repo

    @property
    def ident(self) -> str:
        """VS-R2: `<repo>:<caminho>@<sha256>`."""
        return f"{self.repo}:{self.rel}@{self.sha256}"


@dataclass
class GalleryItem:
    source: str
    plant: str
    room: str
    item_id: str                 # run_id (ou reference_id@vN)
    kind: str                    # render | build | reference
    started_at: str = ""         # ISO UTC do manifest do motor; "" quando só há o dia
    day: str = ""                # AAAA-MM-DD
    stage: str = ""              # etapa do evidence (só PUBLICADO)
    render_verdict: str | None = None
    build: dict[str, str] = field(default_factory=dict)
    camera_file: str = ""        # arquivo de câmeras / render spec do run
    images: list[GalleryImage] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    human_approval: str = "PENDING_HUMAN"
    curation: str = ""           # estado de curadoria (só REFERENCIA)
    title: str = ""
    synthetic: bool | None = None  # só REFERENCIA: imagem gerada (ex.: pelo ChatGPT), nunca foto real
    usage: str = ""              # só REFERENCIA: ex.: conceptual-only
    provenance: str = ""         # só REFERENCIA: registration.source_type / origin.kind

    @property
    def notes(self) -> list[str]:
        out = [UNPUBLISHED_NOTE] if self.source == ENGINE else []
        if self.source == REFERENCE:
            out.append(f"curadoria {self.curation or 'sem estado'}; referência estética, nunca geometria")
            out.append(self.provenance_text())
        return out

    def provenance_text(self) -> str:
        """VS-R1: a referência diz se é sintética e para que pode ser usada."""
        if self.synthetic is None:
            kind = "proveniência sintética não declarada no manifest"
        elif self.synthetic:
            kind = f"sintética ({self.provenance or 'gerada por IA'})"
        else:
            kind = f"não sintética ({self.provenance or 'origem registrada'})"
        return f"{kind}; uso {self.usage or 'não declarado'}"

    @property
    def source_label(self) -> str:
        return SOURCE_LABEL.get(self.source, self.source)

    def when(self) -> str:
        """Data para a UI: `dd/mm HH:MM UTC` com hora do manifest; senão `dd/mm (dia, sem hora)` (VS-R3)."""
        m = _ISO.match(self.started_at)
        if m:
            return f"{m.group(3)}/{m.group(2)} {m.group(4)}:{m.group(5)} UTC"
        if self.day:
            y, mo, d = self.day.split("-")
            return f"{d}/{mo} (dia, sem hora)"
        return "sem data"

    def sort_key(self) -> tuple[str, str]:
        return (self.day or self.started_at[:10], self.started_at)

    def verification_gaps(self) -> list[str]:
        """Por que o item NÃO está verificado (vazio = verificado). Falta de prova conta como falha (fail-closed)."""
        out = [f"inválido: {e}" for e in self.errors[:3]]
        if not self.images:
            out.append("nenhuma imagem com sha256 conferido")
        elif self.rejected:                                                    # um quadro divergente ou ausente basta
            out.append(f"{len(self.rejected)} quadro(s) recusado(s): {self.rejected[0]}")
        if self.kind == "render":
            gaps = sorted({v for v in self.build.values() if v.startswith(SKP_UNCHECKED)})
            out += [f"build: {g}" for g in gaps]
        return out

    @property
    def verified(self) -> bool:
        return not self.verification_gaps()

    def technical_blockers(self) -> list[str]:
        """Por que este item não pode ser "melhor render", sem contar avaliação (F0 §4). Vazio = tecnicamente apto."""
        if self.kind != "render":
            return ["não é run de render"]
        out = self.verification_gaps()
        if self.render_verdict != "PASS":
            out.append(f"render {self.render_verdict or 'sem veredito'}")
        for k in BUILD_KEYS:
            v = self.build.get(k, "ausente")
            if v != "PASS" and not v.startswith(SKP_UNCHECKED):
                out.append(f"build: {k} {v}")
        if self.source != PUBLISHED:
            out.append(UNPUBLISHED_BLOCK)
        return out

    def eligibility(self, evaluations: Sequence[object] = ()) -> list[str]:
        """Bloqueios à elegibilidade de "melhor render". `evaluations` = avaliações VÁLIDAS deste run na rubrica vigente
        (S01C-F1-05); sem nenhuma, o item não é elegível: aparece `sem avaliação`. Vazio = elegível."""
        out = self.technical_blockers()
        if not evaluations:
            out.append("sem avaliação")
        elif not [e for e in evaluations if not uncalibrated(e)]:                # D-S01C-2: nota sem calibração não pesa
            out.append(UNCALIBRATED_ONLY)
        return out

    def eligible(self, evaluations: Sequence[object] = ()) -> bool:
        return not self.eligibility(evaluations)

    def states(self, evaluations: Sequence[object] = ()) -> dict[str, str]:
        """VISÍVEL, VERIFICADO, ELEGÍVEL e MELHOR, separados; cada "não" traz o motivo."""
        gaps, blockers = self.verification_gaps(), self.eligibility(evaluations)
        return {"VISÍVEL": "sim",
                "VERIFICADO": "sim" if not gaps else "não: " + "; ".join(gaps),
                "ELEGÍVEL": "sim" if not blockers else "não: " + "; ".join(blockers),
                "MELHOR": "não escolhido: regra de melhor render ainda não existe (S01C-F1-07)"}


@dataclass
class Gallery:
    items: list[GalleryItem]
    errors: list[str] = field(default_factory=list)          # fonte indisponível, manifest ilegível etc.
    sources: dict[str, str] = field(default_factory=dict)    # fonte -> diretório lido

    def of(self, plant: str, room: str, sources: Iterable[str] | None = None,
           kinds: Iterable[str] | None = None) -> list[GalleryItem]:
        """Itens do cômodo, do mais recente para o mais antigo (VS-R3), com filtro opcional por fonte e tipo (VS-R6)."""
        src = set(sources) if sources is not None else None
        kd = set(kinds) if kinds is not None else None
        out = [i for i in self.items if (i.plant, i.room) == (plant, room)
               and (src is None or i.source in src) and (kd is None or i.kind in kd)]
        return sorted(out, key=GalleryItem.sort_key, reverse=True)

    def get(self, item_id: str) -> GalleryItem | None:
        return next((i for i in self.items if i.item_id == item_id), None)


# ------------------------------------------------------------------------------- cartões e histórico (S01C-F1-04)
NO_EVALUATION = "sem avaliação"
UNCALIBRATED_ONLY = "só avaliações sem calibração aprovada (S01C-F1-06)"
# Calibrações APROVADAS e registradas (S01C-F1-06), por (provedor, modelo, digest). D-S01C-2: só avaliador daqui conta
# para ELEGÍVEL. Vazio: a F1-06 ainda não aprovou nenhuma calibração, então hoje nenhuma avaliação torna um run elegível.
APPROVED_CALIBRATIONS: frozenset[tuple[str, str, str]] = frozenset()


def _evaluator_key(evaluation: object) -> tuple[str, str, str]:
    ev = evaluation.get("evaluator") if isinstance(evaluation, dict) else getattr(evaluation, "evaluator", None)
    ev = ev if isinstance(ev, dict) else {}
    return str(ev.get("provider") or ""), str(ev.get("model") or ""), str(ev.get("digest") or "")


def uncalibrated(evaluation: object) -> bool:
    """Avaliador sem calibração aprovada registrada (D-S01C-2, #100): a avaliação aparece, mas não conta para ELEGÍVEL.
    Não depende da confiança declarada: confiança "normal" sem calibração também não conta."""
    return _evaluator_key(evaluation) not in APPROVED_CALIBRATIONS
BEST_RULE_PENDING = "inconclusivo: regra de melhor render ainda não implementada (S01C-F1-07)"


@dataclass(frozen=True)
class Card:
    role: str                    # referência | último render | melhor conhecido
    item: GalleryItem | None
    text: str                    # legenda do item, ou o motivo de não haver item

    @property
    def image(self) -> GalleryImage | None:
        return primary_image(self.item) if self.item is not None else None


# Câmera da miniatura e da comparação: a que mais se aproxima da referência, em ordem FIXA e declarada (não depende da
# ordem dos arquivos no manifest). r2_canto é a câmera mais nova, feita para a referência r2; depois perspectiva,
# frente e cama_3q; o resto em ordem alfabética. Quadro renderizado vem antes da vista crua do SketchUp; entre luzes do
# mesmo quadro, LIGHTING_PREFERENCE. Nunca inventa câmera: só escolhe entre as imagens conferidas do run.
CAMERA_PREFERENCE = ("r2_canto", "perspectiva", "frente", "cama_3q")
LIGHTING_PREFERENCE = ("dia", "entardecer", "noite")
_FRAME = re.compile(r"^(render_)?(?:(dia|entardecer|noite)_)?(.+)$")


def camera_of(key: str) -> tuple[str, str, bool]:
    """`render_entardecer_close_banco` → ("close_banco", "entardecer", True); `perspectiva` → ("perspectiva", "", False).

    O nome do quadro é câmera + luz; a câmera é o que sobra sem o prefixo de render e de luz."""
    m = _FRAME.match(key)
    render, light, camera = (m.group(1), m.group(2), m.group(3)) if m else (None, None, key)
    return camera, light or "", bool(render or light)


def camera_rank(img: GalleryImage) -> tuple:
    camera, light, rendered = camera_of(img.key)
    cam = CAMERA_PREFERENCE.index(camera) if camera in CAMERA_PREFERENCE else len(CAMERA_PREFERENCE)
    lit = LIGHTING_PREFERENCE.index(light) if light in LIGHTING_PREFERENCE else len(LIGHTING_PREFERENCE)
    return (0 if rendered else 1, cam, camera, lit, img.key)


def camera_text(img: GalleryImage | None) -> str:
    """Rótulo humano do quadro: `câmera perspectiva · luz entardecer` (ou `vista perspectiva` sem render)."""
    if img is None:
        return "câmera —"
    camera, light, rendered = camera_of(img.key)
    return f"câmera {camera}" + (f" · luz {light}" if light else "") if rendered else f"vista {camera} (sem render)"


def primary_image(item: GalleryItem) -> GalleryImage | None:
    """Imagem do cartão e do tile: o quadro de menor `camera_rank` entre as imagens conferidas do run."""
    return min(item.images, key=camera_rank, default=None)


def comparison_pair(a: GalleryItem, b: GalleryItem) -> tuple[GalleryImage | None, GalleryImage | None, str]:
    """Quadros para comparar `a` com `b` na MESMA câmera, quando os dois a têm.

    Percorre as câmeras de `a` na ordem fixa e pega a primeira que `b` também tem (mesma luz se houver, senão a luz
    preferida de `b`). Sem câmera em comum, ou com a referência (outra câmera por natureza, VS-R7), cada lado mostra o seu
    quadro principal e a nota diz isso. A nota é vazia quando o par é a câmera preferida de `a`."""
    first = primary_image(a)
    if b.source == REFERENCE or a.source == REFERENCE:
        return first, primary_image(b), ""
    by_cam: dict[tuple[str, bool], list[GalleryImage]] = {}
    for img in b.images:
        cam, _light, rendered = camera_of(img.key)
        by_cam.setdefault((cam, rendered), []).append(img)
    for img in sorted(a.images, key=camera_rank):
        cam, light, rendered = camera_of(img.key)
        if rendered != camera_of(first.key)[2]:                               # render só com render
            continue
        cands = by_cam.get((cam, rendered))
        if cands:
            other = next((c for c in cands if c.key == img.key), min(cands, key=camera_rank))
            note = "" if img is first else (f"{b.item_id} não tem a câmera {camera_of(first.key)[0]}; "
                                            f"os dois têm {cam}")
            return img, other, note
    other = primary_image(b)
    if first is None or other is None:
        return first, other, ""
    return first, other, (f"sem câmera em comum: {b.item_id} não tem {camera_of(first.key)[0]}; "
                          f"comparando câmeras diferentes")


def caption(item: GalleryItem) -> str:
    extra = f" · render {item.render_verdict}" if item.kind == "render" else ""
    return f"{item.item_id} · {item.when()} · {item.source_label}{extra}"


def latest_render(items: Iterable[GalleryItem]) -> GalleryItem | None:
    """"Último render" = o run de render de maior `started_at` (VS-R3), seja qual for o veredito."""
    renders = [i for i in items if i.kind == "render" and (i.started_at or i.day)]
    return max(renders, key=GalleryItem.sort_key, default=None)


def reference_item(items: Iterable[GalleryItem]) -> GalleryItem | None:
    refs = sorted((i for i in items if i.source == REFERENCE), key=GalleryItem.sort_key, reverse=True)
    return next((i for i in refs if i.images), refs[0] if refs else None)


def visual_text(evaluations: Sequence[object]) -> str:
    return f"{len(evaluations)} avaliação(ões)" if evaluations else NO_EVALUATION


def trend_text(item: GalleryItem, evaluations: Sequence[object]) -> str:
    """Tendência por pares na mesma câmera é do S01C-F1-07; até lá, nunca um número inventado."""
    if item.kind != "render":
        return "—"
    return f"inconclusivo ({NO_EVALUATION})" if not evaluations else "inconclusivo (S01C-F1-07)"


def cards(items: Sequence[GalleryItem], evaluations_for=lambda _item: ()) -> list[Card]:
    """Faixa de 3 cartões: referência, último render e melhor conhecido (S01C-R7). Sem avaliação válida não há
    "melhor": o cartão diz `sem avaliação`. Nada aqui é veredito visual."""
    ref = reference_item(items)
    last = latest_render(items)
    out = [Card("referência", ref, caption(ref) if ref else "sem referência registrada para o cômodo"),
           Card("último render", last, caption(last) if last else "nenhum render")]
    renders = [i for i in items if i.kind == "render"]
    if not any(evaluations_for(i) for i in renders):
        out.append(Card("melhor conhecido", None, NO_EVALUATION))
    elif not any(i.eligible(evaluations_for(i)) for i in renders):
        out.append(Card("melhor conhecido", None, "nenhum render elegível (FAIL, build incompleto, sem avaliação ou só avaliações sem calibração)"))
    else:
        out.append(Card("melhor conhecido", None, BEST_RULE_PENDING))
    return out


def _day_of(run_id: str) -> str:
    m = _DAY.match(run_id)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


# ------------------------------------------------------------------------------------------------------- motor
def _engine_image(root: Path, run_dir: Path, rel: str, sha: object, key: str, item: GalleryItem) -> None:
    """VS-R2: imagem só entra com o sha256 do arquivo igual ao do manifest."""
    p = run_dir / rel
    if not isinstance(sha, str) or len(sha) != 64:
        item.rejected.append(f"{key}: sem sha256 no manifest")
        return
    got = file_sha256(p)
    if got is None:
        item.rejected.append(f"{key}: arquivo ausente ({rel})")
    elif got != sha:
        item.rejected.append(f"{key}: sha256 diverge do manifest")
    else:
        item.images.append(GalleryImage(key, p, sha, ENGINE_REPO, p.relative_to(root).as_posix()))


def _build_verdicts(manifest: dict | None) -> dict[str, str]:
    if manifest is None:
        return {k: "ausente" for k in BUILD_KEYS}
    return {"geometria": str(manifest.get("verdict") or "ausente"),
            "funcional": str(manifest.get("functional_verdict") or "ausente"),
            "construção": str(manifest.get("construction_verdict") or "ausente")}


def _skp_sha(manifest: dict | None) -> str:
    for a in (manifest or {}).get("artifacts") or []:
        if isinstance(a, dict) and a.get("role") == "skp":
            return str(a.get("sha256") or "")
    return ""


def _render_build(root: Path, skp: dict) -> dict[str, str]:
    """Vereditos do build que este render usou, SÓ se o vínculo for provado pelo sha256 do .skp nos dois lados.
    Sem prova (sem caminho, sem sha, build ausente, build sem artefato skp, sha diferente) -> `skp não conferido`."""
    def unchecked(why: str) -> dict[str, str]:
        return {k: f"{SKP_UNCHECKED} ({why})" for k in BUILD_KEYS}
    if not skp.get("path"):
        return unchecked("render sem input_skp.path")
    if not skp.get("sha256"):
        return unchecked("render sem input_skp.sha256")
    build = _read_json((root / str(skp["path"])).parent / "run_manifest.json")
    if build is None:
        return unchecked("run_manifest do build ausente ou ilegível")
    if not _skp_sha(build):
        return unchecked("build sem artefato skp com sha256")
    if _skp_sha(build) != skp.get("sha256"):
        return unchecked("sha256 do skp do render difere do build")
    return _build_verdicts(build)


def _engine_render(root: Path, d: Path, m: dict, plant: str, room: str) -> GalleryItem:
    run_id = str(m.get("run_id") or d.name)
    it = GalleryItem(ENGINE, plant, room, run_id, "render", started_at=str(m.get("started_at") or ""),
                     day=str(m.get("started_at") or "")[:10] or _day_of(run_id),
                     render_verdict=str(m["verdict"]) if m.get("verdict") else None,
                     camera_file=str((m.get("render_spec") or {}).get("path") or ""))
    if m.get("status") not in (None, "OK"):
        it.errors.append(f"status do render {m.get('status')}")
    report = _read_json(d / "render_report.json")
    if report is None:
        it.errors.append("render_report.json ausente ou ilegível: veredito do render não conferido")
    elif report.get("verdict") != m.get("verdict"):
        it.errors.append(f"render_report diz {report.get('verdict')}, render_manifest diz {m.get('verdict')}")
    it.build = _render_build(root, m.get("input_skp") or {})
    for o in m.get("outputs") or []:
        if isinstance(o, dict) and o.get("png"):
            _engine_image(root, d, str(o["png"]), o.get("png_sha256"), str(o.get("key") or o["png"]), it)
        elif isinstance(o, dict):
            it.rejected.append(f"{o.get('key', '?')}: sem png no manifest")
    return it


def _engine_build(root: Path, d: Path, m: dict, plant: str, room: str) -> GalleryItem:
    run_id = str(m.get("run_id") or d.name)
    it = GalleryItem(ENGINE, plant, room, run_id, "build", started_at=str(m.get("started_at") or ""),
                     day=str(m.get("started_at") or "")[:10] or _day_of(run_id), build=_build_verdicts(m))
    if m.get("status") not in (None, "OK"):
        it.errors.append(f"status do build {m.get('status')}")
    for a in m.get("artifacts") or []:
        if isinstance(a, dict) and a.get("role") == "preview" and a.get("path"):
            _engine_image(root, d, str(a["path"]), a.get("sha256"), Path(str(a["path"])).stem, it)
    return it


def load_engine(root: Path, plant: str, room: str) -> tuple[list[GalleryItem], list[str]]:
    """Runs do motor em `artifacts/<planta>_<comodo>/`. Manifest ilegível vira erro, nunca item inventado."""
    base = Path(root) / "artifacts" / f"{plant}_{room}"
    items: list[GalleryItem] = []
    errors: list[str] = []
    if not base.is_dir():
        return items, [f"motor: {base} inexistente"]
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        rm, bm = d / "render_manifest.json", d / "run_manifest.json"
        if rm.is_file():
            m = _read_json(rm)
            if m is None:
                errors.append(f"motor: render_manifest ilegível em {d.name}")
            else:
                items.append(_engine_render(Path(root), d, m, plant, room))
        elif bm.is_file():
            m = _read_json(bm)
            if m is None:
                errors.append(f"motor: run_manifest ilegível em {d.name}")
            else:
                items.append(_engine_build(Path(root), d, m, plant, room))
    return items, errors


# --------------------------------------------------------------------------------------------------- evidence
def _published(run: E.Run, engine_started: dict[str, str]) -> tuple[GalleryItem, set[str]]:
    """Item PUBLICADO a partir do run do evidence, e os run_id do motor que ele representa (VS-R4)."""
    trace = (run.manifest or {}).get("trace") or {}
    rtrace = trace.get("render") or {}
    engine_runs = {Path(str(p)).name for p in (rtrace.get("engine_render_runs") or []) if p}
    if trace.get("engine_run_dir"):
        engine_runs.add(Path(str(trace["engine_run_dir"])).name)
    engine_runs.add(run.run_id)
    render = run.verdict("render")
    kind = "render" if render is not None or run.stage.endswith("-render") else "build"
    started = engine_started.get(run.run_id, "")
    it = GalleryItem(PUBLISHED, run.plant, run.room, run.run_id, kind, started_at=started,
                     day=started[:10] or _day_of(run.run_id), stage=run.stage,
                     render_verdict=str(render.get("verdict")) if render and render.get("verdict") else None,
                     camera_file=run.cameras_rel or str((rtrace.get("render_spec") or {}).get("path") or ""))
    states = run.states()
    it.human_approval = states["HUMAN_APPROVAL"]
    it.build = {"geometria": states["GEOMETRY_PASS"], "funcional": states["FUNCTIONAL_PASS"],
                "construção": str((run.verdict("construction") or {}).get("verdict", "ausente"))}
    if not run.valid:
        it.errors = list(run.errors) or [run.status()]
        it.rejected.append(f"imagens não mostradas: {run.status()}")
        return it, engine_runs
    for f in (run.manifest or {}).get("files") or []:
        rel = str((f or {}).get("path") or "")
        if rel.startswith("views/") and rel.lower().endswith(".png"):
            p = run.path / rel
            it.images.append(GalleryImage(Path(rel).stem, p, str(f.get("sha256")), EVIDENCE_REPO,
                                          p.relative_to(run.path.parents[3]).as_posix()))
    return it, engine_runs


# --------------------------------------------------------------------------------------------- design-library
def load_references(root: Path, plant: str, room: str) -> tuple[list[GalleryItem], list[str]]:
    """Referências registradas para o cômodo. sha256 do arquivo diferente do manifest -> imagem recusada."""
    items: list[GalleryItem] = []
    errors: list[str] = []
    cat = Path(root) / "catalog"
    if not cat.is_dir():
        return items, [f"design-library: {cat} inexistente"]
    for mp in sorted(cat.glob("*/manifest.json")):
        m = _read_json(mp)
        if m is None:
            errors.append(f"design-library: manifest ilegível em {mp.parent.name}")
            continue
        reg = m.get("registration") or {}
        if (reg.get("project_id"), reg.get("room_id")) != (plant, room):
            continue
        ref_id = f"{m.get('reference_id', mp.parent.name)}@v{m.get('version', '?')}"
        at = str(reg.get("registered_at") or "")
        syn = m.get("synthetic")
        it = GalleryItem(REFERENCE, plant, room, ref_id, "reference", started_at=at, day=at[:10],
                         curation=str((m.get("curation") or {}).get("state") or ""), title=str(m.get("title") or ""),
                         camera_file="", synthetic=syn if isinstance(syn, bool) else None,
                         usage=str(m.get("usage") or ""),
                         provenance=str(reg.get("source_type") or (m.get("origin") or {}).get("kind") or ""))
        asset = m.get("asset") or {}
        path, sha = Path(str(asset.get("path") or "")), str(asset.get("sha256") or "")
        got = file_sha256(path) if asset.get("path") else None
        if not asset.get("path") or got is None:
            it.rejected.append(f"{ref_id}: arquivo ausente ({asset.get('path') or 'sem caminho'})")
        elif got != sha:
            it.rejected.append(f"{ref_id}: sha256 diverge do manifest")
        else:
            it.images.append(GalleryImage(str(reg.get("reference_version") or "ref"), path, sha, LIBRARY_REPO,
                                          mp.parent.relative_to(root).as_posix() + "/manifest.json#asset"))
        items.append(it)
    return items, errors


# ---------------------------------------------------------------------------------------------------- montagem
def build_gallery(catalog: E.Catalog | None, engine: Path | None = None, library: Path | None = None,
                  rooms: Iterable[tuple[str, str]] | None = None) -> Gallery:
    """Junta as fontes. Uma fonte indisponível vira erro da galeria; as outras continuam (cada item já diz a fonte)."""
    engine = Path(engine) if engine is not None else engine_dir()
    library = Path(library) if library is not None else library_dir()
    if rooms is None:                                       # sem evidence, os comodos esperados ainda valem
        rooms = catalog.rooms() if catalog else list(E._expected())
    wanted = sorted(set(rooms))
    g = Gallery([], [], {ENGINE: str(engine), REFERENCE: str(library)})
    if catalog is not None:
        g.sources[PUBLISHED] = str(catalog.root)
    else:
        g.errors.append("evidence indisponível: nenhum item PUBLICADO")
    for plant, room in wanted:
        eng, errs = load_engine(engine, plant, room)
        g.errors += errs
        started = {i.item_id: i.started_at for i in eng}
        published_engine: set[str] = set()
        for run in (catalog.runs_of(plant, room) if catalog else []):
            if not run.published:
                continue
            it, ids = _published(run, started)
            g.items.append(it)
            published_engine |= ids
        g.items += [i for i in eng if i.item_id not in published_engine]           # VS-R4: uma vez, como PUBLICADO
        refs, errs = load_references(library, plant, room)
        g.items += refs
        g.errors += errs
    return g


__all__ = ["ENGINE", "IMPORTED", "NO_EVALUATION", "PUBLISHED", "REFERENCE", "SKP_UNCHECKED", "SOURCES",
           "APPROVED_CALIBRATIONS", "UNCALIBRATED_ONLY", "uncalibrated",
           "UNPUBLISHED_BLOCK", "UNPUBLISHED_NOTE", "Card", "Gallery", "GalleryImage", "GalleryItem", "build_gallery",
           "CAMERA_PREFERENCE", "LIGHTING_PREFERENCE", "camera_of", "camera_rank", "camera_text",
           "caption", "cards", "comparison_pair", "engine_dir", "file_sha256", "latest_render", "library_dir", "load_engine",
           "load_references", "primary_image", "reference_item", "trend_text", "visual_text"]
