"""benchmark.py — benchmark Claude | Qwen | Llama do agent-runtime, somente leitura.

Fontes (SPEC S00-05 §4, engineering-specs main 8ed1543; agent-runtime main 92acdcd):
- `benchmark/comparison.json`: agregado por backend (sucesso, testes, medianas, recursos, recomendacao por regra fixa);
- `benchmark/<backend>/runs/<cenario>_r<k>/result.json`: `benchmark_result` v1 por execucao (`schema: "benchmark_result/1.x"`).

Regras do leitor: aceita major 1 e ignora campos desconhecidos; major diferente e recusado por arquivo, com o motivo. Sem
`comparison.json` legivel => INDISPONIVEL, nenhum numero (nunca um valor antigo). Resultado cujo `backend` nao bate com a pasta
nao entra na coluna (sem contaminacao). Nada e escrito.

A pasta vem de AGENT_BAR_BENCHMARK_DIR (ex.: uma copia via `git archive` do runtime); o commit de origem, de
AGENT_BAR_BENCHMARK_COMMIT, aparece na tela. Sem commit informado, a tela diz isso.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

BACKENDS = ("claude", "qwen", "llama")
TITLES = {"claude": "Claude", "qwen": "Qwen", "llama": "Llama"}
_SCHEMA = re.compile(r"^benchmark_result/(\d+)\.\d+$")
_REQUIRED = ("run_id", "scenario", "backend", "outcome")


@dataclass(frozen=True)
class ResultRow:
    run_id: str
    backend: str
    scenario: str
    repeat: int | None
    outcome: str
    attempts: int | None
    duration_s: float | None
    tool_calls: int | None
    visible_passed: int | None
    visible_failed: int | None
    hidden_passed: int | None
    hidden_failed: int | None
    path: Path


@dataclass
class BackendView:
    backend: str
    model: str | None = None
    provider: str | None = None
    success: int | None = None
    runs: int | None = None
    visible_pass: int | None = None
    hidden_pass: int | None = None
    infra_failures: int | None = None
    contract_violations: int | None = None
    median_attempts: float | None = None
    median_duration_s: float | None = None
    median_tool_calls: float | None = None
    tool_error_rate: float | None = None
    median_prompt_tokens: float | None = None
    median_completion_tokens: float | None = None
    max_gpu_mem_mib: float | None = None
    recommendation: str | None = None
    per_scenario: dict = field(default_factory=dict)
    results: list[ResultRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return TITLES.get(self.backend, self.backend)


@dataclass
class BenchmarkView:
    state: str                                   # sem_fonte | indisponivel | lido
    reason: str
    source_dir: str = ""
    origin_commit: str | None = None
    comparison_md: Path | None = None
    backends: list[BackendView] = field(default_factory=list)


def _read(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _tests(d: object) -> tuple[int | None, int | None]:
    d = d if isinstance(d, dict) else {}
    return _num(d.get("passed")), _num(d.get("failed"))


def _results(root: Path, backend: str, view: BackendView) -> None:
    paths = sorted((root / backend / "runs").glob("*/result.json"))
    for p in paths:
        r = _read(p)
        rel = f"{backend}/runs/{p.parent.name}/result.json"
        if r is None:
            view.problems.append(f"{rel}: ilegível")
            continue
        m = _SCHEMA.match(str(r.get("schema") or ""))
        if not m or m.group(1) != "1":
            view.problems.append(f"{rel}: schema {r.get('schema')!r} não suportado (a barra lê benchmark_result/1.x)")
            continue
        missing = [k for k in _REQUIRED if k not in r]
        if missing:
            view.problems.append(f"{rel}: incompleto (falta {', '.join(missing)})")
            continue
        if r["backend"] != backend:                                          # nunca entra na coluna de outro backend
            view.problems.append(f"{rel}: declara backend {r['backend']!r}, fora da pasta {backend}/; ignorado")
            continue
        vp, vf = _tests(r.get("tests_after"))
        hp, hf = _tests(r.get("hidden_tests"))
        view.results.append(ResultRow(str(r["run_id"]), backend, str(r["scenario"]), _num(r.get("repeat")), str(r["outcome"]),
                                      _num(r.get("attempts")), _num(r.get("duration_s")), _num(r.get("tool_calls")), vp, vf, hp, hf, p))
    if view.runs is not None and len(view.results) != view.runs:
        view.problems.append(f"{len(view.results)} de {view.runs} result.json lidos (o comparison.json conta {view.runs})")


def _root(source: Path) -> Path:
    return source if (source / "comparison.json").exists() else source / "benchmark"


def collect_benchmark(source: Path | None, origin_commit: str | None = None) -> BenchmarkView:
    if source is None:
        return BenchmarkView("sem_fonte", "AGENT_BAR_BENCHMARK_DIR não configurado: nenhum resultado de benchmark é exibido")
    root = _root(source)
    comparison = _read(root / "comparison.json")
    backends = comparison.get("backends") if comparison else None
    if not isinstance(backends, dict):
        why = "ausente ou ilegível" if comparison is None else "sem o campo backends"
        return BenchmarkView("indisponivel", f"{root / 'comparison.json'} {why}: nenhum número exibido", str(source), origin_commit)
    view = BenchmarkView("lido", f"lido de {root}", str(source), origin_commit,
                         root / "comparison.md" if (root / "comparison.md").is_file() else None)
    for name in BACKENDS:
        b = BackendView(name)
        s = backends.get(name)
        if not isinstance(s, dict):
            b.problems.append(f"{name} ausente do comparison.json: sem dados")
        else:
            b.model, b.provider = s.get("model"), s.get("provider")
            b.success, b.runs = _num(s.get("success")), _num(s.get("runs"))
            b.visible_pass, b.hidden_pass = _num(s.get("visible_tests_pass_runs")), _num(s.get("hidden_tests_pass_runs"))
            b.infra_failures, b.contract_violations = _num(s.get("infra_failures")), _num(s.get("contract_violations"))
            b.median_attempts, b.median_duration_s = _num(s.get("median_attempts")), _num(s.get("median_duration_s"))
            b.median_tool_calls, b.tool_error_rate = _num(s.get("median_tool_calls")), _num(s.get("tool_error_rate"))
            b.median_prompt_tokens, b.median_completion_tokens = _num(s.get("median_prompt_tokens")), _num(s.get("median_completion_tokens"))
            b.max_gpu_mem_mib = _num(s.get("max_gpu_mem_used_mib_system"))
            b.recommendation = s.get("recommendation") if isinstance(s.get("recommendation"), str) else None
            b.per_scenario = s.get("per_scenario") if isinstance(s.get("per_scenario"), dict) else {}
        _results(root, name, b)
        view.backends.append(b)
    return view


def benchmark_from_env() -> BenchmarkView:
    raw = os.environ.get("AGENT_BAR_BENCHMARK_DIR")
    return collect_benchmark(Path(raw) if raw else None, os.environ.get("AGENT_BAR_BENCHMARK_COMMIT") or None)
