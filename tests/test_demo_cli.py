"""CLI de evidencia: `--screenshot X.png --full` grava tambem a Central completa (cards de execucoes e benchmark)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_screenshot_full_writes_the_full_view_png(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "EVENT_STORE_DB_PATH", "AGENT_BAR_RUNS_DIR")}
    env.update(QT_QPA_PLATFORM="offscreen", AGENT_BAR_SETTINGS=str(tmp_path / "s.json"), AGENT_BAR_GOV_DIR=str(tmp_path / "gov"),
               PATH=os.path.dirname(sys.executable))                       # sem gh/claude/codex no PATH: coleta rapida e sem rede
    out = tmp_path / "x.png"
    r = subprocess.run([sys.executable, "-m", "agent_bar.app", "--screenshot", str(out), "--full"], env=env, cwd=Path(__file__).parents[1],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-800:]
    assert out.is_file() and (tmp_path / "x_panel.png").is_file() and (tmp_path / "x_full.png").is_file() and (tmp_path / "x_ops.png").is_file()
    assert (tmp_path / "x_full.png").stat().st_size > (tmp_path / "x_panel.png").stat().st_size // 2
