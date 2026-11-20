"""M3 / S00-13: execucoes do motor lidas de `runs/<run_id>/` (S00-02 C3 manifest, C4 geometry_report, C5 validation_report).

Os manifests daqui sao FIXTURES DE TESTE (run_id `test-*`), escritos em tmp_path. Nao sao resultado do motor.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_bar import runs as rn

HEAD = "0123456789abcdef0123456789abcdef01234567"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32           # cabecalho PNG minimo; o leitor nao decodifica, so confere hash


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run(root: Path, run_id: str = "test-room_001-a", *, verdict: str = "PASS", visual: str = "PENDING_HUMAN",
             reopened: bool = True, status: str = "OK", tamper: str | None = None, drop: str | None = None,
             started: str = "2026-11-20T10:00:00Z", extra_artifact: dict | None = None) -> Path:
    d = root / run_id
    d.mkdir(parents=True)
    geometry = json.dumps({"schema_version": "1.0.0", "run_id": run_id, "skp_sha256": _sha(b"skp"), "reopened": reopened,
                           "units": "m"}).encode()
    validation = json.dumps({"schema_version": "1.0.0", "run_id": run_id, "verdict": verdict, "visual_verdict": visual,
                             "checks": [{"id": "c1", "target": "opening.door_01.width", "expected": 0.8, "measured": 0.8,
                                         "tolerance": 0.005, "pass": verdict == "PASS"}]}).encode()
    files = {"skp": ("room_001.skp", b"skp"), "preview": ("preview.png", PNG), "geometry_report": ("geometry_report.json", geometry),
             "validation_report": ("validation_report.json", validation), "log": ("build.log", b"build ok\n")}
    artifacts = []
    for role, (name, data) in files.items():
        if role != drop:
            (d / name).write_bytes(data)
        artifacts.append({"role": role, "path": name, "sha256": _sha(data), "bytes": len(data)})
    if extra_artifact:
        artifacts.append(extra_artifact)
    if tamper:
        (d / files[tamper][0]).write_bytes(b"changed after the manifest")
    manifest = {"schema_version": "1.0.0", "run_id": run_id, "started_at": started, "finished_at": started.replace("10:00", "10:02"),
                "engine": {"repo": "Plan2Space/sketchup-mcp-next", "commit": HEAD, "dirty": False},
                "fixture": {"path": "tests/fixtures/rooms/room_001.json", "sha256": _sha(b"fixture")},
                "sketchup": {"version": "26.0.0", "exe_path": "C:/x/SketchUp.exe", "pid": 1},
                "host": {"os": "Windows", "python": "3.12"}, "artifacts": artifacts,
                "steps": [{"name": "build", "status": "OK", "elapsed_s": 3.5}, {"name": "reopen", "status": "OK", "elapsed_s": 1.0}],
                "status": status}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_no_configured_source_says_so_instead_of_showing_an_empty_success():
    v = rn.collect_runs(None)
    assert v.state == "sem_fonte" and v.runs == [] and "AGENT_BAR_RUNS_DIR" in v.reason


def test_a_missing_or_empty_runs_dir_is_sem_dados(tmp_path):
    assert rn.collect_runs(tmp_path / "nope").state == "sem_dados"
    assert not (tmp_path / "nope").exists()                                          # leitura nunca cria a pasta
    assert rn.collect_runs(tmp_path).state == "sem_dados"


def test_a_complete_run_is_tied_to_its_head_and_its_evidence_is_verified(tmp_path):
    make_run(tmp_path)
    v = rn.collect_runs(tmp_path)
    assert v.state == "lido" and len(v.runs) == 1
    r = v.runs[0]
    assert r.run_id == "test-room_001-a" and r.status == "OK" and r.engine_commit == HEAD and r.engine_repo == "Plan2Space/sketchup-mcp-next"
    assert r.verdict == "PASS" and r.visual_verdict == "PENDING_HUMAN" and r.reopened is True
    assert r.problems == [] and all(a.ok for a in r.artifacts)
    assert r.preview_path == tmp_path / "test-room_001-a" / "preview.png"
    assert r.report_paths["validation_report"].name == "validation_report.json" and r.report_paths["manifest"].name == "manifest.json"
    assert [s.name for s in r.steps] == ["build", "reopen"] and r.duration_s == 120


def test_an_artifact_changed_after_the_manifest_is_not_evidence(tmp_path):
    make_run(tmp_path, tamper="preview")
    r = rn.collect_runs(tmp_path).runs[0]
    assert r.preview_path is None and any("preview" in p and "sha256" in p for p in r.problems)


def test_a_missing_artifact_is_reported_as_missing_evidence(tmp_path):
    make_run(tmp_path, drop="validation_report")
    r = rn.collect_runs(tmp_path).runs[0]
    assert r.verdict is None and any("validation_report" in p and "ausente" in p for p in r.problems)


def test_an_agent_filled_visual_verdict_is_flagged_never_shown_as_approved(tmp_path):
    make_run(tmp_path, visual="APPROVED")
    r = rn.collect_runs(tmp_path).runs[0]
    assert r.visual_verdict == "APPROVED" and any("PENDING_HUMAN" in p for p in r.problems)


def test_a_report_without_reopen_invalidates_a_pass(tmp_path):
    make_run(tmp_path, reopened=False)
    r = rn.collect_runs(tmp_path).runs[0]
    assert r.reopened is False and any("reopened" in p for p in r.problems)


def test_a_failed_run_keeps_its_status_and_verdict(tmp_path):
    make_run(tmp_path, verdict="FAIL", status="OK")
    make_run(tmp_path, "test-room_001-b", status="BUILD_FAILED", drop="preview", started="2026-11-20T11:00:00Z")
    runs = rn.collect_runs(tmp_path).runs
    assert [r.run_id for r in runs] == ["test-room_001-b", "test-room_001-a"]                # mais recente primeiro
    assert runs[0].status == "BUILD_FAILED" and runs[1].verdict == "FAIL"


def test_an_artifact_path_outside_the_run_dir_is_refused(tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    make_run(tmp_path, extra_artifact={"role": "log", "path": "../secret.txt", "sha256": _sha(b"x"), "bytes": 1})
    r = rn.collect_runs(tmp_path).runs[0]
    assert any("fora da pasta" in p for p in r.problems)
    assert not [a for a in r.artifacts if a.path == "../secret.txt" and a.ok]


def test_an_unreadable_or_incompatible_manifest_is_listed_with_the_reason(tmp_path):
    (tmp_path / "test-bad").mkdir()
    (tmp_path / "test-bad" / "manifest.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "test-v2").mkdir()
    (tmp_path / "test-v2" / "manifest.json").write_text(json.dumps({"schema_version": "2.0.0", "run_id": "test-v2"}), encoding="utf-8")
    runs = {r.run_id: r for r in rn.collect_runs(tmp_path).runs}
    assert runs["test-bad"].status == "MANIFEST_ILEGIVEL" and runs["test-bad"].problems
    assert runs["test-v2"].status == "MANIFEST_INCOMPATIVEL" and "2.0.0" in runs["test-v2"].problems[0]


def test_a_head_that_is_not_a_full_sha_is_flagged(tmp_path):
    d = make_run(tmp_path)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["engine"]["commit"] = "abc123"
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    r = rn.collect_runs(tmp_path).runs[0]
    assert any("SHA completo" in p for p in r.problems)


def test_reading_runs_never_writes_anything(tmp_path):
    make_run(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    rn.collect_runs(tmp_path)
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before


def test_benchmark_has_no_data_until_the_runtime_publishes_its_contract():
    b = rn.benchmark_status()
    assert b.state == "sem_dados" and [c.agent for c in b.columns] == ["Claude", "Qwen", "Llama"]
    assert all(c.result is None for c in b.columns) and "benchmark_result" in b.reason


# ------------------------------------------------------------------------------------------------ UI (Central completa)
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def _full(bar, s):
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    return bar.details.operations


def test_full_view_says_no_engine_run_is_shown_without_a_source(gui):  # noqa: F811
    bar, app, tmp = gui
    ops = _full(bar, snap(tmp))
    text = ops.cards["runs"].text()
    assert "SEM FONTE" in text and "AGENT_BAR_RUNS_DIR" in text and ops.preview.pixmap().isNull()
    bench = ops.cards["benchmark"].text()
    assert "Claude" in bench and "Qwen" in bench and "Llama" in bench and "sem dados" in bench


def test_full_view_shows_run_head_verdict_problems_and_the_preview(gui, tmp_path):  # noqa: F811
    from PySide6.QtGui import QImage

    bar, app, tmp = gui
    root = tmp_path / "runs"
    d = make_run(root)
    img = QImage(64, 40, QImage.Format.Format_RGB32)
    img.fill(0x3366AA)
    img.save(str(d / "preview.png"))
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for a in m["artifacts"]:
        if a["role"] == "preview":
            a["sha256"] = _sha((d / "preview.png").read_bytes())
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    make_run(root, "test-room_001-b", verdict="FAIL", tamper="preview", started="2026-11-20T09:00:00Z")
    s = snap(tmp)
    s.runs = rn.collect_runs(root)
    ops = _full(bar, s)
    text = ops.cards["runs"].text()
    assert "test-room_001-a" in text and HEAD[:12] in text and "PASS" in text and "PENDING_HUMAN" in text
    assert "test-room_001-b" in text and "FAIL" in text and "sha256 nao confere" in text
    assert 'href="file:' in text                                                          # relatorios e manifest abrem pelo link
    assert not ops.preview.pixmap().isNull() and "test-room_001-a" in ops.preview_caption.text()
