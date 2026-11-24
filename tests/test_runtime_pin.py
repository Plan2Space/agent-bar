"""A barra confere no inicio se o agent-runtime instalado e o fixado no pyproject (pip nao reinstala quando a versao e a mesma)."""
from __future__ import annotations

import json
import logging

import pytest

from agent_bar import runtime_pin as rp

PINNED = "0de08b96d4532a81f047c2b65344dd29fb1150c3"
OLD = "92acdcdd8cd8dd6a69152c79f5bd304280733235"


def pyproject(tmp_path, sha=PINNED):
    p = tmp_path / "pyproject.toml"
    p.write_text(f'[project]\nname = "agent-bar"\ndependencies = ["agent-runtime @ git+https://github.com/Plan2Space/agent-runtime@{sha}"]\n',
                 encoding="utf-8")
    return p


def direct_url(sha):
    return lambda: json.dumps({"url": "https://github.com/Plan2Space/agent-runtime", "vcs_info": {"vcs": "git", "commit_id": sha}})


def test_pinned_sha_is_read_from_the_pyproject(tmp_path):
    assert rp.pinned_sha(pyproject(tmp_path)) == PINNED


def test_the_real_pyproject_pins_a_full_sha():
    assert rp.pinned_sha(rp.PYPROJECT) is not None and len(rp.pinned_sha(rp.PYPROJECT)) == 40


def test_matching_install_is_ok(tmp_path):
    st = rp.check(pyproject(tmp_path), read_direct_url=direct_url(PINNED))
    assert st.ok is True and st.installed == PINNED and "confere" in st.message


def test_old_install_is_flagged_with_the_exact_update_command(tmp_path):
    st = rp.check(pyproject(tmp_path), read_direct_url=direct_url(OLD))
    assert st.ok is False and OLD[:12] in st.message and PINNED[:12] in st.message
    assert f'pip install --force-reinstall --no-deps "agent-runtime @ git+https://github.com/Plan2Space/agent-runtime@{PINNED}"' in st.command


def test_unknown_origin_or_missing_package_is_said_not_assumed_ok(tmp_path):
    def missing():
        raise FileNotFoundError("agent-runtime não instalado")
    assert rp.check(pyproject(tmp_path), read_direct_url=missing).ok is None
    assert rp.check(pyproject(tmp_path), read_direct_url=lambda: None).ok is None                    # sem direct_url.json
    assert rp.check(pyproject(tmp_path), read_direct_url=lambda: '{"url": "file:///x", "dir_info": {}}').ok is None
    assert rp.check(tmp_path / "nao-existe.toml", read_direct_url=direct_url(PINNED)).ok is None


def test_mismatch_is_logged_as_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="agent_bar"):
        rp.log_status(rp.check(pyproject(tmp_path), read_direct_url=direct_url(OLD)))
    assert "agent-runtime" in caplog.text and OLD[:12] in caplog.text


# ------------------------------------------------------------------------------------------------ UI
from tests.test_agent_bar import gui, snap  # noqa: E402,F401


def test_panel_shows_a_red_warning_only_when_the_runtime_is_old(gui, tmp_path):  # noqa: F811
    bar, app, tmp = gui
    bar.set_runtime_pin(rp.check(pyproject(tmp_path), read_direct_url=direct_url(OLD)))
    bar.on_snapshot(snap(tmp))
    w = bar.details.pin_warning
    assert w.isVisibleTo(bar.details) and "agent-runtime desatualizado" in w.text() and "force-reinstall" in w.text()
    bar.set_runtime_pin(rp.check(pyproject(tmp_path), read_direct_url=direct_url(PINNED)))
    assert not w.isVisibleTo(bar.details)


# ------------------------------------------------------------- UX-02: instalado x pyproject x uv.lock, na abertura
def uv_lock(tmp_path, sha=PINNED):
    p = tmp_path / "uv.lock"
    p.write_text('version = 1\n\n[[package]]\nname = "agent-runtime"\nversion = "0.1.0"\n'
                 f'source = {{ git = "https://github.com/Plan2Space/agent-runtime?rev={sha}#{sha}" }}\n', encoding="utf-8")
    return p


def test_the_locked_sha_is_read_from_the_uv_lock(tmp_path):
    assert rp.locked_sha(uv_lock(tmp_path)) == PINNED
    assert rp.locked_sha(tmp_path / "nao-existe.lock") is None


def test_the_real_pyproject_and_uv_lock_pin_the_same_runtime():
    assert rp.locked_sha(rp.UV_LOCK) == rp.pinned_sha(rp.PYPROJECT)              # o lock acompanha o pin


def test_an_old_install_names_the_installed_the_pyproject_and_the_uv_lock_shas_and_the_commands(tmp_path):
    pyproject(tmp_path)
    uv_lock(tmp_path)
    st = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(OLD))
    assert st.ok is False and st.locked == PINNED
    for sha in (OLD[:12], PINNED[:12]):
        assert sha in st.message
    assert "uv.lock" in st.message and rp.UV_SYNC in st.message and "force-reinstall" in st.command


def test_pyproject_and_uv_lock_that_disagree_are_flagged_even_with_a_matching_install(tmp_path):
    pyproject(tmp_path)
    uv_lock(tmp_path, sha=OLD)
    st = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(PINNED))
    assert st.ok is False and "pyproject e uv.lock divergem" in st.message
    assert PINNED[:12] in st.message and OLD[:12] in st.message and st.command == rp.UV_SYNC


def test_a_matching_install_says_it_matches_both(tmp_path):
    pyproject(tmp_path)
    uv_lock(tmp_path)
    st = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(PINNED))
    assert st.ok is True and "confere com o pyproject e o uv.lock" in st.message


def test_the_core_turns_a_divergent_runtime_into_an_action_required_notification(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from agent_bar import core as C
    from agent_bar import settings as cfg
    from agent_bar.cockpit.notifications import NotificationCenter
    QApplication.instance() or QApplication([])
    pyproject(tmp_path)
    uv_lock(tmp_path)
    bad = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(OLD))
    good = rp.check(tmp_path / "pyproject.toml", read_direct_url=direct_url(PINNED))
    unknown = rp.check(tmp_path / "pyproject.toml", read_direct_url=lambda: None)   # "não deu para saber" não alarma
    assert unknown.ok is None
    for st, expected in ((bad, 1), (good, 0), (unknown, 0)):
        monkeypatch.setattr(rp, "check", lambda st=st: st)
        monkeypatch.setattr(C.col, "collect_snapshot", lambda **_k: None)
        center = NotificationCenter(tmp_path / f"n{st.ok}.sqlite")
        core = C.AppCore(cfg.Settings(), center=center, quit_fn=lambda: None, tray_available=lambda: False)
        try:
            core.start()
            core.publish_runtime()                                                   # de novo: mesma chave, sem duplicar
            items = center.list(categories=["ERROR"])
            assert len(items) == expected
            if expected:
                assert items[0].state == "ACTION_REQUIRED" and OLD[:12] in items[0].body and PINNED[:12] in items[0].body
                assert items[0].source == C.RUNTIME_SOURCE
        finally:
            core.worker.stop()
            center.close()


@pytest.mark.local_env
def test_the_environment_running_the_app_matches_pyproject_and_uv_lock():
    """Opt-in (`-m local_env`), fora do CI: o agent-runtime do Python que roda isto contra o pyproject E o uv.lock do
    checkout. Rode com o python do .venv que o atalho usa."""
    st = rp.check()
    assert st.ok is True, f"{st.message}\ncomando: {st.command}"


def test_the_cockpit_opened_by_the_core_gets_the_runtime_status(monkeypatch):
    import os
    from types import SimpleNamespace
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import agent_bar.cockpit.ui.window as CW
    from agent_bar import core as C
    from agent_bar import settings as cfg
    QApplication.instance() or QApplication([])
    got = {}

    class Fake:
        _closed = False

        def __init__(self, **kw):
            got.update(kw)
            self.chat = SimpleNamespace(ensure_client=lambda: None)

        def show(self): pass                                                      # noqa: E704

        def raise_(self): pass                                                    # noqa: E704

        def activateWindow(self): pass                                            # noqa: E704,N802
    monkeypatch.setattr(CW, "CockpitWindow", Fake)
    core = C.AppCore(cfg.Settings(), quit_fn=lambda: None, tray_available=lambda: False)
    core.open_cockpit()
    assert got["runtime_pin"] is core.runtime_pin
