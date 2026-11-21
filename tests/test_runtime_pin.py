"""A barra confere no inicio se o agent-runtime instalado e o fixado no pyproject (pip nao reinstala quando a versao e a mesma)."""
from __future__ import annotations

import json
import logging

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
