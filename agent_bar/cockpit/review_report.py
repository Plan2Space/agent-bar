"""review_report.py — relatório de diferenças entre dois runs (F7.3, R41). Sem Qt.

Cada achado tem categoria, natureza (`estetico` × `fisico`, separadas como no gate de construtibilidade do legado:
falha física vence nota estética) e, quando é medida, a fonte (caminho no run + sha256 do MANIFEST). Não existe campo
de similaridade percentual: `validate()` recusa qualquer chave que tente introduzir um. Os relatórios ficam fora do
evidence (`%LOCALAPPDATA%\\agent_bar\\cockpit\\review\\`), com escrita atômica.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_bar.cockpit.evidence import Run, ensure_outside, evidence_dir
from agent_bar.cockpit.store import atomic_write_json, data_dir

CATEGORIES = {
    "silhueta_proporcao": "Silhueta e proporção",
    "mobiliario": "Defeito de mobiliário",
    "material": "Material ausente ou inadequado",
    "composicao": "Composição",
    "luz_render": "Iluminação e render",
    "geometria_medida": "Diferença geométrica medida",
}
NATURES = {"estetico": "estético", "fisico": "físico"}
FIXES = {"codigo": "corrigível por código", "componente_ou_ferramenta": "exige componente ou ferramenta nova"}
_FORBIDDEN = re.compile(r"similar|percent|score|nota|%", re.I)
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class Measure:
    value: float
    unit: str
    source_path: str                       # relativo ao run (ex.: verdicts/geometry.json)
    source_sha256: str                     # sha256 do MANIFEST do run para esse arquivo
    run: str = "B"                         # de qual run vem a medida: "A" ou "B"


@dataclass
class Finding:
    category: str
    nature: str
    description: str
    camera: str = ""
    fix: str = "codigo"
    measure: Measure | None = None

    def errors(self) -> list[str]:
        out = []
        if self.category not in CATEGORIES:
            out.append(f"categoria inválida: {self.category!r}")
        if self.nature not in NATURES:
            out.append(f"natureza precisa ser estetico ou fisico: {self.nature!r}")
        if self.fix not in FIXES:
            out.append(f"classificação de correção inválida: {self.fix!r}")
        if not self.description.strip():
            out.append("descrição vazia")
        if self.category == "geometria_medida" and self.measure is None:
            out.append("diferença geométrica sem medida: registre valor, unidade e a fonte no run")
        if self.measure is not None:
            m = self.measure
            if not m.source_path or not _SHA64.match(m.source_sha256 or ""):
                out.append("medida sem fonte (caminho + sha256 do relatório no run)")
            if not m.unit.strip():
                out.append("medida sem unidade")
            if m.run not in ("A", "B"):
                out.append("medida precisa dizer de qual run vem (A ou B)")
        return out


def run_ref(r: Run) -> dict:
    """Identificação imutável de um run no relatório: etapa, run_id, commit do motor e sha256 do MANIFEST."""
    import hashlib
    mf = r.path / "MANIFEST.json"
    sha = hashlib.sha256(mf.read_bytes()).hexdigest() if mf.is_file() else ""
    commit = (((r.manifest or {}).get("trace") or {}).get("engine") or {}).get("commit", "")
    return {"plant": r.plant, "room": r.room, "stage": r.stage, "run_id": r.run_id, "engine_commit": commit,
            "manifest_sha256": sha, "cameras": r.cameras_rel}


def measure_sources(r: Run) -> list[tuple[str, str]]:
    """Arquivos de relatório do run que podem ser fonte de medida: (caminho, sha256 do MANIFEST)."""
    files = {f.get("path"): f.get("sha256") for f in (r.manifest or {}).get("files") or [] if isinstance(f, dict)}
    return [(p, str(files[p])) for p in sorted(files) if p and p.startswith("verdicts/") and p.endswith(".json")]


@dataclass
class DiffReport:
    id: str
    run_a: dict
    run_b: dict
    created_at: float
    findings: list[Finding] = field(default_factory=list)
    note: str = "Relatório de diferenças observáveis. Não é veredito; não contém similaridade percentual (P2S-VISUAL-REFERENCE-LOOP §3)."

    @classmethod
    def new(cls, a: Run, b: Run, clock: Callable[[], float] = time.time) -> DiffReport:
        if not (a.valid and b.valid):
            raise ValueError("relatório só entre dois runs válidos (verify.py)")
        return cls(id=uuid.uuid4().hex[:12], run_a=run_ref(a), run_b=run_ref(b), created_at=clock())

    def add(self, f: Finding) -> list[str]:
        errs = f.errors()
        if not errs:
            self.findings.append(f)
        return errs

    def counts(self) -> dict[str, int]:
        return {n: sum(1 for f in self.findings if f.nature == n) for n in NATURES}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = f"{self.run_a['stage']} × {self.run_b['stage']}"
        d["counts"] = self.counts()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DiffReport:
        fs = []
        for f in d.get("findings") or []:
            m = f.get("measure")
            fs.append(Finding(**{**{k: v for k, v in f.items() if k != "measure"}, "measure": Measure(**m) if m else None}))
        return cls(id=d["id"], run_a=d["run_a"], run_b=d["run_b"], created_at=d["created_at"], findings=fs,
                   note=d.get("note", ""))


def validate(d: dict) -> list[str]:
    """Erros de um relatório serializado. Recusa campos de similaridade/nota/percentual em qualquer nível."""
    errs: list[str] = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if _FORBIDDEN.search(str(k)):
                    errs.append(f"campo proibido (similaridade/nota sem métrica validada): {path}{k}")
                walk(v, f"{path}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}{i}.")

    walk(d)
    for side in ("run_a", "run_b"):
        r = d.get(side) or {}
        if not r.get("run_id") or not _SHA64.match(str(r.get("manifest_sha256", ""))):
            errs.append(f"{side} sem run_id ou sha256 do MANIFEST")
    try:
        rep = DiffReport.from_dict(d)
    except (KeyError, TypeError) as exc:
        return errs + [f"relatório malformado: {exc}"]
    for i, f in enumerate(rep.findings):
        errs += [f"achado {i}: {e}" for e in f.errors()]
    return errs


class ReportStore:
    def __init__(self, root: Path | None = None, evidence_root: Path | None = None) -> None:
        self.root = (root or data_dir()) / "review"
        self.evidence_root = evidence_root or evidence_dir()

    def path_for(self, rep: DiffReport) -> Path:
        a, b = rep.run_a, rep.run_b
        name = f"{a['stage']}__vs__{b['stage']}-{rep.id}.json"
        return ensure_outside(self.root / a["plant"] / a["room"] / name, self.evidence_root)

    def save(self, rep: DiffReport) -> Path:
        d = rep.to_dict()
        errs = validate(d)
        if errs:
            raise ValueError("; ".join(errs))
        p = self.path_for(rep)
        atomic_write_json(p, d)
        return p

    def load(self, path: Path) -> DiffReport:
        return DiffReport.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def latest(self, a: Run, b: Run) -> DiffReport | None:
        d = self.root / a.plant / a.room
        if not d.is_dir():
            return None
        ra, rb = run_ref(a), run_ref(b)
        best = None
        for p in sorted(d.glob(f"{a.stage}__vs__{b.stage}-*.json")):
            try:
                rep = self.load(p)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if rep.run_a.get("manifest_sha256") == ra["manifest_sha256"] and rep.run_b.get("manifest_sha256") == rb["manifest_sha256"]:
                if best is None or rep.created_at > best.created_at:
                    best = rep
        return best
