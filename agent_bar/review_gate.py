"""review_gate.py — gate local da Revisão visual contra o evidence REAL (achado H-013 da auditoria do harness).

O Plan2Space/evidence e privado e o agent-bar e publico: os 2 testes de integracao com o evidence real nao rodam no CI
(nada privado entra aqui). Em troca, PR que toca a Revisao visual traz no corpo o resultado desses testes rodados na
maquina do Felipe, travado no HEAD do PR:

    python -m agent_bar.review_gate run --evidence E:\\Claude\\apps\\evidence      # gera o bloco para colar no PR

O CI (`check-pr`) reprova o PR que toca a Revisao visual sem um bloco valido: SHA do evidence (checkout limpo), SHA do
agent-bar igual ao HEAD do PR e "2 passed" (pulado ou falho nao vale). So SHAs e a linha de resultado vao para o PR.
Opcao futura registrada na Issue: job de CI com credencial de leitura do evidence (depende do Felipe).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATHS = (
    "agent_bar/cockpit/evidence.py", "agent_bar/cockpit/review_*.py", "agent_bar/cockpit/human_review.py",
    "agent_bar/cockpit/ui/review_tab.py", "agent_bar/cockpit/ui/diff_panel.py", "agent_bar/cockpit/ui/packages_panel.py",
    "agent_bar/cockpit/ui/human_panel.py",
)
REAL_TESTS = ("tests/cockpit/test_evidence.py::test_real_suite01_runs_and_tampered_copy",
              "tests/cockpit/test_review_tab.py::test_real_suite01_d1_vs_poc01")
BEGIN, END = "<!-- review-gate -->", "<!-- /review-gate -->"
BLOCK_RE = re.compile(re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.S)

Runner = Callable[..., subprocess.CompletedProcess]


def _flags() -> int:
    from agent_bar.cockpit.acp import creation_flags
    return creation_flags()


def _git(repo: Path, *args: str, run: Runner = subprocess.run) -> str:
    return run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
               creationflags=_flags()).stdout.strip()


def touches_review(files: Iterable[str]) -> list[str]:
    return sorted(f for f in files if any(fnmatch.fnmatchcase(f.replace("\\", "/"), p) for p in REVIEW_PATHS))


# ------------------------------------------------------------------------------------------------ local: gerar o bloco
def run_local(evidence: Path, repo: Path = ROOT, run: Runner = subprocess.run) -> dict:
    ev_sha = _git(evidence, "rev-parse", "HEAD", run=run)
    ev_clean = _git(evidence, "status", "--porcelain", run=run) == ""
    ab_sha = _git(repo, "rev-parse", "HEAD", run=run)
    env = {**os.environ, "AGENT_BAR_EVIDENCE_DIR": str(evidence), "QT_QPA_PLATFORM": "offscreen"}
    cp = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", *REAL_TESTS], cwd=str(repo),
             capture_output=True, text=True, env=env, creationflags=_flags())
    lines = [ln for ln in (cp.stdout or "").splitlines() if re.search(r"\d+ (passed|failed|skipped|error)", ln)]
    result = re.sub(r"\s+in [\d.]+s.*$", "", lines[-1].strip("= ")) if lines else f"pytest saiu com {cp.returncode}"
    block = "\n".join([BEGIN, f"evidence: {ev_sha} ({'limpo' if ev_clean else 'SUJO'})", f"agent-bar: {ab_sha}",
                       f"resultado: {result}", END])
    return {"evidence": ev_sha, "evidence_clean": ev_clean, "agent_bar": ab_sha, "result": result,
            "passed": result == f"{len(REAL_TESTS)} passed" and cp.returncode == 0 and ev_clean, "block": block}


# ------------------------------------------------------------------------------------------------ CI: conferir o PR
def check_pr(body: str, changed: Iterable[str], head_sha: str) -> list[str]:
    """Motivos de reprovacao (vazio = ok). So exige o bloco quando o PR toca a Revisao visual."""
    review = touches_review(changed)
    if not review:
        return []
    m = BLOCK_RE.search(body or "")
    where = f"o PR toca a Revisao visual ({', '.join(review)})"
    if not m:
        return [f"{where} e nao traz o bloco do gate local: rode `python -m agent_bar.review_gate run --evidence <checkout>` "
                "e cole a saida no corpo do PR"]
    text = m.group(1)
    errs = []
    ev = re.search(r"^evidence:\s*([0-9a-f]{40})\s*\((limpo|SUJO)\)\s*$", text, re.M)
    ab = re.search(r"^agent-bar:\s*([0-9a-f]{40})\s*$", text, re.M)
    res = re.search(r"^resultado:\s*(.+?)\s*$", text, re.M)
    if not ev:
        errs.append("bloco sem 'evidence: <sha40> (limpo)'")
    elif ev.group(2) != "limpo":
        errs.append("o checkout do evidence estava sujo: rode de novo num checkout limpo")
    if not ab:
        errs.append("bloco sem 'agent-bar: <sha40>'")
    elif ab.group(1) != head_sha:
        errs.append(f"o gate rodou em {ab.group(1)[:12]}, mas o HEAD do PR e {head_sha[:12]}: rode de novo no HEAD atual")
    if not res:
        errs.append("bloco sem 'resultado:'")
    elif res.group(1) != f"{len(REAL_TESTS)} passed":
        errs.append(f"resultado '{res.group(1)}' (precisa ser '{len(REAL_TESTS)} passed': pulado ou falho nao vale)")
    return errs


def _changed_in_ci(base: str, head: str) -> list[str]:
    return _git(ROOT, "diff", "--name-only", f"{base}...{head}").splitlines()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["run"] and "--evidence" in args:
        out = run_local(Path(args[args.index("--evidence") + 1]))
        print(out["block"])
        if not out["passed"]:
            print("\nATENCAO: o gate nao passou (precisa de 2 passed num checkout limpo do evidence).", file=sys.stderr)
        return 0 if out["passed"] else 1
    if args[:1] == ["check-pr"]:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
        pr = event.get("pull_request")
        if not pr:
            print("review-gate: nao e pull_request; nada a conferir")
            return 0
        errs = check_pr(pr.get("body") or "", _changed_in_ci(pr["base"]["sha"], pr["head"]["sha"]), pr["head"]["sha"])
        for e in errs:
            print(f"review-gate: {e}")
        if not errs:
            print("review-gate: ok")
        return 1 if errs else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
