"""capability_proofs.py — prova de capacidade por (executor, tipo de tarefa) para o roteador Local First (Frente E).

Le so registros que o proprio executor gravou: `benchmark_run.json` (p2s.mcp_compat.run.v1) e `outcome.json`
(p2s.local_outcome.v1). Grava:
- `capability_proofs.json` (p2s.local_capability.v1): um item por (executor_id, task_type) com runs, passes, fails,
  infra_errors, success_rate = passes/(passes+fails) (null sem denominador: sem prova), versoes do motor e as fontes
  com sha256. INFRA_ERROR nunca conta contra o modelo;
- `failures.jsonl` (p2s.local_failure.v1): uma linha por FAIL do benchmark (as do local_job ja saem prontas).

    python -m agent_bar.platform.capability_proofs <pasta> [<pasta> ...] --out <pasta>/capability_proofs.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA = "p2s.local_capability.v1"
CASE_TASK = {"C0": "discovery", "C1": "build", "C2": "build", "C6": "render"}   # caso do benchmark -> tipo de tarefa


def bench_executor_id(run: dict) -> str:
    m = (run.get("versions") or {}).get("model") or {}
    prov = ((run.get("versions") or {}).get("provider") or {}).get("id") or "ollama"
    return f"{prov}:{m.get('id')}@{(m.get('digest') or 'sem-digest')[:8]}"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _bucket(items: dict, key: tuple[str, str]) -> dict:
    return items.setdefault(key, {"executor_id": key[0], "task_type": key[1], "runs": 0, "passes": 0, "fails": 0,
                                  "infra_errors": 0, "engine_sha40": set(), "source": []})


def _failure_from_case(run: dict, case: dict, path: Path) -> dict:
    calls = case.get("tool_calls") or []
    expected = {"C1": {"fixture_ref": "room_001"}, "C2": {"fixture_ref": "plan_alt_5x3_e3a"}}.get(case["id"])
    invalid = None
    if calls and expected:
        got = calls[-1].get("arguments") or {}
        name = next((k for k in sorted(set(expected) | set(got)) if got.get(k) != expected.get(k)), None)
        if name:
            invalid = {"name": name, "sent": got.get(name), "expected": expected.get(name)}
    refusals = case.get("refusals") or []
    klass = ("PROTOCOL" if refusals else "CAPABILITY" if not calls else "CONTEXT" if invalid else "SCHEMA")
    if case["id"] == "C0":
        klass = "CAPABILITY"
    return {"schema": "p2s.local_failure.v1", "executor_id": bench_executor_id(run),
            "task_type": CASE_TASK.get(case["id"], case["id"]), "tool": calls[-1]["name"] if calls else None,
            "failure_class": klass, "error": "; ".join(case.get("reasons") or []), "invalid_argument": invalid,
            "contract_misread": None, "sequence": [c["name"] for c in calls] or None,
            "sent": calls[-1].get("arguments") if calls else None,
            "helpful_example": {"tool": "build_room_sample", "arguments": expected} if expected else None,
            "adapter_fix": None, "skill_hint": None, "source": f"{path.as_posix()}#{case['id']}"}


def aggregate(paths: list[Path]) -> tuple[dict, list[dict]]:
    items: dict[tuple[str, str], dict] = {}
    failures: list[dict] = []
    for path in sorted(paths):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                                                          # ilegivel: fica fora (nada inventado)
        src = {"path": path.as_posix(), "sha256": _sha(path)}
        if doc.get("schema") == "p2s.mcp_compat.run.v1":
            ex = bench_executor_id(doc)
            sha = ((doc.get("versions") or {}).get("engine") or {}).get("commit")
            for case in doc.get("cases") or []:
                task = CASE_TASK.get(case.get("id"))
                st = case.get("status")
                if not task or st not in ("PASS", "FAIL", "INFRA_ERROR"):
                    continue
                b = _bucket(items, (ex, task))
                b["runs"] += 1
                b["passes"] += st == "PASS"
                b["fails"] += st == "FAIL"
                b["infra_errors"] += st == "INFRA_ERROR"
                if sha:
                    b["engine_sha40"].add(sha)
                if src not in b["source"]:
                    b["source"].append(src)
                if st == "FAIL":
                    failures.append(_failure_from_case(doc, case, path))
        elif doc.get("schema") == "p2s.local_outcome.v1" and doc.get("executor_id") and doc.get("task_type"):
            st = doc.get("status")
            if st == "REFUSED":
                continue                                                      # job recusado antes de rodar: nao mede
            b = _bucket(items, (doc["executor_id"], doc["task_type"]))
            b["runs"] += 1
            infra = str(doc.get("error") or "").startswith("INFRA:") or st == "CANCELLED"
            b["passes"] += st == "SUCCEEDED"
            b["infra_errors"] += (st != "SUCCEEDED") and infra
            b["fails"] += (st != "SUCCEEDED") and not infra
            sha = (doc.get("engine") or {}).get("sha40")
            if sha:
                b["engine_sha40"].add(sha)
            b["source"].append(src)
            if isinstance(doc.get("failure"), dict):                          # o arquivo agregado traz todas as falhas
                failures.append(doc["failure"])
    out = []
    for key in sorted(items):
        b = items[key]
        denom = b["passes"] + b["fails"]
        out.append({**b, "engine_sha40": sorted(b["engine_sha40"]),
                    "success_rate": round(b["passes"] / denom, 4) if denom else None})
    return {"schema": SCHEMA, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "items": out}, failures


def find_sources(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for r in roots:
        found += [p for p in Path(r).rglob("benchmark_run.json")]
        found += [p for p in Path(r).rglob("outcome*.json")]
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="agrega prova de capacidade por (executor, task_type)")
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    doc, failures = aggregate(find_sources([Path(r) for r in args.roots]))
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    fail_path = out.with_name("failures.jsonl")
    fail_path.write_text("".join(json.dumps(f, ensure_ascii=False, default=str) + "\n" for f in failures),
                         encoding="utf-8")
    print(json.dumps({"out": str(out), "items": len(doc["items"]), "failures": len(failures)}))
    return 0


def load(path: Path) -> dict[str, Any]:
    """Leitura para quem consome (Studio, roteador): schema conferido."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"schema inesperado: {doc.get('schema')!r}")
    return doc


if __name__ == "__main__":
    sys.exit(main())
