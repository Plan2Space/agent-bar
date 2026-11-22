"""evidence.py — leitor SOMENTE LEITURA do repositorio Plan2Space/evidence (F7.1, R35–R40, R44). Sem Qt.

O contrato NAO e reescrito aqui: cada run e validado pelo `tools/verify.py` do proprio checkout (importado e executado
sem --engine/--base), fail-closed. Sem verify.py legivel, nada e carregado. Run citado e ausente no disco, ou etapa
esperada sem run publicado, aparece como ausente. Este modulo nunca abre arquivo do checkout para escrita, e
`ensure_outside()` recusa qualquer destino de escrita dentro dele (usado pelos pacotes e relatorios).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

DEFAULT_DIR = Path(r"E:\Claude\apps\evidence")
CAMERA_KEYS = ("projection", "eye", "target", "up", "fov", "height_m")
DEFAULT_EXPECTED = {("planta_74", "suite_01"): ("D1", "D2", "PoC02", "PoC01", "R1")}


class EvidenceError(Exception):
    """Falha que impede ler o repositorio (checkout ausente, verify.py ilegivel, index.json invalido)."""


def evidence_dir() -> Path:
    return Path(os.environ.get("AGENT_BAR_EVIDENCE_DIR") or DEFAULT_DIR)


def ensure_outside(dest: Path, root: Path) -> Path:
    """Destino de escrita do cockpit: recusa se cair dentro do checkout do evidence (runs sao imutaveis)."""
    d, r = Path(dest).resolve(), Path(root).resolve()
    if d == r or r in d.parents:
        raise EvidenceError(f"escrita recusada dentro do evidence (somente leitura): {d}")
    return d


def load_verifier(root: Path) -> ModuleType:
    """Importa `<root>/tools/verify.py` (o mesmo contrato do CI do evidence). Ausente ou ilegivel -> EvidenceError."""
    path = Path(root) / "tools" / "verify.py"
    if not path.is_file():
        raise EvidenceError(f"verify.py ausente em {path}: sem o contrato, nada e carregado")
    name = f"_p2s_evidence_verify_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EvidenceError(f"verify.py ilegivel: {path}")
    mod = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True                                             # sem __pycache__ dentro do checkout
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                                                   # noqa: BLE001 - qualquer falha = recusa
        raise EvidenceError(f"verify.py nao carregou: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.dont_write_bytecode = previous
    for fn in ("check_run", "check_index", "run_dirs"):
        if not callable(getattr(mod, fn, None)):
            raise EvidenceError(f"verify.py sem {fn}(): contrato incompativel")
    return mod


def _read_json(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Run:
    plant: str
    room: str
    stage: str
    run_id: str
    path: Path
    present: bool
    errors: list[str] = field(default_factory=list)
    manifest: dict | None = None
    cameras_rel: str = ""
    cameras: dict[str, dict] = field(default_factory=dict)
    image_size: tuple[int, int] | None = None
    published: bool = True                      # False = etapa esperada sem run no index (ex.: R1 ainda nao publicado)

    @property
    def valid(self) -> bool:
        return self.present and not self.errors

    @property
    def label(self) -> str:
        return f"{self.stage} · {self.run_id}" if self.run_id else f"{self.stage} · (ausente)"

    def status(self) -> str:
        if not self.published:
            return "ausente (não publicado)"
        if not self.present:
            return "ausente no disco"
        return "válido (verify.py)" if self.valid else f"inválido: {len(self.errors)} divergência(s)"

    # ------------------------------------------------------------------------------------------------- conteudo
    def views(self, source: str | None = None) -> list[dict]:
        vs = [v for v in (self.manifest or {}).get("views") or [] if isinstance(v, dict)]
        return [v for v in vs if source is None or v.get("source") == source]

    def view_path(self, name: str) -> Path | None:
        p = self.path / "views" / f"{name}.png"
        return p if self.valid and p.is_file() else None

    def verdict(self, kind: str) -> dict | None:
        return _read_json(self.path / "verdicts" / f"{kind}.json") if self.present else None

    def llm_reviews(self) -> list[Path]:
        d = self.path / "reviews" / "llm"
        return sorted(d.glob("*.md")) if self.present and d.is_dir() else []

    def references(self) -> list[Path]:
        d = self.path / "references"
        return sorted(p for p in d.rglob("*") if p.is_file()) if self.present and d.is_dir() else []

    def compare_images(self) -> dict[str, Path]:
        d = self.path / "compare"
        return {p.stem: p for p in sorted(d.glob("*.png"))} if self.present and d.is_dir() else {}

    def comparison(self) -> dict | None:
        c = (self.manifest or {}).get("comparison")
        return c if isinstance(c, dict) else None

    def render_views(self) -> list[dict]:
        """Vistas de renderizador real (PoC 03). Nenhuma = render ausente; nunca se usa imagem sintetica no lugar."""
        return [v for v in self.views() if v.get("source") == "render" or "render" in str(v.get("name", "")).lower()]

    def pixel_fraction(self, camera: str, other: Run) -> float | None:
        """Fracao de pixels alterados do compare/ deste run contra `other`, se o run a registrou (R40: nao e veredito)."""
        c = self.comparison() or {}
        ag = c.get("against") or {}
        if (ag.get("stage"), ag.get("run_id")) != (other.stage, other.run_id):
            return None
        val = (c.get("changed_pixel_fraction") or {}).get(camera)
        return float(val) if isinstance(val, (int, float)) else None

    def missing(self) -> list[str]:
        """Evidencias ausentes deste run, ditas explicitamente na UI (nunca omitidas)."""
        if not self.published:
            return ["run não publicado no evidence"]
        if not self.present:
            return ["pasta do run ausente no disco"]
        out = []
        if self.verdict("functional") is None:
            out.append("verdicts/functional.json (FUNCTIONAL_PASS)")
        if self.verdict("human") is None:
            out.append("verdicts/human.json (HUMAN_APPROVAL → PENDING_HUMAN)")
        if not self.llm_reviews():
            out.append("reviews/llm/ (VISUAL_REVIEW)")
        if not self.references():
            out.append("references/ (referência visual)")
        if not self.render_views():
            out.append("render de renderizador real (PoC 03)")
        if self.comparison() is None:
            out.append("comparison/compare/ com a etapa anterior")
        return out

    def human_errors(self) -> list[str]:
        return [e for e in self.errors if "verdicts/human" in e]

    def states(self) -> dict[str, str]:
        """Os 4 estados do §6 da P2S-VISUAL-REFERENCE-LOOP, separados (R39). Nada aqui vira aprovacao humana sem
        `verdicts/human.json` aceito pelo verify.py."""
        if not self.present:
            return {"GEOMETRY_PASS": "ausente", "FUNCTIONAL_PASS": "ausente", "VISUAL_REVIEW": "ausente",
                    "HUMAN_APPROVAL": "PENDING_HUMAN"}
        g, f = self.verdict("geometry"), self.verdict("functional")
        n = len(self.llm_reviews())
        human = "PENDING_HUMAN"
        h = self.verdict("human")
        if h is not None:
            human = (str(h.get("verdict")) if self.valid and not self.human_errors()
                     else f"PENDING_HUMAN (human.json inválido: {'; '.join(self.human_errors())[:160] or 'run inválido'})")
        return {"GEOMETRY_PASS": str((g or {}).get("verdict", "ausente")),
                "FUNCTIONAL_PASS": str((f or {}).get("verdict", "ausente")),
                "VISUAL_REVIEW": f"{n} revisão(ões) de LLM (consultivo)" if n else "nenhuma revisão",
                "HUMAN_APPROVAL": human}


@dataclass
class Catalog:
    root: Path
    runs: list[Run]
    errors: list[str]                           # divergencias do check_index (nivel repositorio)

    def rooms(self) -> list[tuple[str, str]]:
        return sorted({(r.plant, r.room) for r in self.runs})

    def runs_of(self, plant: str, room: str) -> list[Run]:
        return [r for r in self.runs if (r.plant, r.room) == (plant, room)]

    def get(self, plant: str, room: str, stage: str, run_id: str) -> Run | None:
        return next((r for r in self.runs if (r.plant, r.room, r.stage, r.run_id) == (plant, room, stage, run_id)), None)


def _expected(env: str | None = None) -> dict[tuple[str, str], tuple[str, ...]]:
    raw = os.environ.get("AGENT_BAR_REVIEW_EXPECTED", "") if env is None else env
    if not raw.strip():
        return dict(DEFAULT_EXPECTED)
    out: dict[tuple[str, str], tuple[str, ...]] = {}
    for part in raw.split(";"):
        m = re.match(r"^\s*([\w.-]+)/([\w.-]+):([\w.,-]+)\s*$", part)
        if m:
            out[(m.group(1), m.group(2))] = tuple(s for s in m.group(3).split(",") if s)
    return out


def load_catalog(root: Path | None = None, expected: dict[tuple[str, str], tuple[str, ...]] | None = None) -> Catalog:
    """Le o index.json e valida cada run com o verify.py do checkout. Falha de leitura do repositorio -> EvidenceError."""
    root = Path(root or evidence_dir())
    if not root.is_dir():
        raise EvidenceError(f"checkout do evidence inexistente: {root}")
    verifier = load_verifier(root)
    idx = _read_json(root / "index.json")
    if idx is None:
        raise EvidenceError(f"index.json ausente ou ilegível em {root}")
    try:
        repo_errors = list(verifier.check_index(root, verifier.run_dirs(root)))
    except Exception as exc:                                                   # noqa: BLE001 - fail-closed
        raise EvidenceError(f"verify.check_index falhou: {type(exc).__name__}: {exc}") from exc
    runs: list[Run] = []
    for plant, rooms in (idx.get("plants") or {}).items():
        for room, stages in (rooms or {}).items():
            for stage, s in (stages or {}).items():
                for run_id in (s or {}).get("runs") or []:
                    runs.append(_load_run(root, verifier, plant, room, stage, str(run_id)))
    for (plant, room), stages in (expected if expected is not None else _expected()).items():
        have = {r.stage for r in runs if (r.plant, r.room) == (plant, room)}
        for st in stages:
            if st not in have:
                runs.append(Run(plant, room, st, "", root / plant / room / st, present=False, published=False,
                                errors=["etapa esperada sem run publicado no index.json"]))
    return Catalog(root, runs, repo_errors)


def _load_run(root: Path, verifier: ModuleType, plant: str, room: str, stage: str, run_id: str) -> Run:
    d = root / plant / room / stage / run_id
    if not d.is_dir():
        return Run(plant, room, stage, run_id, d, present=False, errors=["run citado no index.json e ausente no disco"])
    try:
        errs = list(verifier.check_run(root, d, None))
    except Exception as exc:                                                   # noqa: BLE001 - fail-closed
        errs = [f"verify.check_run falhou: {type(exc).__name__}: {exc}"]
    run = Run(plant, room, stage, run_id, d, present=True, errors=errs, manifest=_read_json(d / "MANIFEST.json"))
    rel = str(((run.manifest or {}).get("views_render") or {}).get("cameras") or "")
    run.cameras_rel = rel
    cams = _read_json(root / rel) if rel else None
    if cams:
        run.cameras = {str(c.get("name")): c for c in cams.get("cameras") or [] if isinstance(c, dict)}
        img = cams.get("image") or {}
        if isinstance(img.get("width"), int) and isinstance(img.get("height"), int):
            run.image_size = (img["width"], img["height"])
    return run


def camera_definition(cam: dict) -> dict[str, Any]:
    return {k: cam.get(k) for k in CAMERA_KEYS}


def compatible_cameras(a: Run, b: Run) -> tuple[list[str], list[tuple[str, str]]]:
    """Pares de camera comparaveis entre dois runs (R38) e as recusadas com o motivo."""
    refused: list[tuple[str, str]] = []
    for r in (a, b):
        if not r.valid:
            return [], [("*", f"{r.label}: {r.status()}")]
    if a.image_size != b.image_size:
        return [], [("*", f"resolução diferente: {a.image_size} × {b.image_size}")]
    names_a = [v["name"] for v in a.views("cameras")]
    names_b = {v["name"] for v in b.views("cameras")}
    pairs: list[str] = []
    for name in names_a + sorted(names_b - set(names_a)):
        if name not in names_b:
            refused.append((name, f"só existe em {a.stage}"))
        elif name not in names_a:
            refused.append((name, f"só existe em {b.stage}"))
        elif name not in a.cameras or name not in b.cameras:
            refused.append((name, "câmera sem definição no cameras*.json"))
        elif camera_definition(a.cameras[name]) != camera_definition(b.cameras[name]):
            refused.append((name, f"definição diferente entre {a.cameras_rel} e {b.cameras_rel}"))
        else:
            pairs.append(name)
    return pairs, refused


_ORIGIN = re.compile(r"\*{0,2}fonte:?\*{0,2}\s*:?\s*(.+)", re.I)
_LICENSE = re.compile(r"licen[cç]a:?\s*(.+)", re.I)


def reference_origin(path: Path) -> str | None:
    """Origem registrada de uma referencia (R44): texto com 'Fonte:' e 'Licença:', ou um `<arquivo>.source.json`
    ao lado de uma imagem com `source` e `license`. Sem origem -> None (nao pode ser 'referencia aprovada')."""
    side = path.with_name(path.name + ".source.json")
    meta = _read_json(side) if side.is_file() else None
    if meta and meta.get("source") and meta.get("license"):
        return f"{meta['source']} · licença {meta['license']}"
    if path.suffix.lower() in (".md", ".txt"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        src, lic = _ORIGIN.search(text), _LICENSE.search(text)
        if src and lic:
            return f"{src.group(1).strip()[:200]} · licença {lic.group(1).strip()[:120]}"
    return None


def is_inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


__all__ = ["Catalog", "EvidenceError", "Run", "compatible_cameras", "ensure_outside", "evidence_dir", "load_catalog",
           "load_verifier", "reference_origin"]
