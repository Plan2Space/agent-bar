"""F2: as últimas plantas (.skp) e renders, mais recentes primeiro, somente leitura."""
from __future__ import annotations

import os
from pathlib import Path

from agent_bar.cockpit import latest_designs as L


def _touch(p: Path, mtime: float, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


def _tree(tmp_path: Path) -> Path:
    r = tmp_path / "engine"
    _touch(r / "planta_74_suite_01" / "rc6b" / "planta_74_suite_01.skp", 1000)
    _touch(r / "planta_74_suite_01" / "rc7b" / "planta_74_suite_01.skp", 3000)
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "work" / "render_copy.skp", 3100)      # cópia de trabalho
    _touch(r / "planta_74_suite_01" / "rc7b" / "planta_74_suite_01~.skp", 3200)              # backup do SketchUp
    _touch(r / "_archive" / "velho" / "antiga.skp", 9000)                                    # arquivo morto
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "renders" / "entardecer_frente.png", 3300)
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "renders" / "entardecer_cama.png", 3400)
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "renders" / "entardecer_cama.vray.log", 3400)
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "textures" / "p2s_voil.png", 3500)     # textura
    _touch(r / "planta_74_suite_01" / "rc7b-render" / "compare" / "a_vs_b.png", 3600)        # comparação
    _touch(r / "planta_74_suite_01" / "rc6-render" / "renders" / "dia_frente.jpg", 2000)
    return r


def test_plants_are_skp_files_newest_first_without_work_copies_backups_or_archive(tmp_path):
    root = _tree(tmp_path)
    got = L.scan([root])
    assert [(d.run, d.name) for d in got.plants] == [("rc7b", "planta_74_suite_01.skp"),
                                                     ("rc6b", "planta_74_suite_01.skp")]


def test_renders_are_images_in_a_renders_folder_newest_first(tmp_path):
    root = _tree(tmp_path)
    got = L.scan([root])
    assert [(d.run, d.name) for d in got.renders] == [("rc7b-render", "entardecer_cama.png"),
                                                      ("rc7b-render", "entardecer_frente.png"),
                                                      ("rc6-render", "dia_frente.jpg")]


def test_a_missing_root_is_reported_not_an_error_and_the_others_still_count(tmp_path):
    root = _tree(tmp_path)
    got = L.scan([tmp_path / "nao-existe", root])
    assert len(got.plants) == 2 and got.missing and "nao-existe" in got.missing[0]


def test_the_same_file_under_two_roots_appears_once_and_the_limit_holds(tmp_path):
    root = _tree(tmp_path)
    got = L.scan([root, root / "planta_74_suite_01"], limit=1)
    assert len(got.plants) == 1 and len(got.renders) == 1


def test_scanning_writes_nothing(tmp_path):
    root = _tree(tmp_path)
    before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    L.scan([root])
    after = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())
    assert before == after


def test_the_plant_thumbnail_is_the_one_sketchup_saved_inside_the_skp(tmp_path):
    import zipfile
    skp = tmp_path / "a.skp"
    with zipfile.ZipFile(skp, "w") as z:
        z.writestr("meta/model_thumbnail.png", b"modelo")
        z.writestr("meta/preview_thumbnail.png", b"preview")
    assert L.skp_thumbnail(skp) == b"preview"
    old = _touch(tmp_path / "antigo.skp", 1, b"nao e zip")                    # .skp antigo / corrompido
    assert L.skp_thumbnail(old) is None
