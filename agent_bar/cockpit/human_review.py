"""human_review.py — integração com o fluxo de revisão humana (F7, E6). Sem Qt. NUNCA escreve veredito.

O veredito visual é só do Felipe (P2S-EVIDENCE-v1 §5): um comentário dele no GitHub e um PR no evidence com
`verdicts/human.json`, aprovado pela conta revisora no HEAD. O cockpit só:
  * mostra o estado humano de cada run (PENDING_HUMAN ou o human.json aceito pelo verify.py, com quote e statement_ref);
  * monta o link do run no GitHub, no commit exato do checkout, para ele revisar e comentar;
  * gera um RESUMO LOCAL para a decisão dele (estados, imagens, achados, revisões de LLM), que não é veredito.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from agent_bar.cockpit.evidence import Run, ensure_outside, evidence_dir
from agent_bar.cockpit.library import git_sha
from agent_bar.cockpit.review_report import CATEGORIES, NATURES, DiffReport, run_ref
from agent_bar.cockpit.store import data_dir

EVIDENCE_REPO = "Plan2Space/evidence"
HOW_TO_DECIDE = (
    "Como registrar o veredito (só o Felipe): 1) comente no GitHub a decisão (APPROVED ou CHANGES_REQUESTED) com uma frase "
    "literal; 2) um PR no evidence acrescenta verdicts/human.json citando esse comentário (statement_ref), a frase (quote) "
    "e o commit do motor do run; 3) a conta revisora aprova no HEAD e o CI confere a autoria. O cockpit não escreve esse "
    "arquivo e não declara aprovação."
)


def human_detail(r: Run) -> dict:
    """Estado humano de um run para a UI. Detalhes só quando o human.json foi aceito pelo verify.py."""
    state = r.states()["HUMAN_APPROVAL"]
    out = {"run": r.label, "state": state, "accepted": state in ("APPROVED", "CHANGES_REQUESTED")}
    h = r.verdict("human") if r.present else None
    if out["accepted"] and h is not None:
        out.update({k: h.get(k) for k in ("reviewer_login", "date", "quote", "statement_ref", "run_commit_sha")})
        out["authorship"] = "autoria conferida pelo CI do evidence (check_human_authorship.py), não pelo cockpit"
    elif h is not None:
        out["problem"] = "; ".join(r.human_errors()) or "run inválido no verify.py"
    return out


def github_url(r: Run, evidence_root: Path | None = None) -> str | None:
    """URL do run no GitHub, fixada no commit do checkout (o que o cockpit mostrou é o que ele vai ver)."""
    root = Path(evidence_root or evidence_dir())
    sha = git_sha(root)
    if not sha or not r.present:
        return None
    rel = r.path.resolve().relative_to(root.resolve()).as_posix()
    return f"https://github.com/{EVIDENCE_REPO}/tree/{sha}/{rel}"


def decision_summary(a: Run, b: Run, report: DiffReport | None, *, root: Path | None = None,
                     evidence_root: Path | None = None, clock: Callable[[], float] = time.time) -> Path:
    """Resumo local para o Felipe decidir sobre o run B (comparado com A). Não é veredito; não vai ao evidence."""
    if not b.valid:
        raise ValueError(f"resumo só para run válido: {b.status()}")
    ev = Path(evidence_root or evidence_dir())
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(clock()))
    out = ensure_outside((root or data_dir()) / "review" / "human" / b.plant / b.room / b.stage / f"{b.run_id}-{stamp}.md", ev)
    out.parent.mkdir(parents=True, exist_ok=True)
    rb, ra = run_ref(b), run_ref(a)
    lines = [f"# Resumo para a decisão do Felipe — {b.stage} ({b.run_id})", "",
             "> **Não é veredito.** " + HOW_TO_DECIDE, "",
             f"- Run: `{b.plant}/{b.room}/{b.stage}/{b.run_id}` · motor `{rb['engine_commit']}` · MANIFEST `{rb['manifest_sha256']}`",
             f"- Comparado com: `{a.stage}/{a.run_id}` · MANIFEST `{ra['manifest_sha256'] or '—'}`",
             f"- No GitHub: {github_url(b, ev) or '(checkout sem commit legível)'}", "", "## Estados (separados)"]
    for k, v in b.states().items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Imagens das câmeras fixas"]
    lines += [f"- {v['name']}: `{b.view_path(v['name'])}`" for v in b.views("cameras")] or ["- nenhuma"]
    lines += ["", "## Diferenças registradas"]
    if report is not None and report.findings:
        for f in report.findings:
            m = f" ({f.measure.value:g} {f.measure.unit}, fonte `{f.measure.source_path}`)" if f.measure else ""
            lines.append(f"- [{NATURES[f.nature]} · {CATEGORIES[f.category]}] {f.description}{m}")
    else:
        lines.append("- nenhuma registrada")
    lines += ["", "## Revisões de LLM (consultivas, não decidem)"]
    lines += [f"- `{p.name}`" for p in b.llm_reviews()] or ["- nenhuma"]
    lines += ["", "## Ausências"] + [f"- {m}" for m in b.missing()]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out
