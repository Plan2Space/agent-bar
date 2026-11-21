"""runs.py — execucoes do motor novo lidas de `runs/<run_id>/` (S00-02: C3 manifest, C4 geometry_report, C5 validation_report, C10 layout).

Somente leitura: nunca cria pasta nem arquivo. Evidencia so conta quando o arquivo existe DENTRO da pasta da execucao e o sha256
bate com o manifest. Qualquer falta vira um item em `problems`, mostrado na UI ("sem evidencia"), nunca um sucesso presumido.
O veredito visual e sempre do humano: `visual_verdict` diferente de PENDING_HUMAN e sinalizado.

O resultado do benchmark Claude | Qwen | Llama depende do contrato `benchmark_result` v1 do agent-runtime (Frente B), ainda nao
publicado: ate la a UI mostra "sem dados" e nao le formato nenhum.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_MAJOR = "1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED = ("run_id", "started_at", "engine", "artifacts", "status")
MAX_HASH_BYTES = 512 * 1024 * 1024                    # .skp grande ainda cabe; acima disso nao confere (diz isso)


@dataclass(frozen=True)
class ArtifactCheck:
    role: str
    path: str
    ok: bool
    note: str = ""


@dataclass(frozen=True)
class Step:
    name: str
    status: str
    elapsed_s: float | None


@dataclass
class RunView:
    run_id: str
    status: str
    dir: Path
    started_at: str = ""
    finished_at: str = ""
    engine_repo: str = ""
    engine_commit: str = ""
    engine_dirty: bool | None = None
    fixture_path: str = ""
    steps: list[Step] = field(default_factory=list)
    artifacts: list[ArtifactCheck] = field(default_factory=list)
    verdict: str | None = None                       # PASS | FAIL | BLOCKED do validation_report (None = sem relatorio valido)
    visual_verdict: str | None = None
    reopened: bool | None = None
    preview_path: Path | None = None
    report_paths: dict[str, Path] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)      # checks do validation_report com pass=false
    plan_override: bool | None = None                           # execucao com plano forcado (ex.: erro de planner injetado)

    @property
    def duration_s(self) -> int | None:
        a, b = _iso(self.started_at), _iso(self.finished_at)
        return int((b - a).total_seconds()) if a and b else None


@dataclass
class RunsView:
    state: str                                       # sem_fonte | sem_dados | lido
    reason: str
    source_dir: str = ""
    runs: list[RunView] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkColumn:
    agent: str
    result: dict | None = None


@dataclass
class BenchmarkStatus:
    state: str
    reason: str
    columns: list[BenchmarkColumn] = field(default_factory=list)


def _iso(text: object) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


_HASH_CACHE: dict[tuple[str, int, int], str] = {}      # (caminho, mtime_ns, tamanho) -> sha256: a coleta roda a cada poucos segundos


def _sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _HASH_CACHE:
        return _HASH_CACHE[key]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if len(_HASH_CACHE) > 512:
        _HASH_CACHE.clear()
    _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def _inside(base: Path, rel: str) -> Path | None:
    if not rel or Path(rel).is_absolute():
        return None
    try:
        target = (base / rel).resolve()
        target.relative_to(base.resolve())
    except (OSError, ValueError):
        return None
    return target


def _check_artifact(run_dir: Path, a: object) -> tuple[ArtifactCheck, Path | None]:
    if not isinstance(a, dict):
        return ArtifactCheck("?", "?", False, "entrada de artifact ilegivel"), None
    role, rel, sha = str(a.get("role") or "?"), str(a.get("path") or ""), str(a.get("sha256") or "").lower()
    target = _inside(run_dir, rel)
    if target is None:
        return ArtifactCheck(role, rel, False, "caminho fora da pasta da execucao"), None
    if not target.is_file():
        return ArtifactCheck(role, rel, False, "arquivo ausente"), None
    try:
        if target.stat().st_size > MAX_HASH_BYTES:
            return ArtifactCheck(role, rel, False, "grande demais para conferir sha256"), None
        if _sha256(target) != sha:
            return ArtifactCheck(role, rel, False, "sha256 nao confere com o manifest"), None
    except OSError as exc:
        return ArtifactCheck(role, rel, False, f"ilegivel: {type(exc).__name__}"), None
    return ArtifactCheck(role, rel, True), target


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


MANIFEST_NAMES = ("run_manifest.json", "manifest.json")   # o motor publica run_manifest.json; a S00-02 C3 diz manifest.json


def _manifest_path(run_dir: Path) -> Path:
    return next((run_dir / n for n in MANIFEST_NAMES if (run_dir / n).exists()), run_dir / MANIFEST_NAMES[0])


def read_run(run_dir: Path) -> RunView:
    mpath = _manifest_path(run_dir)
    m = _read_json(mpath)
    if m is None:
        return RunView(run_dir.name, "MANIFEST_ILEGIVEL", run_dir, problems=[f"{mpath.name} ausente ou JSON invalido"])
    version = str(m.get("schema_version") or "")
    if version.split(".")[0] != SUPPORTED_MAJOR:
        return RunView(str(m.get("run_id") or run_dir.name), "MANIFEST_INCOMPATIVEL", run_dir,
                       problems=[f"schema_version {version or '?'} (a barra le {SUPPORTED_MAJOR}.x)"])
    r = RunView(str(m.get("run_id") or run_dir.name), str(m.get("status") or "?"), run_dir,
                started_at=str(m.get("started_at") or ""), finished_at=str(m.get("finished_at") or ""))
    r.report_paths["manifest"] = mpath
    r.problems += [f"manifest sem o campo {k}" for k in _REQUIRED if k not in m]
    if r.run_id != run_dir.name:
        r.problems.append(f"run_id {r.run_id} difere da pasta {run_dir.name}")
    engine = m.get("engine") if isinstance(m.get("engine"), dict) else {}
    r.engine_repo, r.engine_commit = str(engine.get("repo") or ""), str(engine.get("commit") or "")
    r.engine_dirty = engine.get("dirty") if isinstance(engine.get("dirty"), bool) else None
    if not _SHA_RE.match(r.engine_commit):
        r.problems.append("engine.commit nao e um SHA completo: execucao sem HEAD exato")
    if r.engine_dirty:
        r.problems.append("motor com arvore suja (dirty): o HEAD nao descreve o codigo que rodou")
    r.plan_override = m.get("plan_override") if isinstance(m.get("plan_override"), bool) else None
    fixture = m.get("fixture") if isinstance(m.get("fixture"), dict) else {}
    r.fixture_path = str(fixture.get("path") or "")
    r.steps = [Step(str(s.get("name") or "?"), str(s.get("status") or "?"), s.get("elapsed_s") if isinstance(s.get("elapsed_s"), (int, float)) else None)
               for s in (m.get("steps") or []) if isinstance(s, dict)]
    found: dict[str, Path] = {}
    for a in m.get("artifacts") or []:
        check, target = _check_artifact(run_dir, a)
        r.artifacts.append(check)
        if check.ok and target is not None:
            found.setdefault(check.role, target)
        else:
            r.problems.append(f"{check.role} ({check.path}): {check.note}")
    for role in ("geometry_report", "validation_report", "preview"):
        if role not in found and not any(c.role == role for c in r.artifacts):
            r.problems.append(f"{role}: ausente do manifest (sem evidencia)")
    if "preview" in found:
        r.preview_path = found["preview"]
    for role in ("geometry_report", "validation_report", "skp", "log"):
        if role in found:
            r.report_paths[role] = found[role]
    g = _read_json(found["geometry_report"]) if "geometry_report" in found else None
    if g is not None:
        r.reopened = g.get("reopened") if isinstance(g.get("reopened"), bool) else None
        if r.reopened is not True:
            r.problems.append("geometry_report sem reopened=true: medicao invalida (C4)")
    v = _read_json(found["validation_report"]) if "validation_report" in found else None
    if v is not None:
        verdict = v.get("verdict")
        r.verdict = verdict if verdict in ("PASS", "FAIL", "BLOCKED") else None
        if r.verdict is None:
            r.problems.append(f"validation_report com verdict invalido: {verdict!r}")
        r.failed_checks = [f"{c.get('target') or c.get('id')}: esperado {c.get('expected')}, medido {c.get('measured')} (tol {c.get('tolerance')})"
                           for c in (v.get("checks") or []) if isinstance(c, dict) and c.get("pass") is False]
        r.visual_verdict = str(v.get("visual_verdict")) if v.get("visual_verdict") is not None else None
        if r.visual_verdict != "PENDING_HUMAN":
            r.problems.append(f"visual_verdict = {r.visual_verdict!r}; so o humano decide (esperado PENDING_HUMAN)")
    return r


def runs_dir_from_env() -> Path | None:
    raw = os.environ.get("AGENT_BAR_RUNS_DIR")
    return Path(raw) if raw else None


def collect_runs(runs_dir: Path | None, limit: int = 10) -> RunsView:
    if runs_dir is None:
        return RunsView("sem_fonte", "AGENT_BAR_RUNS_DIR nao configurado: nenhuma execucao do motor e exibida")
    if not runs_dir.is_dir():
        return RunsView("sem_dados", f"pasta de execucoes nao existe: {runs_dir}", str(runs_dir))
    try:                                                     # runs/<run_id>/ (C10) ou artifacts/<room>/<run_id>/ (motor): ate 2 niveis
        dirs = []
        for d in runs_dir.iterdir():
            if not d.is_dir():
                continue
            if any((d / n).exists() for n in MANIFEST_NAMES):
                dirs.append(d)
            else:
                dirs += [e for e in d.iterdir() if e.is_dir() and any((e / n).exists() for n in MANIFEST_NAMES)]
    except OSError as exc:
        return RunsView("sem_dados", f"pasta de execucoes ilegivel: {type(exc).__name__}", str(runs_dir))
    if not dirs:
        return RunsView("sem_dados", f"nenhuma execucao com run_manifest.json/manifest.json em {runs_dir}", str(runs_dir))
    runs = [read_run(d) for d in dirs]
    runs.sort(key=lambda r: (r.started_at, r.run_id), reverse=True)
    return RunsView("lido", f"{len(runs)} execucao(oes) lida(s) de {runs_dir}", str(runs_dir), runs[:limit])


def benchmark_status() -> BenchmarkStatus:
    return BenchmarkStatus("sem_dados", "contrato benchmark_result v1 do agent-runtime (Frente B) ainda nao publicado: nenhum resultado e exibido",
                           [BenchmarkColumn("Claude"), BenchmarkColumn("Qwen"), BenchmarkColumn("Llama")])
