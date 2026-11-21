"""Leitor do benchmark Claude | Qwen | Llama (benchmark_result v1 + comparison.json do agent-runtime). Somente leitura.

Os arquivos aqui sao FIXTURES DE TESTE escritas em tmp_path (modelo `teste-*`). Nao sao resultado de benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_bar import benchmark as bm

BACKENDS = ("claude", "qwen", "llama")


def result(backend, scenario="s1_paginate", repeat=1, outcome="SUCCESS", schema="benchmark_result/1.0", **extra):
    r = {"schema": schema, "run_id": f"{backend}-{scenario}-r{repeat}", "scenario": scenario, "repeat": repeat, "backend": backend,
         "provider": "teste", "model": f"teste-{backend}", "outcome": outcome, "functional_success": outcome == "SUCCESS",
         "infra_failure": False, "attempts": 1, "duration_s": 5.0, "tool_calls": 4, "tool_errors": 0,
         "tests_after": {"passed": 4, "failed": 0, "passed_all": True}, "hidden_tests": {"passed": 8, "failed": 0, "passed_all": True},
         "campo_futuro": {"ignorado": True}}
    r.update(extra)
    return r


def backend_summary(backend, success=3, runs=3, recommendation="autonomo_controlado"):
    return {"backend": backend, "model": f"teste-{backend}", "provider": "teste", "success": success, "runs": runs, "infra_failures": 0,
            "contract_violations": 0, "visible_tests_pass_runs": success, "hidden_tests_pass_runs": success, "median_attempts": 1,
            "median_duration_s": 7.5, "median_tool_calls": 5, "tool_error_rate": 0.0, "median_prompt_tokens": 1000,
            "median_completion_tokens": 100, "max_gpu_mem_used_mib_system": 2048.0, "human_intervention_runs": 0,
            "per_scenario": {"s1_paginate": {"success": success, "runs": runs}}, "recommendation": recommendation, "campo_novo": 1}


def make_bench(root: Path, backends=BACKENDS, runs=3, comparison=True, results=True) -> Path:
    b = root / "benchmark"
    b.mkdir(parents=True)
    if comparison:
        (b / "comparison.json").write_text(json.dumps({"backends": {k: backend_summary(k, runs=runs, success=runs) for k in backends},
                                                       "rule": ["x"]}), encoding="utf-8")
        (b / "comparison.md").write_text("# comparacao\n", encoding="utf-8")
    for k in backends:
        for i in range(1, runs + 1):
            d = b / k / "runs" / f"s1_paginate_r{i}"
            d.mkdir(parents=True)
            if results:
                (d / "result.json").write_text(json.dumps(result(k, repeat=i)), encoding="utf-8")
    return b


def test_no_source_is_said_explicitly():
    v = bm.collect_benchmark(None)
    assert v.state == "sem_fonte" and "AGENT_BAR_BENCHMARK_DIR" in v.reason and v.backends == []


def test_valid_benchmark_is_read_with_origin_commit_and_links(tmp_path):
    make_bench(tmp_path)
    v = bm.collect_benchmark(tmp_path, origin_commit="92acdcdd8cd8dd6a69152c79f5bd304280733235")
    assert v.state == "lido" and v.origin_commit.startswith("92acdcd") and v.comparison_md.name == "comparison.md"
    assert [b.backend for b in v.backends] == ["claude", "qwen", "llama"]
    c = v.backends[0]
    assert (c.success, c.runs, c.visible_pass, c.hidden_pass, c.recommendation) == (3, 3, 3, 3, "autonomo_controlado")
    assert (c.median_attempts, c.median_duration_s, c.median_tool_calls, c.max_gpu_mem_mib) == (1, 7.5, 5, 2048.0)
    assert len(c.results) == 3 and all(r.path.name == "result.json" for r in c.results) and c.problems == []
    assert c.results[0].hidden_passed == 8 and c.results[0].outcome == "SUCCESS"


def test_the_benchmark_folder_itself_is_also_accepted(tmp_path):
    b = make_bench(tmp_path)
    assert bm.collect_benchmark(b).state == "lido"


def test_unknown_major_is_refused_per_result(tmp_path):
    b = make_bench(tmp_path)
    p = b / "qwen" / "runs" / "s1_paginate_r1" / "result.json"
    p.write_text(json.dumps(result("qwen", schema="benchmark_result/2.0")), encoding="utf-8")
    q = bm.collect_benchmark(tmp_path).backends[1]
    assert len(q.results) == 2 and any("2.0" in x and "não suportado" in x for x in q.problems)


def test_missing_or_broken_comparison_is_unavailable_never_old_numbers(tmp_path):
    make_bench(tmp_path, comparison=False)
    v = bm.collect_benchmark(tmp_path)
    assert v.state == "indisponivel" and "comparison.json" in v.reason and v.backends == []
    (tmp_path / "benchmark" / "comparison.json").write_text("{quebrado", encoding="utf-8")
    assert bm.collect_benchmark(tmp_path).state == "indisponivel"
    (tmp_path / "benchmark" / "comparison.json").write_text(json.dumps({"rule": []}), encoding="utf-8")
    assert bm.collect_benchmark(tmp_path).state == "indisponivel"
    assert bm.collect_benchmark(tmp_path / "nao-existe").state == "indisponivel"


def test_a_backend_missing_from_the_comparison_shows_no_data_for_that_column(tmp_path):
    make_bench(tmp_path, backends=("claude", "llama"))
    v = bm.collect_benchmark(tmp_path)
    q = v.backends[1]
    assert v.state == "lido" and q.backend == "qwen" and q.success is None and "ausente" in q.problems[0]


def test_incomplete_result_is_listed_as_a_problem_not_counted(tmp_path):
    b = make_bench(tmp_path)
    (b / "llama" / "runs" / "s1_paginate_r2" / "result.json").write_text(json.dumps({"schema": "benchmark_result/1.0"}), encoding="utf-8")
    (b / "llama" / "runs" / "s1_paginate_r3" / "result.json").unlink()
    ll = bm.collect_benchmark(tmp_path).backends[2]
    assert len(ll.results) == 1
    assert any("incompleto" in x for x in ll.problems) and any("1 de 3" in x for x in ll.problems)


def test_a_result_of_another_backend_in_a_folder_is_never_mixed_in(tmp_path):
    b = make_bench(tmp_path)
    (b / "claude" / "runs" / "s1_paginate_r1" / "result.json").write_text(json.dumps(result("llama")), encoding="utf-8")
    c = bm.collect_benchmark(tmp_path).backends[0]
    assert len(c.results) == 2 and all(r.backend == "claude" for r in c.results) and any("llama" in x for x in c.problems)


def test_reading_never_writes(tmp_path):
    make_bench(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    bm.collect_benchmark(tmp_path)
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before


# ------------------------------------------------------------------------------------------------ UI
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def _full(bar, s):
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    return bar.details.operations


def test_cards_show_metrics_recommendation_links_and_origin(gui, tmp_path):  # noqa: F811
    bar, app, tmp = gui
    make_bench(tmp_path / "src")
    s = snap(tmp)
    s.benchmark = bm.collect_benchmark(tmp_path / "src", origin_commit="92acdcdd8cd8dd6a69152c79f5bd304280733235")
    ops = _full(bar, s)
    text = ops.cards["benchmark"].text()
    assert "92acdcdd8cd8" in text and "comparison.md" in text and 'href="file:' in text
    for name in ("Claude", "Qwen", "Llama"):
        col = ops.bench_columns[name].text()
        assert "3/3" in col and "autonomo_controlado" in col and "ocultos 3/3" in col
        assert col.count('href="file:') == 3                                      # um link por result.json
    assert "ainda nao publicado" not in text


def test_unavailable_benchmark_shows_the_reason_and_no_numbers(gui, tmp_path):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.benchmark = bm.collect_benchmark(tmp_path / "nada")
    ops = _full(bar, s)
    assert "INDISPONÍVEL" in ops.cards["benchmark"].text()
    assert all("sem dados" in ops.bench_columns[n].text() and "sucesso" not in ops.bench_columns[n].text() for n in ("Claude", "Qwen", "Llama"))
