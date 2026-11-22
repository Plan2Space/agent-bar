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
    for needle in ("P2S Studio.lnk", r".venv\Scripts\pythonw.exe", "WorkingDirectory", "IconLocation", "agent_bar.ico",
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


def test_install_shortcut_removes_the_old_name_and_uninstall_covers_both():
    install = (ROOT / "launchers" / "install_shortcut.ps1").read_text(encoding="ascii")
    assert "Plan2Space Agent Bar.lnk" in install and "Remove-Item -LiteralPath $oldLnk" in install
    assert install.index('"P2S Studio.lnk"') < install.index("$lnk.Save()")
    uninstall = (ROOT / "launchers" / "uninstall_shortcut.ps1").read_text(encoding="ascii")
    assert '"P2S Studio.lnk"' in uninstall and '"Plan2Space Agent Bar.lnk"' in uninstall


def test_the_single_instance_key_did_not_change_with_the_rename():
    """Versao antiga (chave plan2space-agent-bar) e nova precisam se excluir: renomear a chave deixaria as duas rodarem juntas."""
    pytest.importorskip("PySide6.QtNetwork")
    from agent_bar.single_instance import DEFAULT_KEY
    assert DEFAULT_KEY == "plan2space-agent-bar"


def test_running_instance_records_its_version_and_a_newer_checkout_is_detected(qapp, tmp_path):
    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    old = SingleInstance(key, tmp_path, version="a" * 40)
    assert old.acquire(on_activate=lambda: None) is True
    assert old.running_info()["version"] == "a" * 40
    same = SingleInstance(key, tmp_path, version="a" * 40)
    newer = SingleInstance(key, tmp_path, version="b" * 40)
    unknown = SingleInstance(key, tmp_path, version="")
    assert same.acquire(on_activate=lambda: None, notify=False) is False and not same.newer_available()
    assert newer.acquire(on_activate=lambda: None, notify=False) is False and newer.newer_available()
    assert not unknown.newer_available()                                     # sem versao propria: nao sabe, nao pergunta
    old.release()
    assert not (tmp_path / f"{key}.json").exists()                           # quem sai apaga o proprio registro


def test_quit_request_reaches_only_the_quit_handler_and_the_new_version_takes_over(qapp, tmp_path):
    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    events = []
    old = SingleInstance(key, tmp_path, version="a" * 40)

    def old_quits():
        events.append("quit")
        old.release()                                                         # o que o close_app faz ao fechar o app
    assert old.acquire(on_activate=lambda: events.append("activate"), on_quit=old_quits) is True
    new = SingleInstance(key, tmp_path, version="b" * 40)
    assert new.acquire(on_activate=lambda: None, notify=False) is False
    assert new.send("quit") is True
    assert _pump(qapp, lambda: events == ["quit"])
    assert new.acquire(on_activate=lambda: None, notify=False) is True
    assert new.running_info()["version"] == "b" * 40
    new.release()


def test_activate_is_still_the_default_message(qapp, tmp_path):
    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    events = []
    first = SingleInstance(key, tmp_path)
    assert first.acquire(on_activate=lambda: events.append("activate"), on_quit=lambda: events.append("quit")) is True
    second = SingleInstance(key, tmp_path)                                   # vivo ate a entrega (como o processo da 2a abertura)
    assert second.acquire(on_activate=lambda: None) is False
    assert _pump(qapp, lambda: events == ["activate"])                      # a sondagem ("ping") nao conta; o pedido conta uma vez
    first.release()


def test_checkout_version_is_the_commit_or_empty(tmp_path):
    from agent_bar import startup
    v = startup.checkout_version(ROOT)
    assert v == "" or (len(v) == 40 and all(c in "0123456789abcdef" for c in v))
    assert startup.checkout_version(tmp_path) == ""                          # pasta sem git: nao sabe
    assert startup.checkout_version(ROOT, run=lambda *a, **k: (_ for _ in ()).throw(OSError("sem git"))) == ""


OLD_INSTANCE = '''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from agent_bar.single_instance import SingleInstance
app = QApplication([])
inst = SingleInstance(sys.argv[1], Path(sys.argv[2]), version="a" * 40)
assert inst.acquire(on_activate=lambda: None, on_quit=lambda: (Path(sys.argv[2], "quit.marker").write_text("ok"), inst.release(), app.quit()))
Path(sys.argv[2], "ready.marker").write_text("ok")
QTimer.singleShot(30000, app.quit)
app.exec()
'''


def test_a_newer_version_asks_a_real_older_process_to_quit_and_takes_over(qapp, tmp_path):
    import subprocess
    import sys
    import time

    from agent_bar.single_instance import SingleInstance
    key = f"agent-bar-test-{uuid.uuid4().hex[:8]}"
    script = tmp_path / "old_instance.py"
    script.write_text(OLD_INSTANCE, encoding="utf-8")
    old = subprocess.Popen([sys.executable, str(script), key, str(tmp_path)], cwd=ROOT)
    try:
        end = time.time() + 20
        while time.time() < end and not (tmp_path / "ready.marker").exists():
            time.sleep(0.05)
        new = SingleInstance(key, tmp_path, version="b" * 40)
        assert new.acquire(on_activate=lambda: None, notify=False) is False and new.newer_available()
        assert new.send("quit")
        got = _pump(qapp, lambda: new.acquire(on_activate=lambda: None, notify=False), secs=20)
        assert got and (tmp_path / "quit.marker").exists()
        assert old.wait(timeout=20) == 0
        new.release()
    finally:
        if old.poll() is None:
            old.kill()
