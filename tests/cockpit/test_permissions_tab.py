"""Aba Permissões (F4): pendentes destacados no topo, historico filtravel, detalhes com explicacao de repeticao e
regras persistentes desativaveis."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.permission_inbox import PermissionInbox  # noqa: E402
from agent_bar.cockpit.ui.permissions_tab import PermissionsTab  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def tc(title, kind="edit", **raw):
    return {"toolCallId": "t", "title": title, "kind": kind, "rawInput": raw or {"file_path": "C:/ws/a.py"}}


@pytest.fixture
def tab(tmp_path):
    ib = PermissionInbox(tmp_path / "p.sqlite")
    t = PermissionsTab(ib, refresh_ms=60_000)
    yield t
    t.shutdown()
    t.deleteLater()
    ib.close()


def rows(t):
    return [[t.tree.topLevelItem(i).text(c) for c in (2, 6)] for i in range(t.tree.topLevelItemCount())]


def test_pending_come_first_and_the_summary_counts_them(tab):
    ib = tab.inbox
    done = ib.record(tc("Write a.py"), provider="claude-acp", session_id="s1")
    ib.decide(done.id, "allow_once")
    ib.record(tc("Run rm", kind="execute", command="rm -rf build"), provider="claude-acp", session_id="s1")
    tab.refresh()
    assert rows(tab) == [["Run rm", "PENDING"], ["Write a.py", "ALLOWED"]]
    assert tab.summary.text().startswith("1 pendente")


def test_filters_by_status_and_text(tab):
    ib = tab.inbox
    a = ib.record(tc("Write a.py"), provider="p", session_id="s1")
    ib.decide(a.id, None)
    ib.record(tc("Read b.py", kind="read"), provider="p", session_id="s1")
    tab.status_box.setCurrentIndex(tab.status_box.findData("DENIED"))
    assert rows(tab) == [["Write a.py", "DENIED"]]
    tab.status_box.setCurrentIndex(0)
    tab.search.setText("b.py")
    assert rows(tab) == [["Read b.py", "PENDING"]]


def test_details_explain_a_repeated_request(tab):
    ib = tab.inbox
    a = ib.record(tc("Write a.py"), provider="p", session_id="s1")
    ib.decide(a.id, "allow_once")
    ib.record(tc("Write a.py"), provider="p", session_id="s1")
    tab.refresh()
    tab.tree.setCurrentItem(tab.tree.topLevelItem(0))
    html = tab.details.toPlainText()
    assert "Pedido repetido" in html and "permitir uma vez" in html and "C:/ws/a.py" in html


def test_a_persistent_rule_can_be_disabled_from_the_tab(tab):
    tab.inbox.add_rule("edit", "Write", "C:/ws")
    tab.refresh()
    assert tab.rules_box.count() == 1 and tab.disable_btn.isEnabled()
    tab.disable_btn.click()
    assert tab.inbox.rules() == [] and not tab.disable_btn.isEnabled()
