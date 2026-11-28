"""project_folders.py — pastas de projeto que o usuario escolheu no Chat (FR-02).

Ficam em `preferences.json` (o mesmo arquivo da preferencia do Enter), mais recente primeiro. Pasta que deixou de
existir nao e oferecida. Nao depende de `E:\\Claude\\apps`: essa pasta e so uma sugestao quando existe (profiles.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

KEY = "project_folders"
LIMIT = 8


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(path: Path) -> list[Path]:
    """As pastas lembradas que ainda existem, mais recente primeiro."""
    raw = _read(path).get(KEY, [])
    if not isinstance(raw, list):
        return []
    return [Path(p) for p in raw if isinstance(p, str) and p and Path(p).is_dir()]


def remember(path: Path, folder: Path) -> None:
    """Poe `folder` no topo da lista (sem repetir) e grava de forma atomica, sem mexer nas outras preferencias."""
    data = _read(path)
    current = [p for p in data.get(KEY, []) if isinstance(p, str)] if isinstance(data.get(KEY), list) else []
    folder_s = str(Path(folder).resolve())
    data[KEY] = [folder_s, *[p for p in current if os.path.normcase(p) != os.path.normcase(folder_s)]][:LIMIT]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
