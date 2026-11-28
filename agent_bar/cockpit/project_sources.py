"""project_sources.py — coletores locais da Central de acompanhamento (§7): catálogo de SPECs e evidências publicadas.

Fontes: os checkouts locais de `engineering-specs` e `evidence`, lidos por `git` SÓ DE LEITURA (`rev-parse`, `ls-tree`,
`show`) no branch padrão do origin (`origin/develop`), nunca o working tree (que pode estar em outra branch ou sujo).
Não faz fetch: a tela diz o SHA lido, e o dado é tão novo quanto o último fetch do checkout.

O status de uma SPEC é texto livre declarado no próprio arquivo e o engineering-specs avisa que o status operacional
fica no Project #1. Por isso é exibido literal, como "declarado na SPEC", e nunca convertido em estado de entrega.
Sem linha de status: desconhecido. Evidência publicada = runs do `index.json` do evidence; o índice `lab/` só conta
quando estiver no branch padrão (antes disso: desconhecido, nunca 0).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_bar.cockpit.acp import CREATE_NO_WINDOW

UNKNOWN = "desconhecido"
DEFAULT_REF = "origin/develop"
WORKSPACE = Path("E:/Claude/apps")
Git = Callable[..., "str | None"]

_STATUS = re.compile(r"^\s*(?:[-*]\s+)?\**status\**\s*:\s*\**\s*(?P<v>\S.*?)\s*$", re.IGNORECASE)
_NOT_SPEC_DIRS = ("templates/", "scripts/", ".github/")
_NOT_SPEC_NAMES = {"README.md", "AGENTS.md", "CLAUDE.md", "INDEX.md"}
_STATUS_LIMIT = 160


def run_git(repo: Path, *args: str) -> str | None:
    """`git -C repo <args>`; None quando falha (checkout ausente, ref inexistente). Só os subcomandos de leitura."""
    if not args or args[0] not in ("rev-parse", "ls-tree", "show"):
        raise ValueError(f"git somente leitura: {args[:1]}")
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=20, check=False,
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def status_line(line: str) -> str | None:
    m = _STATUS.match(line)
    return m.group("v")[:_STATUS_LIMIT] if m else None


def spec_id(path: str) -> str:
    """Id com cara de código (FP-055_x.md → FP-055, P2S-X-001.md → P2S-X-001); nome em snake_case é o nome inteiro."""
    stem = Path(path).stem
    head = stem.split("_", 1)[0]
    return head if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*", head) and any(ch.isdigit() for ch in head) else stem


def spec_matches(path: str, linked: str) -> bool:
    """O campo "SPEC vinculada" do Project cita a SPEC pelo id (às vezes com PR/SHA, ou várias com "/")."""
    sid = spec_id(path)
    return bool(sid) and re.search(rf"(?<![\w-]){re.escape(sid)}(?![\w-])", linked or "") is not None


def _is_spec(path: str) -> bool:
    return path.endswith(".md") and not path.startswith(_NOT_SPEC_DIRS) and Path(path).name not in _NOT_SPEC_NAMES


@dataclass(frozen=True)
class SpecDoc:
    path: str
    title: str
    declared_status: str                    # literal da linha "Status:" do arquivo, ou UNKNOWN


@dataclass(frozen=True)
class SpecCatalog:
    state: str
    read_at: float
    ref: str = DEFAULT_REF
    sha: str = ""
    specs: list[SpecDoc] = field(default_factory=list)
    error: str = ""


def collect_specs(repo: Path = WORKSPACE / "engineering-specs", now: float = 0.0, ref: str = DEFAULT_REF,
                  git: Git = run_git) -> SpecCatalog:
    sha = git(repo, "rev-parse", ref)
    listing = git(repo, "ls-tree", "-r", "--name-only", ref) if sha else None
    if not sha or listing is None:
        return SpecCatalog(UNKNOWN, now, ref, error=f"não consegui ler {ref} em {repo}")
    docs = []
    for path in sorted(p.strip() for p in listing.splitlines() if _is_spec(p.strip())):
        text = git(repo, "show", f"{ref}:{path}") or ""
        title, status = Path(path).stem, None
        for line in text.splitlines():
            if line.startswith("# ") and title == Path(path).stem:
                title = line[2:].strip()
            status = status or status_line(line)
            if status and title != Path(path).stem:
                break
        docs.append(SpecDoc(path, title, status or UNKNOWN))
    return SpecCatalog("ok", now, ref, sha.strip()[:40], docs)


@dataclass(frozen=True)
class EvidenceSnapshot:
    state: str
    read_at: float
    ref: str = DEFAULT_REF
    sha: str = ""
    published_runs: int | None = None       # runs do index.json (planta → suíte → etapa → runs)
    lab_runs: int | None = None             # runs do lab/index.json; None = ainda não está no branch padrão
    note: str = ""
    error: str = ""
    published_run_ids: tuple[str, ...] = ()  # ids dos runs do index.json (a timeline tira a data do prefixo)


def _index_run_ids(data: object) -> tuple[str, ...]:
    ids: list[str] = []
    for plant in (data or {}).get("plants", {}).values() if isinstance(data, dict) else []:
        for suite in plant.values() if isinstance(plant, dict) else []:
            for step in suite.values() if isinstance(suite, dict) else []:
                if isinstance(step, dict):
                    ids += [str(r) for r in step.get("runs") or []]
    return tuple(ids)


def collect_evidence(repo: Path = WORKSPACE / "evidence", now: float = 0.0, ref: str = DEFAULT_REF,
                     git: Git = run_git) -> EvidenceSnapshot:
    sha = git(repo, "rev-parse", ref)
    raw = git(repo, "show", f"{ref}:index.json") if sha else None
    if not sha or raw is None:
        return EvidenceSnapshot(UNKNOWN, now, ref, error=f"não consegui ler {ref}:index.json em {repo}")
    try:
        run_ids = _index_run_ids(json.loads(raw))
    except json.JSONDecodeError as exc:
        return EvidenceSnapshot(UNKNOWN, now, ref, sha.strip()[:40], error=f"index.json inválido: {exc}")
    lab_raw = git(repo, "show", f"{ref}:lab/index.json")
    lab, note = None, f"lab/ ainda não está em {ref}"
    if lab_raw is not None:
        try:
            models = json.loads(lab_raw).get("models") or {}
            lab = sum(len(m.get("runs") or []) for m in models.values() if isinstance(m, dict))
            note = ""
        except (json.JSONDecodeError, AttributeError) as exc:
            note = f"lab/index.json inválido: {exc}"
    return EvidenceSnapshot("ok", now, ref, sha.strip()[:40], len(run_ids), lab, note, published_run_ids=run_ids)
