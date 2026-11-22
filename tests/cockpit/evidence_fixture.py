"""Repositorio de evidencias SINTETICO para os testes da revisao visual (sem acesso ao Plan2Space/evidence, que e privado).

O `tools/verify.py` daqui e um substituto minimo com a MESMA interface (check_run, check_index, run_dirs): confere
MANIFEST, sha256/bytes dos arquivos e o human.json. Os testes provam que o leitor DELEGA ao verify.py do checkout e e
fail-closed; o contrato real e exercitado no teste de integracao contra o checkout de verdade (skip sem ele).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FAKE_VERIFY = '''
import hashlib, json
from pathlib import Path

def run_dirs(root):
    return sorted(p for p in Path(root).glob("*/*/*/*") if p.is_dir() and p.name not in ("views", "verdicts", "reviews", "compare", "references"))

def check_run(root, d, engine):
    errs = []
    rel = d.relative_to(root).as_posix()
    try:
        m = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))
    except Exception:
        return [f"{rel}: sem MANIFEST.json"]
    listed = {f["path"]: f for f in m.get("files", [])}
    present = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()} - {"MANIFEST.json", "verdicts/human.json"}
    present = {p for p in present if not p.startswith("reviews/llm/")}
    errs += [f"{rel}: arquivo fora do MANIFEST: {n}" for n in sorted(present - set(listed))]
    errs += [f"{rel}: MANIFEST lista arquivo ausente: {n}" for n in sorted(set(listed) - present)]
    for name, f in listed.items():
        p = d / name
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            errs.append(f"{rel}/{name}: sha256 diverge do MANIFEST")
    cams = Path(root) / m["views_render"]["cameras"]
    if hashlib.sha256(cams.read_bytes()).hexdigest() != m["views_render"]["cameras_sha256"]:
        errs.append(f"{rel}: camera mudou depois do run")
    hp = d / "verdicts" / "human.json"
    if hp.exists():
        h = json.loads(hp.read_text(encoding="utf-8"))
        if h.get("kind") != "human" or h.get("verdict") not in ("APPROVED", "CHANGES_REQUESTED") or not h.get("statement_ref"):
            errs.append(f"{rel}/verdicts/human.json: kind/verdict inválidos")
    return errs

def check_index(root, runs):
    return []
'''

CAM_V1 = [
    {"name": "top", "projection": "parallel", "eye": [1, 1, 30], "target": [1, 1, 0], "up": [0, 1, 0], "height_m": 4.8},
    {"name": "iso_sw", "projection": "perspective", "eye": [-5, -7, 7], "target": [1, 1, 1], "up": [0, 0, 1], "fov": 35},
    {"name": "interno", "projection": "perspective", "eye": [3, 2, 1.6], "target": [0, 2, 1], "up": [0, 0, 1], "fov": 75},
]


def png(seed: str, w: int = 16, h: int = 10) -> bytes:
    """PNG valido e deterministico (cor derivada do nome): o leitor e a UI conseguem abrir de verdade."""
    import struct
    import zlib
    r, g, bl = hashlib.sha256(seed.encode()).digest()[:3]
    raw = b"".join(b"\x00" + bytes((r, g, bl)) * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _wj(p: Path, obj) -> bytes:
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    p.write_bytes(data)
    return data


def build(root: Path, *, v2_changes_interno: bool = True, with_verify: bool = True) -> Path:
    """planta_x/quarto com A (cameras.json, 3 cameras) e B (cameras.v2.json: v1 + 'detalhe'; 'interno' muda se pedido)."""
    root.mkdir(parents=True, exist_ok=True)
    for n in ("README.md", "AGENTS.md", "INDEX.md"):
        (root / n).write_bytes(b"x\n")
    if with_verify:
        (root / "tools").mkdir(exist_ok=True)
        (root / "tools" / "verify.py").write_text(FAKE_VERIFY, encoding="utf-8")
    room = root / "planta_x" / "quarto"
    v1 = _wj(room / "cameras.json", {"image": {"width": 1600, "height": 1000}, "cameras": CAM_V1})
    cams2 = [dict(c) for c in CAM_V1] + [{"name": "detalhe", "projection": "perspective", "eye": [1, 1, 1], "target": [2, 2, 1],
                                         "up": [0, 0, 1], "fov": 50}]
    if v2_changes_interno:
        cams2[2]["fov"] = 60
    v2 = _wj(room / "cameras.v2.json", {"image": {"width": 1600, "height": 1000}, "cameras": cams2})
    _run(root, "A", "run-a", "planta_x/quarto/cameras.json", _sha(v1), [c["name"] for c in CAM_V1])
    _run(root, "B", "run-b", "planta_x/quarto/cameras.v2.json", _sha(v2), [c["name"] for c in cams2], functional=True)
    _wj(root / "index.json", {"plants": {"planta_x": {"quarto": {
        "A": {"current_run": "run-a", "runs": ["run-a"]},
        "B": {"current_run": "run-b", "runs": ["run-b"]},
        "C": {"current_run": "run-c", "runs": ["run-c"]}}}}})                      # C citado e ausente no disco
    return root


def _run(root: Path, stage: str, run_id: str, cams_rel: str, cams_sha: str, cams: list[str], functional: bool = False) -> None:
    d = root / "planta_x" / "quarto" / stage / run_id
    files: dict[str, bytes] = {}
    for c in cams + ["pdf_x_skp"]:
        p = d / "views" / f"{c}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = png(f"{stage}/{c}")
        p.write_bytes(data)
        files[f"views/{c}.png"] = data
    files["verdicts/geometry.json"] = _wj(d / "verdicts" / "geometry.json", {"kind": "geometry", "verdict": "PASS",
                                          "source": {"path": "validation_report.json", "sha256": "a" * 64}})
    files["verdicts/tests.json"] = _wj(d / "verdicts" / "tests.json", {"kind": "tests", "verdict": "PASS"})
    if functional:
        files["verdicts/functional.json"] = _wj(d / "verdicts" / "functional.json", {"kind": "functional", "verdict": "PASS"})
    files["references/briefing.md"] = b"# briefing\n**Fonte:** PDF do Felipe\nLicen\xc3\xa7a: documento interno\n"
    (d / "references").mkdir(parents=True, exist_ok=True)
    (d / "references" / "briefing.md").write_bytes(files["references/briefing.md"])
    files["references/sem_origem.md"] = b"# so um texto\n"
    (d / "references" / "sem_origem.md").write_bytes(files["references/sem_origem.md"])
    views = [{"name": c, "angle": c, "source": "cameras"} for c in cams] + [{"name": "pdf_x_skp", "angle": "prancha", "source": "engine"}]
    _wj(d / "MANIFEST.json", {"plant": "planta_x", "room": "quarto", "stage": stage, "run_id": run_id,
                              "trace": {"engine": {"commit": "c" * 40}, "fixture": {"path": "fx.json", "blob_sha256": "f" * 64},
                                        "source_pdf": {"path": "p.pdf", "page": 1, "sha256": "e" * 64}},
                              "views_render": {"cameras": cams_rel, "cameras_sha256": cams_sha}, "views": views,
                              "files": [{"path": k, "sha256": _sha(v), "bytes": len(v)} for k, v in sorted(files.items())]})
