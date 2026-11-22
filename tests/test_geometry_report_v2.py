"""C4 geometry_report v1 e v2 (P2S-CONTRACTS 2.0, engineering-specs 6b9d720 §10; motor sketchup-mcp-next e72f742).

v2 traz `continuity` (paredes continuas: solidos, emendas, faces internas). Execucoes `test-*` sao fixtures em tmp_path.
"""
from __future__ import annotations

import hashlib
import json

from agent_bar import runs as rn
from tests.test_runs_evidence import make_run

CONTINUOUS = {"wall_groups": [{"name": "walls", "manifold": True, "coplanar_seam_edges": 0}],
              "internal_faces": [], "cross_group_seams": [], "overlapping_groups": []}
SPLIT = {"wall_groups": [{"name": n, "manifold": True, "coplanar_seam_edges": 0} for n in ("wall_s", "wall_e", "wall_n", "wall_w")],
         "internal_faces": [{"groups": ["wall_s", "wall_e"], "axis": "y", "plane": 0.0, "area_m2": 0.405}] * 4,
         "cross_group_seams": [{"groups": ["wall_s", "wall_e"]}], "overlapping_groups": []}


def with_geometry(tmp_path, run_id, version, continuity=None, drop_continuity=False):
    d = make_run(tmp_path, run_id)
    g = json.loads((d / "geometry_report.json").read_text(encoding="utf-8"))
    g["schema_version"] = version
    if continuity is not None:
        g["continuity"] = continuity
    if drop_continuity:
        g.pop("continuity", None)
    data = json.dumps(g).encode()
    (d / "geometry_report.json").write_bytes(data)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for a in m["artifacts"]:
        if a["role"] == "geometry_report":
            a["sha256"], a["bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def only(tmp_path):
    return rn.collect_runs(tmp_path).runs[0]


def test_v1_report_is_still_read_and_says_continuity_was_not_measured(tmp_path):
    with_geometry(tmp_path, "test-v1", "1.0.0")
    r = only(tmp_path)
    assert r.geometry_version == "1.0.0" and r.continuity is None and r.problems == []


def test_v2_continuous_walls_are_one_solid_without_seams_or_internal_faces(tmp_path):
    with_geometry(tmp_path, "test-v2", "2.0.0", CONTINUOUS)
    c = only(tmp_path).continuity
    assert (c.solids, c.non_manifold, c.seams, c.internal_faces, c.overlaps) == (1, 0, 0, 0, 0) and c.continuous is True


def test_v2_split_walls_show_solids_seams_and_internal_faces(tmp_path):
    with_geometry(tmp_path, "test-split", "2.1.0", SPLIT)
    c = only(tmp_path).continuity
    assert (c.solids, c.seams, c.internal_faces) == (4, 1, 4) and c.continuous is False


def test_non_manifold_group_and_coplanar_seam_edges_count(tmp_path):
    cont = {"wall_groups": [{"name": "walls", "manifold": False, "coplanar_seam_edges": 3}],
            "internal_faces": [], "cross_group_seams": [], "overlapping_groups": [{"groups": ["a", "b"]}]}
    with_geometry(tmp_path, "test-bad", "2.0.0", cont)
    c = only(tmp_path).continuity
    assert (c.non_manifold, c.seams, c.overlaps, c.continuous) == (1, 3, 1, False)


def test_unknown_major_is_a_listed_problem_never_numbers(tmp_path):
    with_geometry(tmp_path, "test-v3", "3.0.0", CONTINUOUS)
    r = only(tmp_path)
    assert r.continuity is None and any("3.0.0" in p and "não suportado" in p for p in r.problems)


def test_v2_without_continuity_is_a_problem_not_a_zero(tmp_path):
    with_geometry(tmp_path, "test-v2-sem", "2.0.0", drop_continuity=True)
    r = only(tmp_path)
    assert r.continuity is None and any("continuity" in p for p in r.problems)


# ------------------------------------------------------------------------------------------------ UI
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def _full(bar, s):
    bar.on_snapshot(s)
    bar.toggle_details()
    bar.details.toggle_full_mode()
    return bar.details.operations


def test_run_card_shows_the_continuity_summary(gui, tmp_path):  # noqa: F811
    bar, app, tmp = gui
    with_geometry(tmp_path / "runs", "test-split", "2.0.0", SPLIT)
    with_geometry(tmp_path / "runs", "test-v1", "1.0.0")
    s = snap(tmp)
    s.runs = rn.collect_runs(tmp_path / "runs")
    text = _full(bar, s).cards["runs"].text()
    assert "continuidade: 4 sólidos · 1 emenda(s) · 4 face(s) interna(s)" in text and "C4 v2.0.0" in text
    assert "continuidade não medida (C4 v1.0.0)" in text
