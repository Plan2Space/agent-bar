"""Experiencia de app Windows: instancia unica, log em arquivo, erro antes da UI visivel, launchers so com o venv do checkout."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from agent_bar import startup

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------------------------------ log e erro fatal
def test_log_dir_lives_under_localappdata_and_never_in_the_repo(tmp_path):
    assert startup.log_dir({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "agent_bar" / "logs"
    assert startup.log_dir({"AGENT_BAR_LOG_DIR": str(tmp_path / "x")}) == tmp_path / "x"


def test_startup_log_is_written_to_a_file(tmp_path):
    log = startup.configure_logging(tmp_path)
    import logging
    logging.getLogger("agent_bar").info("barra iniciada pid=%s", os.getpid())
    for h in logging.getLogger("agent_bar").handlers:
        h.flush()
    assert log == tmp_path / "agent_bar.log" and "barra iniciada" in log.read_text(encoding="utf-8")


def test_a_failure_before_the_ui_goes_to_the_log_and_to_a_message_box(tmp_path):
    shown = []
    log = startup.configure_logging(tmp_path)
    try:
        raise RuntimeError("PySide6 quebrado")
    except RuntimeError as exc:
        startup.report_fatal(exc, log, show=lambda title, text: shown.append((title, text)))
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError: PySide6 quebrado" in text and "Traceback" in text
    assert shown and "PySide6 quebrado" in shown[0][1] and str(log) in shown[0][1]


# ------------------------------------------------------------------------------------------------ instancia unica
@pytest.fixture
def qapp():
    pytest.importorskip("PySide6.QtNetwork")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, cond, secs=3.0):
    import time
    end = time.time() + secs
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_second_instance_is_refused_and_asks_the_first_to_come_forward(qapp, tmp_path):
    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    first = SingleInstance(key, tmp_path)
    raised = []
    assert first.acquire(on_activate=lambda: raised.append(True)) is True
    second = SingleInstance(key, tmp_path)
    assert second.acquire(on_activate=lambda: None) is False
    assert _pump(qapp, lambda: raised == [True])
    first.release()
    third = SingleInstance(key, tmp_path)
    assert third.acquire(on_activate=lambda: None) is True                 # liberado = pode abrir de novo
    third.release()


def test_a_stale_lock_from_a_dead_process_does_not_block_the_app(qapp, tmp_path):
    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    (tmp_path / f"{key}.lock").write_text("999999\nagent_bar\nhost\n", encoding="utf-8")   # PID que nao existe
    inst = SingleInstance(key, tmp_path)
    assert inst.acquire(on_activate=lambda: None) is True
    inst.release()


# ------------------------------------------------------------------------------------------------ launchers
def test_powershell_launcher_never_falls_back_to_the_global_python():
    text = (ROOT / "launchers" / "run_agent_bar.ps1").read_text(encoding="ascii")
    assert '$py = "python"' not in text and ".venv" in text and "exit 2" in text


def test_cmd_launcher_uses_the_checkout_venv_pythonw_and_its_own_directory():
    text = (ROOT / "launchers" / "AGENT-BAR.cmd").read_text(encoding="ascii")
    assert r".venv\Scripts\pythonw.exe" in text and "%~dp0" in text and 'cd /d "%REPO%"' in text


@pytest.mark.parametrize("name", ["install_shortcut.ps1", "uninstall_shortcut.ps1", "run_agent_bar.ps1", "AGENT-BAR.cmd"])
def test_launch_scripts_are_ascii_and_never_bypass_the_execution_policy(name):
    raw = (ROOT / "launchers" / name).read_bytes()
    raw.decode("ascii")                                                     # PS 5.1 le .ps1 sem BOM como ANSI: acento quebra
    assert b"Bypass" not in raw


def test_install_shortcut_points_at_the_venv_pythonw_with_working_dir_and_icon():
    text = (ROOT / "launchers" / "install_shortcut.ps1").read_text(encoding="ascii")
    for needle in ("Plan2Space Agent Bar.lnk", r".venv\Scripts\pythonw.exe", "WorkingDirectory", "IconLocation", "agent_bar.ico",
                   "-m agent_bar.app"):
        assert needle in text


def test_the_icon_is_a_real_ico_file_in_the_repo():
    raw = (ROOT / "launchers" / "agent_bar.ico").read_bytes()
    assert raw[:4] == b"\x00\x00\x01\x00" and int.from_bytes(raw[4:6], "little") >= 3   # cabecalho ICO com >= 3 tamanhos


# ------------------------------------------------------------------------------------------------ fontes locais (launchers/local.env)
def test_local_env_fills_only_agent_bar_sources_and_never_overrides_the_environment(tmp_path):
    f = tmp_path / "local.env"
    f.write_text("# comentario\nEVENT_STORE_DB_PATH=C:/x/store.db\nAGENT_BAR_RUNS_DIR = C:/runs \nAGENT_BAR_ORG=Ja\nPATH=C:/evil\n"
                 "GH_TOKEN=nao\nlinha sem igual\n", encoding="utf-8")
    env = {"AGENT_BAR_ORG": "Plan2Space"}
    loaded = startup.load_local_env(f, env)
    assert env["EVENT_STORE_DB_PATH"] == "C:/x/store.db" and env["AGENT_BAR_RUNS_DIR"] == "C:/runs"
    assert env["AGENT_BAR_ORG"] == "Plan2Space" and "PATH" not in env and "GH_TOKEN" not in env
    assert sorted(loaded) == ["AGENT_BAR_RUNS_DIR", "EVENT_STORE_DB_PATH"]


def test_a_missing_local_env_changes_nothing(tmp_path):
    env: dict[str, str] = {}
    assert startup.load_local_env(tmp_path / "nope.env", env) == [] and env == {}
