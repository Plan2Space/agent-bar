"""Fontes SINTÉTICAS da galeria multi-fonte (S01C-F1-03): motor, um run de render publicado no evidence e a
design-library. Os formatos copiam os manifests reais (render_manifest/run_manifest do sketchup-mcp-next, MANIFEST do
evidence, manifest de referência da design-library), sem nenhum dado privado.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.cockpit.evidence_fixture import _wj, png
from tests.cockpit.evidence_fixture import build as build_evidence

PLANT, ROOM = "planta_x", "quarto"
R4 = "20261122-planta_x_quarto-render4"
R4B = "20261122-planta_x_quarto-render4b"
OK_RENDER = "20261123-planta_x_quarto-rc1-render"
OK_BUILD = "20261123-planta_x_quarto-rc1"
OLD_BUILD = "20261122-planta_x_quarto-refine4"
BAD_SHA = "20261123-planta_x_quarto-rc2-render"
UNPUB_FAIL = "20261123-planta_x_quarto-rc3-render"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _build_run(base: Path, run_id: str, started: str, construction: str | None) -> str:
    d = base / run_id
    skp = f"skp:{run_id}".encode()
    (d).mkdir(parents=True, exist_ok=True)
    (d / "planta_x_quarto.skp").write_bytes(skp)
    prev = png(f"{run_id}/preview")
    (d / "preview.png").write_bytes(prev)
    m = {"schema_version": "1.0.0", "run_id": run_id, "started_at": started, "status": "OK", "verdict": "PASS",
         "functional_verdict": "PASS",
         "artifacts": [{"role": "skp", "path": "planta_x_quarto.skp", "sha256": _sha(skp)},
                       {"role": "preview", "path": "preview.png", "sha256": _sha(prev)}]}
    if construction is not None:
        m["construction_verdict"] = construction
    _wj(d / "run_manifest.json", m)
    return _sha(skp)


def _render_run(base: Path, run_id: str, started: str, verdict: str, build_id: str, skp_sha: str,
                keys: tuple[str, ...] = ("dia_cama_3q", "noite_cama_3q"), tamper: str = "", *, status: str = "OK",
                report_verdict: str | None = None, report: bool = True) -> None:
    """`tamper`: nome do quadro alterado depois do run, ou "*" para todos. `report_verdict`: o render_report diverge
    do manifest. `report=False`: sem render_report. `skp_sha=""`: input_skp sem sha256."""
    d = base / run_id
    outputs = []
    for k in keys:
        data = png(f"{run_id}/{k}")
        p = d / "renders" / f"{k}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data + (b"x" if tamper in (k, "*") else b""))           # tamper: arquivo mudou depois do run
        outputs.append({"key": k, "png": f"renders/{k}.png", "png_sha256": _sha(data)})
    common = {"schema_version": "1.0.0", "run_id": run_id, "render_id": "rid"}
    skp = {"path": f"artifacts/{PLANT}_{ROOM}/{build_id}/planta_x_quarto.skp"}
    if skp_sha:
        skp["sha256"] = skp_sha
    _wj(d / "render_manifest.json", {**common, "started_at": started, "finished_at": started, "status": status,
                                     "verdict": verdict, "render_spec": {"path": "tests/fixtures/render/x.render.json"},
                                     "input_skp": skp, "outputs": outputs})
    if report:
        _wj(d / "render_report.json", {**common, "report_kind": "render", "verdict": report_verdict or verdict})


def build_engine(root: Path) -> Path:
    base = root / "artifacts" / f"{PLANT}_{ROOM}"
    old = _build_run(base, OLD_BUILD, "2026-11-22T20:47:51Z", None)            # build antigo: sem construction_verdict
    ok = _build_run(base, OK_BUILD, "2026-11-23T04:20:00Z", "PASS")
    _render_run(base, R4, "2026-11-22T22:01:55Z", "FAIL", OLD_BUILD, old)
    _render_run(base, R4B, "2026-11-23T00:54:00Z", "FAIL", OLD_BUILD, old)
    _render_run(base, OK_RENDER, "2026-11-23T04:28:00Z", "PASS", OK_BUILD, ok)
    _render_run(base, BAD_SHA, "2026-11-23T05:22:00Z", "PASS", OK_BUILD, ok, tamper="noite_cama_3q")
    _render_run(base, UNPUB_FAIL, "2026-11-23T06:16:00Z", "FAIL", OK_BUILD, ok)
    (base / "20261123-planta_x_quarto-broken").mkdir()
    (base / "20261123-planta_x_quarto-broken" / "render_manifest.json").write_text("{nao e json", encoding="utf-8")
    return root


# --------------------------------------------------------------- um run negativo por guard (revisão do #88)
REPORT_FAIL = "20261121-planta_x_quarto-n1-render"       # render_manifest PASS, render_report FAIL
STATUS_BLOCKED = "20261121-planta_x_quarto-n2-render"    # status BLOCKED
SKP_MISMATCH = "20261121-planta_x_quarto-n3-render"      # input_skp.sha256 diferente do skp do build
ALL_REJECTED = "20261121-planta_x_quarto-n4-render"      # todos os quadros mudaram depois do run
NO_REPORT = "20261121-planta_x_quarto-n5-render"         # sem render_report.json
NO_SKP_SHA = "20261121-planta_x_quarto-n6-render"        # input_skp sem sha256
NO_BUILD_SKP = "20261121-planta_x_quarto-n7-render"      # build sem artefato skp
NEG_BUILD = "20261121-planta_x_quarto-nbuild"
NEG_BUILD_NOSKP = "20261121-planta_x_quarto-nbuild-noskp"
NEGATIVES = (REPORT_FAIL, STATUS_BLOCKED, SKP_MISMATCH, ALL_REJECTED, NO_REPORT, NO_SKP_SHA, NO_BUILD_SKP)


def build_negative_engine(root: Path) -> Path:
    """Motor só com runs de render PASS em build PASS, cada um com UMA prova faltando ou contraditória."""
    base = root / "artifacts" / f"{PLANT}_{ROOM}"
    ok = _build_run(base, NEG_BUILD, "2026-11-21T01:00:00Z", "PASS")
    _build_run(base, NEG_BUILD_NOSKP, "2026-11-21T01:05:00Z", "PASS")
    m = json.loads((base / NEG_BUILD_NOSKP / "run_manifest.json").read_text(encoding="utf-8"))
    m["artifacts"] = [a for a in m["artifacts"] if a["role"] != "skp"]
    _wj(base / NEG_BUILD_NOSKP / "run_manifest.json", m)
    t = "2026-11-21T02:0{}:00Z"
    _render_run(base, REPORT_FAIL, t.format(1), "PASS", NEG_BUILD, ok, report_verdict="FAIL")
    _render_run(base, STATUS_BLOCKED, t.format(2), "PASS", NEG_BUILD, ok, status="BLOCKED")
    _render_run(base, SKP_MISMATCH, t.format(3), "PASS", NEG_BUILD, "9" * 64)
    _render_run(base, ALL_REJECTED, t.format(4), "PASS", NEG_BUILD, ok, tamper="*")
    _render_run(base, NO_REPORT, t.format(5), "PASS", NEG_BUILD, ok, report=False)
    _render_run(base, NO_SKP_SHA, t.format(6), "PASS", NEG_BUILD, "")
    _render_run(base, NO_BUILD_SKP, t.format(7), "PASS", NEG_BUILD_NOSKP, "8" * 64)
    return root


PUB_OK = "20261121-planta_x_quarto-pubok-render"           # publicado, render PASS, build PASS nos três
PUB_TAMPERED = "20261121-planta_x_quarto-pubbad-render"    # publicado, mas o verify.py recusa (arquivo mudou)


def publish_render(evidence: Path, run_id: str, *, stage: str, render: str = "PASS", construction: str | None = "PASS",
                   tamper: bool = False) -> None:
    """Run de render publicado no evidence sintético (MANIFEST com sha256 de cada arquivo, conferido pelo verify.py)."""
    d = evidence / PLANT / ROOM / stage / run_id
    files: dict[str, bytes] = {}
    for k in ("render_dia_cama_3q",):
        data = png(f"{run_id}/{k}")
        (d / "views").mkdir(parents=True, exist_ok=True)
        (d / "views" / f"{k}.png").write_bytes(data)
        files[f"views/{k}.png"] = data
    files["verdicts/render.json"] = _wj(d / "verdicts" / "render.json", {"kind": "render", "verdict": render})
    files["verdicts/geometry.json"] = _wj(d / "verdicts" / "geometry.json", {"kind": "geometry", "verdict": "PASS"})
    files["verdicts/functional.json"] = _wj(d / "verdicts" / "functional.json", {"kind": "functional", "verdict": "PASS"})
    if construction is not None:
        files["verdicts/construction.json"] = _wj(d / "verdicts" / "construction.json",
                                                  {"kind": "construction", "verdict": construction})
    cams = evidence / PLANT / ROOM / "cameras.json"
    _wj(d / "MANIFEST.json", {"plant": PLANT, "room": ROOM, "stage": stage, "run_id": run_id,
                              "trace": {"engine_run_dir": f"artifacts/{PLANT}_{ROOM}/{run_id}"},
                              "views_render": {"cameras": f"{PLANT}/{ROOM}/cameras.json",
                                               "cameras_sha256": _sha(cams.read_bytes())},
                              "views": [], "files": [{"path": k, "sha256": _sha(v), "bytes": len(v)}
                                                     for k, v in sorted(files.items())]})
    if tamper:                                                                  # mudou depois de publicado
        v = d / "views" / "render_dia_cama_3q.png"
        v.write_bytes(v.read_bytes() + b"x")
    idx = json.loads((evidence / "index.json").read_text(encoding="utf-8"))
    idx["plants"][PLANT][ROOM][stage] = {"current_run": run_id, "runs": [run_id]}
    _wj(evidence / "index.json", idx)


def build_negatives(tmp: Path) -> tuple[Path, Path, Path]:
    """Evidence com o render4, um publicado PASS e um publicado adulterado; motor só com os runs negativos."""
    ev = build_evidence(tmp / "ev")
    publish_render4(ev)
    publish_render(ev, PUB_OK, stage="P1-render")
    publish_render(ev, PUB_TAMPERED, stage="P2-render", tamper=True)
    return ev, build_engine_negative(tmp / "engine"), build_library(tmp / "dl", tmp / "assets")


build_engine_negative = build_negative_engine


def publish_render4(evidence: Path) -> None:
    """Publica o render4 (+ render4b) no evidence sintético, como o PR real fez: verdicts/render.json FAIL."""
    d = evidence / PLANT / ROOM / "R4-render" / R4
    files: dict[str, bytes] = {}
    for k in ("render_dia_cama_3q", "render_noite_cama_3q"):
        data = png(f"published/{k}")
        p = d / "views" / f"{k}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files[f"views/{k}.png"] = data
    files["verdicts/render.json"] = _wj(d / "verdicts" / "render.json", {"kind": "render", "verdict": "FAIL"})
    files["verdicts/geometry.json"] = _wj(d / "verdicts" / "geometry.json", {"kind": "geometry", "verdict": "PASS"})
    files["verdicts/functional.json"] = _wj(d / "verdicts" / "functional.json", {"kind": "functional", "verdict": "PASS"})
    cams = evidence / PLANT / ROOM / "cameras.json"
    _wj(d / "MANIFEST.json", {"plant": PLANT, "room": ROOM, "stage": "R4-render", "run_id": R4,
                              "trace": {"engine_run_dir": f"artifacts/{PLANT}_{ROOM}/{R4}",
                                        "render": {"engine_render_runs": [f"artifacts/{PLANT}_{ROOM}/{R4}",
                                                                          f"artifacts/{PLANT}_{ROOM}/{R4B}"]}},
                              "views_render": {"cameras": f"{PLANT}/{ROOM}/cameras.json",
                                               "cameras_sha256": _sha(cams.read_bytes())},
                              "views": [], "files": [{"path": k, "sha256": _sha(v), "bytes": len(v)}
                                                     for k, v in sorted(files.items())]})
    idx = json.loads((evidence / "index.json").read_text(encoding="utf-8"))
    idx["plants"][PLANT][ROOM]["R4-render"] = {"current_run": R4, "runs": [R4]}
    del idx["plants"][PLANT][ROOM]["C"]                                         # sem o run citado e ausente
    _wj(evidence / "index.json", idx)


def build_library(root: Path, assets: Path) -> Path:
    assets.mkdir(parents=True, exist_ok=True)
    good = png("ref-r2")
    (assets / "r2.png").write_bytes(good)
    (assets / "r1.png").write_bytes(png("ref-r1") + b"mudou")                  # sha256 do manifest e de antes

    def ref(rid: str, path: Path, sha: str, room: str = ROOM) -> None:
        _wj(root / "catalog" / rid / "manifest.json", {
            "reference_id": rid, "version": 1, "title": f"referência {rid}", "usage": "conceptual-only",
            "synthetic": True, "asset": {"storage": "local-path", "path": path.as_posix(), "sha256": sha},
            "curation": {"state": "CANDIDATE"},
            "registration": {"project_id": PLANT, "room_id": room, "reference_version": rid[-2:],
                             "source_type": "GPT_GENERATED_ON_REQUEST",
                             "registered_at": "2026-11-23T03:00:00Z"}})

    ref("ref-quarto-r2", assets / "r2.png", _sha(good))
    ref("ref-quarto-r1", assets / "r1.png", _sha(png("ref-r1")))
    ref("ref-sala-r1", assets / "r2.png", _sha(good), room="sala")             # outro cômodo: fora da galeria
    return root


def build_all(tmp: Path) -> tuple[Path, Path, Path]:
    ev = build_evidence(tmp / "ev")
    publish_render4(ev)
    return ev, build_engine(tmp / "engine"), build_library(tmp / "dl", tmp / "assets")

# Avaliação de um avaliador com calibração aprovada, SÓ para teste (#100): o registro real começa vazio.
CAL_KEY = ("teste", "avaliador-calibrado", "c" * 64)
CAL = {"evaluator": {"provider": CAL_KEY[0], "model": CAL_KEY[1], "digest": CAL_KEY[2]}, "confidence": "normal"}
