"""UX-07 (§2.4, "Confirmar aprovação de PR"): o "Aprovar" sai da Central da barra e vai para a área PRs, no PR
selecionado. Mesma política (approvals.py não muda): só linha aprovável, mesmo texto (`confirmation_text`), mesma
releitura de HEAD e CI antes de postar, mesma linha postada. Diálogo de risco: modal, título "Confirmar", "Não" como
padrão, fechar = recusar. Instância somente leitura não aprova. Contra o `gh` falso, sem rede."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from agent_bar import approvals as ap  # noqa: E402
from agent_bar.cockpit.github import GhClient  # noqa: E402

_APP = QApplication.instance() or QApplication([])
FAKE = Path(__file__).with_name("fake_gh.py")
REPO = "Plan2Space/agent-bar"
SHA = "a" * 40                                                                  # HEAD do PR 7 no gh falso


def board(can=True, risk="MEDIUM", state="aguardando", reason=""):
    row = ap.PrApproval(REPO, 7, url="https://github.com/Plan2Space/agent-bar/pull/7", title="feat: coisa", head_sha=SHA,
                        risk=risk, ci_state="success", state=state, can_approve=can, blocked_reason=reason)
    return ap.ApprovalBoard(rows=[row], login="fmodesto30")


def wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.jsonl"))
    monkeypatch.delenv("AGENT_BAR_OBSERVE_ONLY", raising=False)
    tabs = []

    def build(b, approve_fn=None):
        from agent_bar.cockpit.ui.github_tabs import PrsTab
        kw = {"approve_fn": approve_fn} if approve_fn else {}
        tab = PrsTab(GhClient(exe=[sys.executable, str(FAKE)]), repos=[REPO], approvals_fn=lambda: b, **kw)
        tabs.append(tab)
        tab.refresh()
        assert wait(lambda: tab.tree.topLevelItemCount() == 2)
        tab.tree.setCurrentItem(tab.tree.topLevelItem(0))                     # PR 7
        assert wait(lambda: "#7" in tab.detail.toPlainText())
        return tab
    yield build
    from PySide6.QtCore import QCoreApplication, QEvent
    for t in tabs:
        t.shutdown()
        t.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def capture_confirm(monkeypatch, answer):
    seen = {}

    def exec_(box):
        seen.update(title=box.windowTitle(), text=box.text(), modal=box.isModal(),
                    default=box.standardButton(box.defaultButton()) if box.defaultButton() else None)
        return answer
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return seen


def test_the_selected_pr_shows_its_approval_state_and_the_button_on_its_exact_head(make):
    tab = make(board())
    assert "aguardando" in tab.approval_label.text() and "MEDIUM" in tab.approval_label.text()
    assert SHA[:7] in tab.approve_btn.text() and tab.approve_btn.isEnabled()


def test_a_pr_that_cannot_be_approved_here_says_why_and_the_button_is_off(make):
    tab = make(board(can=False, risk="HIGH", state="aguardando", reason="HIGH: decisão do Felipe"))
    assert not tab.approve_btn.isEnabled() and "HIGH: decisão do Felipe" in tab.approval_label.text()


def test_confirm_dialog_has_the_same_title_and_text_and_no_as_default_and_no_posts_nothing(make, monkeypatch):
    calls = []
    tab = make(board(), approve_fn=lambda gh, row, confirm: calls.append(row) or (True, "postado"))
    seen = capture_confirm(monkeypatch, QMessageBox.StandardButton.No)
    tab.approve_btn.click()
    assert seen["title"] == "Confirmar" and seen["text"] == ap.confirmation_text(board().rows[0])
    assert seen["modal"] and seen["default"] == QMessageBox.StandardButton.No           # risco: "não" é o padrão
    assert calls == [] and "cancelada" in tab.status.text()                             # fechar/Não = nada postado


def test_yes_runs_the_same_approve_with_the_head_and_ci_reread_off_the_ui_thread(make, monkeypatch):
    calls = []

    def approve(gh, row, confirm):
        calls.append((row.pr_ref, row.head_sha, confirm("x")))                         # já confirmado na UI
        return True, "aprovação postada: https://github.com/x#c1"
    tab = make(board(), approve_fn=approve)
    capture_confirm(monkeypatch, QMessageBox.StandardButton.Yes)
    tab.approve_btn.click()
    assert wait(lambda: "aprovação postada" in tab.status.text())
    assert calls == [(f"{REPO}#7", SHA, True)]


def test_a_refusal_from_the_head_or_ci_reread_is_shown_not_hidden(make, monkeypatch):
    tab = make(board(), approve_fn=lambda gh, row, confirm: (False, "recusado: o HEAD mudou de aaaa para bbbb"))
    capture_confirm(monkeypatch, QMessageBox.StandardButton.Yes)
    tab.approve_btn.click()
    assert wait(lambda: "recusado: o HEAD mudou" in tab.status.text())


def test_an_observe_only_instance_never_approves(make, monkeypatch):
    monkeypatch.setenv("AGENT_BAR_OBSERVE_ONLY", "1")
    calls = []
    tab = make(board(), approve_fn=lambda gh, row, confirm: calls.append(1) or (True, ""))
    assert not tab.approve_btn.isEnabled() and "somente leitura" in tab.approval_label.text()
    seen = capture_confirm(monkeypatch, QMessageBox.StandardButton.Yes)                 # nem o diálogo aparece
    tab.approval.approve()                                                              # nem chamando direto
    time.sleep(0.2)
    _APP.processEvents()
    assert calls == [] and seen == {} and "somente leitura" in tab.status.text()


def test_without_the_collection_the_area_says_so(make):
    tab = make(None)
    assert not tab.approve_btn.isEnabled() and "coleta ainda não chegou" in tab.approval_label.text()


def test_the_real_approve_still_rereads_head_and_ci_before_posting():
    """A política continua em approvals.approve: HEAD que mudou → recusa sem postar (o gh de escrita nunca é chamado)."""
    calls = []

    def gh(args):
        calls.append(args)
        if args[:2] == ["pr", "view"] or "graphql" in args:
            return 0, '{"headRefOid": "' + "b" * 40 + '"}'
        return 0, ""
    ok, msg = ap.approve(gh, board().rows[0], confirm=lambda _t: True)
    assert not ok and ("HEAD" in msg or "conferir" in msg)
    assert not any(a[:2] == ["pr", "comment"] for a in calls)


def test_the_window_feeds_the_prs_area_from_the_core_snapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import agent_bar.cockpit.ui.window as CW
    from agent_bar.cockpit import window_state as WS
    from agent_bar.cockpit.profiles import AgentProfile
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(CW, "screen_rects", lambda: [WS.Rect(0, 0, 2560, 1400)])
    (tmp_path / "ws").mkdir()
    b = board()
    core = SimpleNamespace(snap=None, settings=SimpleNamespace(bar_enabled=False),
                           runtime_pin=None, worker=SimpleNamespace(refresh=lambda force=False: None))
    w = CW.CockpitWindow(repos=[REPO], workspaces_fn=lambda: [tmp_path / "ws"], core=core,
                         profiles_fn=lambda: [AgentProfile("s", "S", (sys.executable, "x"))],
                         window_store=WS.WindowStateStore(tmp_path / "w.json"))
    try:
        assert w.prs.approvals_fn() is None                                     # coleta ainda não chegou
        core.snap = SimpleNamespace(approvals=b)
        assert w.prs.approvals_fn() is b
    finally:
        from PySide6.QtCore import QCoreApplication, QEvent
        w.shutdown()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
