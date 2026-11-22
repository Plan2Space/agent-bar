"""review_packages.py — pacotes do ciclo de correção (F7.4, R42–R44). Sem Qt. Tudo LOCAL, fora do evidence.

* Pacote para o Claude (`request.md` + `request.json`): run de origem, imagens e câmeras, referência aprovada,
  problemas do relatório de diferenças, restrições geométricas e critérios de aceite. Exige que a correção gere um RUN
  NOVO e diz que o agente pode recomendar, mas não aprovar visualmente.
* Pacote GPT (§5 da P2S-VISUAL-REFERENCE-LOOP): briefing, planta, medidas, imagens atuais (copiadas), referências
  aprovadas e uma pergunta objetiva, em `requests/gpt/<planta>/<cômodo>/<etapa>/<run>/<AAAA-MM-DD>-<assunto>/`. O
  Felipe leva ao ChatGPT; nada é automatizado.
Referência sem origem registrada nunca entra como "aprovada". Nenhum pacote sobrescreve outro.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from agent_bar.cockpit.evidence import Run, ensure_outside, evidence_dir, reference_origin
from agent_bar.cockpit.review_report import CATEGORIES, FIXES, NATURES, DiffReport, run_ref
from agent_bar.cockpit.store import atomic_write_json, data_dir

DEFAULT_CRITERIA = (
    "A correção gera um RUN NOVO no motor (novo run_id); o run de origem e os runs publicados no evidence não são editados.",
    "Mesmas câmeras do cômodo (mesmo cameras*.json ou uma versão nova cujas câmeras comparadas tenham definição idêntica).",
    "GEOMETRY_PASS e FUNCTIONAL_PASS medidos no .skp reaberto em outro processo, sem afrouxar tolerância nenhuma.",
    "Cada problema listado abaixo resolvido ou justificado por escrito, com a imagem da mesma câmera antes e depois.",
    "HUMAN_APPROVAL continua PENDING_HUMAN: o agente pode recomendar, mas nunca declara aprovação visual.",
)


class PackageError(Exception):
    pass


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().encode("ascii", "ignore").decode()).strip("-")
    return s[:48] or "pedido"


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fresh_dir(base: Path) -> Path:
    """Diretório novo: nunca reaproveita (nem sobrescreve) um pacote existente."""
    d, n = base, 2
    while d.exists():
        d = base.with_name(f"{base.name}-{n}")
        n += 1
    d.mkdir(parents=True)
    return d


def approved_reference(path: Path | None) -> dict:
    """Referência aprovada com origem (R44), ou o registro explícito de que não há. Sem origem -> PackageError."""
    if path is None:
        return {"status": "sem referência aprovada"}
    origin = reference_origin(Path(path))
    if origin is None:
        raise PackageError(f"referência sem origem registrada (fonte e licença): {Path(path).name}")
    return {"status": "aprovada", "path": str(path), "sha256": _sha_file(Path(path)), "origin": origin}


def _views(r: Run, cameras: Sequence[str]) -> list[dict]:
    files = {f.get("path"): f.get("sha256") for f in (r.manifest or {}).get("files") or [] if isinstance(f, dict)}
    out = []
    for name in cameras:
        p = r.view_path(name)
        if p is not None:
            out.append({"camera": name, "path": str(p), "sha256": files.get(f"views/{name}.png", "")})
    return out


def _constraints(r: Run) -> dict:
    trace = (r.manifest or {}).get("trace") or {}
    geo = r.verdict("geometry") or {}
    return {"fixture": trace.get("fixture"), "source_pdf": trace.get("source_pdf"), "tolerance": geo.get("tolerance"),
            "geometry_checks": geo.get("checks_total"), "functional_checks": (r.verdict("functional") or {}).get("checks_total"),
            "cameras": r.cameras_rel}


def _findings(rep: DiffReport | None) -> list[dict]:
    if rep is None:
        return []
    out = []
    for f in rep.findings:
        d = {"category": CATEGORIES[f.category], "nature": NATURES[f.nature], "fix": FIXES[f.fix], "camera": f.camera,
             "description": f.description}
        if f.measure is not None:
            d["measure"] = {"value": f.measure.value, "unit": f.measure.unit, "run": f.measure.run,
                            "source": f.measure.source_path, "sha256": f.measure.source_sha256}
        out.append(d)
    return out


class PackageWriter:
    def __init__(self, root: Path | None = None, evidence_root: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = root or data_dir()
        self.evidence_root = evidence_root or evidence_dir()
        self.clock = clock

    def _date(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    # ----------------------------------------------------------------------------------------- Claude
    def claude(self, origin: Run, report: DiffReport | None, cameras: Sequence[str], *, subject: str,
               reference: Path | None = None, extra_criteria: Sequence[str] = (), compared_with: Run | None = None) -> Path:
        if not origin.valid:
            raise PackageError(f"run de origem inválido ou ausente: {origin.status()}")
        if report is None or not report.findings:
            raise PackageError("sem problemas observados: registre o relatório de diferenças antes do pacote")
        ref = approved_reference(reference)
        base = self.root / "requests" / "claude" / origin.plant / origin.room / origin.stage / origin.run_id
        out = _fresh_dir(ensure_outside(base / f"{self._date()}-{_slug(subject)}", self.evidence_root))
        criteria = list(DEFAULT_CRITERIA) + [c for c in extra_criteria if c.strip()]
        data = {"kind": "claude_correction_request", "version": 1, "subject": subject, "created_at": self.clock(),
                "origin_run": {**run_ref(origin), "path": str(origin.path)},
                "compared_with": run_ref(compared_with) if compared_with is not None else None,
                "images": _views(origin, cameras), "reference": ref, "problems": _findings(report),
                "report_id": report.id, "constraints": _constraints(origin), "acceptance_criteria": criteria,
                "must_create_new_run": True, "agent_may_approve_visually": False}
        atomic_write_json(out / "request.json", data)
        (out / "request.md").write_text(_claude_md(data), encoding="utf-8", newline="\n")
        return out

    # ------------------------------------------------------------------------------------------- GPT
    def gpt(self, run: Run, report: DiffReport | None, cameras: Sequence[str], *, subject: str, briefing: str,
            question: str, references: Sequence[Path] = ()) -> Path:
        if not run.valid:
            raise PackageError(f"run inválido ou ausente: {run.status()}")
        if not question.strip():
            raise PackageError("o pacote GPT precisa de uma pergunta objetiva")
        if not briefing.strip():
            raise PackageError("o pacote GPT precisa de um briefing")
        refs = [approved_reference(p) for p in references]                    # sem origem: recusa o pacote inteiro
        base = self.root / "requests" / "gpt" / run.plant / run.room / run.stage / run.run_id
        out = _fresh_dir(ensure_outside(base / f"{self._date()}-{_slug(subject)}", self.evidence_root))
        (out / "imagens").mkdir()
        copied = []
        engine = [v["name"] for v in run.views("engine")]
        for name in list(cameras) + [e for e in engine if e not in cameras]:
            p = run.view_path(name)
            if p is not None:
                dst = out / "imagens" / p.name
                shutil.copy2(p, dst)
                copied.append({"camera": name, "file": f"imagens/{p.name}", "sha256": _sha_file(dst)})
        data = {"kind": "gpt_request", "version": 1, "subject": subject, "created_at": self.clock(),
                "run": {**run_ref(run), "path": str(run.path)}, "briefing": briefing, "question": question,
                "images": copied, "plan": _constraints(run), "measurements": _findings(report),
                "approved_references": refs, "how_to_use": "O Felipe leva este pacote ao ChatGPT. A resposta volta como "
                "arquivo em reviews/llm/ (revisão) ou references/ (imagem, origem 'gerada por GPT a pedido do Felipe'), por PR no "
                "evidence. Não altera nenhum veredito."}
        atomic_write_json(out / "pacote.json", data)
        (out / "briefing.md").write_text(_gpt_md(data), encoding="utf-8", newline="\n")
        return out


def _claude_md(d: dict) -> str:
    o = d["origin_run"]
    lines = [f"# Pedido de correção — {d['subject']}", "",
             f"**Run de origem:** {o['plant']}/{o['room']}/{o['stage']}/{o['run_id']} · motor `{o['engine_commit']}` · "
             f"MANIFEST sha256 `{o['manifest_sha256']}`", "",
             "> A correção gera um **run novo**. Não edite o run de origem nem nada publicado no evidence. "
             "Você pode recomendar, mas não declarar aprovação visual: `HUMAN_APPROVAL` continua `PENDING_HUMAN`.", ""]
    if d.get("compared_with"):
        c = d["compared_with"]
        lines += [f"**Comparado com:** {c['stage']}/{c['run_id']} (MANIFEST `{c['manifest_sha256'][:16]}…`)", ""]
    ref = d["reference"]
    lines += ["## Referência", (f"- aprovada: `{ref['path']}` · origem: {ref['origin']} · sha256 `{ref['sha256'][:16]}…`"
                                if ref["status"] == "aprovada" else "- sem referência aprovada"), "", "## Problemas observados"]
    for i, p in enumerate(d["problems"], 1):
        m = p.get("measure")
        meas = f" · medida {m['value']} {m['unit']} (run {m['run']}: `{m['source']}` sha256 `{m['sha256'][:12]}…`)" if m else ""
        lines.append(f"{i}. [{p['nature']} · {p['category']} · {p['fix']}] {p['description']}"
                     + (f" — câmera `{p['camera']}`" if p["camera"] else "") + meas)
    lines += ["", "## Imagens e câmeras (do run de origem)"]
    lines += [f"- `{v['camera']}`: `{v['path']}` (sha256 `{v['sha256'][:12]}…`)" for v in d["images"]] or ["- nenhuma"]
    c = d["constraints"]
    lines += ["", "## Restrições geométricas", f"- fixture: `{json.dumps(c.get('fixture'), ensure_ascii=False)}`",
              f"- PDF de origem: `{json.dumps(c.get('source_pdf'), ensure_ascii=False)}`",
              f"- tolerâncias: `{json.dumps(c.get('tolerance'), ensure_ascii=False)}`", f"- câmeras: `{c.get('cameras')}`",
              "", "## Critérios de aceite"]
    lines += [f"- {x}" for x in d["acceptance_criteria"]]
    return "\n".join(lines) + "\n"


def _gpt_md(d: dict) -> str:
    r = d["run"]
    lines = [f"# Pedido ao GPT — {d['subject']}", "",
             f"Planta {r['plant']}, cômodo {r['room']}, etapa {r['stage']} (run `{r['run_id']}`, motor `{r['engine_commit'][:12]}`).",
             "", "## Briefing", d["briefing"], "", "## Pergunta objetiva", d["question"], "", "## Imagens atuais (pasta imagens/)"]
    lines += [f"- {i['file']} — câmera `{i['camera']}`" for i in d["images"]] or ["- nenhuma"]
    lines += ["", "## Planta e medidas", f"- fixture: `{json.dumps((d['plan'] or {}).get('fixture'), ensure_ascii=False)}`",
              f"- tolerâncias: `{json.dumps((d['plan'] or {}).get('tolerance'), ensure_ascii=False)}`"]
    lines += [f"- {m['description']}" + (f" ({m['measure']['value']} {m['measure']['unit']})" if m.get("measure") else "")
              for m in d["measurements"]]
    lines += ["", "## Referências aprovadas"]
    lines += [f"- {x['path']} — {x['origin']}" for x in d["approved_references"]] or ["- nenhuma"]
    lines += ["", "---", d["how_to_use"]]
    return "\n".join(lines) + "\n"
