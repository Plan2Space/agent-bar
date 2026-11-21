"""Cor da linha de motivo no card de aprovacao: aprovado = verde (cor de aprovado), bloqueado = vermelho. GitHub falso."""
from __future__ import annotations

from tests.test_agent_bar import gui, snap  # noqa: F401
from tests.test_approval_ui import REPO, SHA1, _open_full, collect, comment, pr_state

APPROVED = "#3ddc84"
BLOCKED = "#ff9a9b"


def test_approved_by_line_uses_the_approved_color(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(comments=[comment(f"APROVADO MEDIUM {SHA1}")])})
    text = _open_full(bar, s).cards["approval"].text()
    assert f"<span style='color:{APPROVED}'>aprovado por fmodesto30" in text
    assert f"<span style='color:{BLOCKED}'>aprovado por" not in text


def test_blocked_reason_keeps_the_blocked_color(gui):  # noqa: F811
    bar, app, tmp = gui
    s = snap(tmp)
    s.approvals = collect({(REPO, 7): pr_state(rollup="FAILURE")})
    text = _open_full(bar, s).cards["approval"].text()
    assert f"<span style='color:{BLOCKED}'>CI failure" in text
