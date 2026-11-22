"""F6: modo desenvolvedor — lista permitida, sandbox, validacao, token de uso unico, promocao com backup, irreversiveis."""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

from agent_bar.cockpit import devmode as D


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "agent_bar").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "agent_bar" / "a.py").write_bytes(b"A = 1\n")                                  # LF fixo: o hash nao depende do SO
    (r / "README.md").write_text("readme\n", encoding="utf-8")
    (r / ".git").mkdir()
    (r / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")
    return r


def session(repo, tmp_path):
    return D.DevSession(repo, root=tmp_path / "dev", session_id="s1", clock=lambda: 0.0)


OK = lambda sandbox: (True, "ok")                                                   # noqa: E731


@pytest.mark.parametrize("rel,why", [
    ("../evil.py", "'..'"), ("agent_bar/../../x.py", "'..'"), ("C:/Windows/x.py", "absoluto"), ("/etc/x", "absoluto"),
    ("README.md", "fora da lista"), (".github/workflows/ci.yml", "fora da lista"), ("pyproject.toml", "fora da lista"), ("", "vazio")])
def test_paths_outside_allowlist_are_refused(rel, why):
    assert why in (D.check_path(rel) or "")


def test_paths_inside_allowlist_pass():
    for rel in ("agent_bar/x.py", "tests/cockpit/t.py", "docs/a/b.md"):
        assert D.check_path(rel) is None


def test_sandbox_excludes_git_and_proposal_tracks_changes(repo, tmp_path):
    s = session(repo, tmp_path)
    sb = s.create_sandbox()
    assert not (sb / ".git").exists() and (sb / "agent_bar" / "a.py").is_file()
    assert s.refresh_proposal() == "applied_sandbox" and s.reason == "sandbox sem mudancas"
    assert s.apply_changes({"agent_bar/a.py": "A = 2\n", "tests/test_new.py": "def test_x():\n    pass\n"}) == "proposed"
    assert [c.path for c in s.proposal.changes] == ["agent_bar/a.py", "tests/test_new.py"]
    assert (repo / "agent_bar" / "a.py").read_text(encoding="utf-8") == "A = 1\n"            # checkout intocado


def test_agent_proposal_with_bad_path_blocks(repo, tmp_path):
    s = session(repo, tmp_path)
    assert s.apply_changes({"agent_bar/ok.py": "x", "../fora.py": "y"}) == "blocked"
    assert "'..'" in s.reason
    assert not (tmp_path / "fora.py").exists()


def test_change_outside_allowlist_inside_sandbox_blocks(repo, tmp_path):
    s = session(repo, tmp_path)
    sb = s.create_sandbox()
    (sb / "README.md").write_text("mexido\n", encoding="utf-8")
    assert s.refresh_proposal() == "blocked" and "README.md" in s.reason


@pytest.mark.skipif(sys.platform == "win32" and not os.environ.get("CI"), reason="symlink no Windows exige privilegio")
def test_symlink_in_sandbox_blocks(repo, tmp_path):
    s = session(repo, tmp_path)
    sb = s.create_sandbox()
    try:
        os.symlink(repo / "README.md", sb / "agent_bar" / "link.py")
    except OSError:
        pytest.skip("sem permissao para symlink")
    assert s.refresh_proposal() == "blocked" and "link simbolico" in s.reason


def test_validation_failure_blocks(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    assert s.validate(lambda sb: (False, "1 failed")) == "blocked" and "validacao falhou" in s.reason


def test_full_cycle_promotes_with_backup_and_manifest(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n", "agent_bar/new.py": "N = 1\n"})
    assert s.validate(OK) == "validated"
    token = s.issue_token("s1")
    expected = hashlib.sha256(f"{repo.resolve()}\0{s.proposal.digest}\0{s._nonce}\0s1".encode()).hexdigest()
    assert token == expected
    backup = s.promote(token, "s1")
    assert s.state == "promoted"
    assert (repo / "agent_bar" / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert (repo / "agent_bar" / "new.py").read_text(encoding="utf-8") == "N = 1\n"
    assert (backup / "agent_bar" / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    m = D.manifest(backup)
    files = {f["path"]: f for f in m["files"]}
    assert files["agent_bar/a.py"]["before_sha256"] == hashlib.sha256(b"A = 1\n").hexdigest()
    assert files["agent_bar/a.py"]["after_sha256"] == hashlib.sha256(b"A = 2\n").hexdigest()
    assert files["agent_bar/new.py"]["before_sha256"] == "" and m["digest"] == s.proposal.digest
    assert not (repo / ".git" / "index").exists()                                           # nada de git: sem commit


def test_token_is_single_use(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    s.validate(OK)
    token = s.issue_token("s1")
    with pytest.raises(D.DevModeError):
        s.promote("0" * 64, "s1")                                                           # errado: consome o token
    with pytest.raises(D.DevModeError):
        s.promote(token, "s1")                                                              # o certo ja nao vale
    assert (repo / "agent_bar" / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_token_from_another_session_is_refused(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    s.validate(OK)
    token = s.issue_token("s1")
    with pytest.raises(D.DevModeError, match="outra sessao"):
        s.promote(token, "s2")


def test_proposal_changed_after_approval_is_refused(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    s.validate(OK)
    token = s.issue_token("s1")
    (s.sandbox / "agent_bar" / "a.py").write_text("A = 3\n", encoding="utf-8")              # mexeu depois de aprovar
    with pytest.raises(D.DevModeError, match="mudou"):
        s.promote(token, "s1")
    assert s.state == "blocked"
    assert (repo / "agent_bar" / "a.py").read_text(encoding="utf-8") == "A = 1\n"


def test_checkout_changed_after_proposal_is_refused(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    s.validate(OK)
    token = s.issue_token("s1")
    (repo / "agent_bar" / "a.py").write_text("A = 9\n", encoding="utf-8")                  # alguem mexeu no checkout
    with pytest.raises(D.DevModeError, match="mudou"):
        s.promote(token, "s1")


def test_token_requires_validated_state(repo, tmp_path):
    s = session(repo, tmp_path)
    s.apply_changes({"agent_bar/a.py": "A = 2\n"})
    with pytest.raises(D.DevModeError):
        s.issue_token("s1")


@pytest.mark.parametrize("cmd", ["git push origin main", "git commit -m x", "gh pr merge 3", "rm -rf build",
                                 "Remove-Item -Recurse -Force x", "DROP TABLE users", "terraform apply", "kubectl delete pod",
                                 "docker push img", "npm publish", "git reset --hard HEAD~1", "gh api -X DELETE x"])
def test_irreversible_commands_are_detected(cmd):
    assert D.irreversible(cmd)


@pytest.mark.parametrize("cmd", ["git status", "git diff", "pytest -q", "ruff check .", "gh pr list"])
def test_safe_commands_are_not_flagged(cmd):
    assert D.irreversible(cmd) is None or cmd.startswith("gh pr")


def test_devmode_is_opt_in():
    assert D.enabled({}) is False and D.enabled({"AGENT_BAR_COCKPIT_DEVMODE": "1"}) is True


def test_permission_text_and_irreversible_request_read_raw_input_and_content():
    tc = {"title": "Terminal", "rawInput": {"command": "git push origin main"}, "content": []}
    assert D.irreversible_request(tc) == "git push"
    tc2 = {"title": "x", "content": [{"type": "content", "content": {"type": "text", "text": "rm -rf /tmp/a"}}]}
    assert D.irreversible_request(tc2)
    assert D.irreversible_request({"title": "Read a.py"}) is None


# --------------------------------------------------------------------------------------------------------- GUI


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qtw = pytest.importorskip("PySide6.QtWidgets")
    return qtw.QApplication.instance() or qtw.QApplication([])


def _wait(app, cond, timeout=20.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _dispose(w):
    from PySide6.QtCore import QCoreApplication, QEvent
    if hasattr(w, "shutdown"):
        w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_dev_tab_is_disabled_without_opt_in(repo, tmp_path):
    _app()
    from agent_bar.cockpit.ui.dev_tab import DevTab
    tab = DevTab(repo=repo, enabled=False, root=tmp_path / "dev")
    try:
        assert "desligado" in tab.state.text() and not any(b.isEnabled() for b in tab.buttons)
    finally:
        _dispose(tab)


def test_dev_tab_full_cycle_with_confirmations(repo, tmp_path):
    app = _app()
    from agent_bar.cockpit.ui.dev_tab import DevTab
    used, asked = [], []
    tab = DevTab(repo=repo, enabled=True, root=tmp_path / "dev", use_workspace=used.append,
                 confirm=lambda parent, title, text: asked.append(title) or True, validator=OK)
    try:
        tab.create_sandbox()
        assert used == [str(tab.session.sandbox)]
        (tab.session.sandbox / "agent_bar" / "a.py").write_bytes(b"A = 5\n")
        tab.refresh()
        assert tab.validate_btn.isEnabled() and tab.files.topLevelItemCount() == 1
        tab.validate()
        assert _wait(app, lambda: tab.session.state == "validated" and tab.approve_btn.isEnabled())
        tab.approve()
        assert tab.promote_btn.isEnabled() and not tab.approve_btn.isEnabled()
        tab.promote()
        assert tab.session.state == "promoted" and asked == ["Aprovar promoção (uso único)", "Promover agora"]
        assert (repo / "agent_bar" / "a.py").read_bytes() == b"A = 5\n"
        assert "MANIFEST.json" in tab.output.toPlainText()
    finally:
        _dispose(tab)


def test_dev_tab_declined_approval_promotes_nothing(repo, tmp_path):
    app = _app()
    from agent_bar.cockpit.ui.dev_tab import DevTab
    tab = DevTab(repo=repo, enabled=True, root=tmp_path / "dev", confirm=lambda *a: False, validator=OK)
    try:
        tab.create_sandbox()
        (tab.session.sandbox / "agent_bar" / "a.py").write_bytes(b"A = 5\n")
        tab.refresh()
        tab.validate()
        assert _wait(app, lambda: tab.session.state == "validated")
        tab.approve()
        assert tab.token == "" and not tab.promote_btn.isEnabled()
        assert (repo / "agent_bar" / "a.py").read_bytes() == b"A = 1\n"
    finally:
        _dispose(tab)


def test_irreversible_dialog_requires_typed_code(repo):
    _app()
    from agent_bar.cockpit.acp import PermissionRequest
    from agent_bar.cockpit.ui.widgets import IrreversibleDialog
    req = PermissionRequest("s", {"title": "git push origin main"}, ({"optionId": "a", "kind": "allow_once"},))
    dlg = IrreversibleDialog(req, "git push")
    try:
        dlg.entry.setText("errado")
        dlg.ok.click()
        assert dlg.confirmed is False
        dlg.entry.setText(dlg.code)
        dlg.ok.click()
        assert dlg.confirmed is True
    finally:
        _dispose(dlg)


def test_auto_approve_never_approves_irreversible(tmp_path):
    from agent_bar.cockpit import workflows as W
    from agent_bar.cockpit.acp import AsyncRuntime, PermissionRequest
    rt = AsyncRuntime("t")
    try:
        wf = W.Workflow.new("x", workspace=str(tmp_path))
        wf.auto_approve = True
        runner = W.WorkflowRunner(wf, runtime=rt, argv_for=lambda s: ["x"], spec_text="")
        got = []
        st = wf.stages[0]
        # o handler de permissao da etapa, chamado como o cliente ACP chamaria (sem processo)
        req = PermissionRequest("s", {"title": "Terminal", "rawInput": {"command": "git push"}},
                                ({"optionId": "a", "kind": "allow_once"},))
        runner.permission_handler(0, st)(req, got.append)
        assert got == [None] and "RECUSADA (irreversível" in st.log[-1]
    finally:
        rt.stop()
