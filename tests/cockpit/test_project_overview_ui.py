"""Visão geral do projeto (§7.1/§7.2/§7.3, P0): renderiza o que os coletores leram, offscreen, sem rede.

Regras travadas: fonte sem resposta aparece "desconhecido" (nunca 0); totais parciais dizem "parcial"; agentes ativos
sem fonte do Runtime = "desconhecido"; sem percentual na tela; sem cor solta (#rrggbb) no módulo novo (ratchet DS-01).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from agent_bar.cockpit import project_status as ps
from tests.cockpit.test_project_status import NOW, client

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from agent_bar.cockpit.ui import project_overview as po  # noqa: E402

SHOTS = Path(os.environ.get("AGENT_BAR_SHOTS", "")) if os.environ.get("AGENT_BAR_SHOTS") else None


def _ov(**kw) -> ps.Overview:
    return ps.collect_overview(client(**kw), "Plan2Space", now=NOW)


def _text(w) -> str:
    parts = []
    for lab in w.findChildren(QtWidgets.QLabel):
        parts.append(lab.text())
    for t in w.findChildren(QtWidgets.QTableWidget):
        for r in range(t.rowCount()):
            for c in range(t.columnCount()):
                it = t.item(r, c)
                parts.append(it.text() if it else "")
    return "\n".join(parts)


@pytest.fixture
def view():
    w = po.ProjectOverview(collect=None)
    w.resize(1100, 760)
    yield w
    w.shutdown()
    w.deleteLater()


def test_overview_shows_counts_read_from_the_sources(view):
    ov = _ov()
    view.set_overview(ov)
    txt = _text(view)
    totals = ov.github.totals()
    assert f"PRs abertos\n{totals['open_prs']}" in txt
    assert f"Tarefas concluídas\n{ov.project.counts[ps.T_DONE]}" in txt
    assert "Agentes ativos\ndesconhecido" in txt
    assert "Plan2Space/repo-novo" in txt and "novo" in txt


def test_unknown_repo_is_shown_as_unknown_and_totals_as_partial(view):
    view.set_overview(_ov(fail_repos=("Plan2Space/evidence",)))
    txt = _text(view)
    row = next(r for r in range(view.repos.rowCount()) if view.repos.item(r, 0).text() == "Plan2Space/evidence")
    assert view.repos.item(row, 1).text() == "desconhecido"
    assert "parcial" in txt


def test_no_percent_and_fronts_unknown_until_the_board_field_exists(view):
    view.set_overview(_ov())
    txt = _text(view)
    assert "%" not in txt
    assert view.fronts.item(0, 0).text() == ps.FRONT_UNKNOWN


def test_specs_table_lists_delivery_states_by_name(view):
    ov = _ov()
    view.set_overview(ov)
    headers = [view.specs.horizontalHeaderItem(c).text() for c in range(view.specs.columnCount())]
    for s in (ps.PLANEJADO, ps.EM_IMPLEMENTACAO, ps.EM_REVISAO, ps.INTEGRADO, ps.COMPROVADO, ps.BLOQUEADO):
        assert s in headers
    assert view.specs.rowCount() == len(ov.project.specs)


def test_refresh_collects_off_the_ui_thread_and_renders(view):
    calls = []

    def collect():
        calls.append(1)
        return _ov()
    w = po.ProjectOverview(collect=collect)
    try:
        w.refresh()
        end = time.monotonic() + 10
        while w.overview is None and time.monotonic() < end:
            _APP.processEvents()
            time.sleep(0.02)
        assert calls == [1] and w.overview is not None
    finally:
        w.shutdown()
        w.deleteLater()


def test_collect_failure_is_shown_not_swallowed(view):
    def boom():
        raise RuntimeError("sem rede")
    w = po.ProjectOverview(collect=boom)
    try:
        w.refresh()
        end = time.monotonic() + 10
        while "sem rede" not in w.status.text() and time.monotonic() < end:
            _APP.processEvents()
            time.sleep(0.02)
        assert "sem rede" in w.status.text()
    finally:
        w.shutdown()
        w.deleteLater()


def test_module_has_no_loose_hex_colours():
    src = Path(po.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", src)


def test_grab_renders_offscreen(view):
    view.set_overview(_ov(fail_repos=("Plan2Space/evidence",)))
    img = view.grab()
    assert not img.isNull() and img.width() > 600
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        img.save(str(SHOTS / "project_overview.png"))


def test_open_dialog_for_the_header_entry_point():
    dlg = po.open_project_overview(None, collect=lambda: _ov(), show=False)
    try:
        assert isinstance(dlg, QtWidgets.QDialog) and dlg.findChild(po.ProjectOverview) is not None
    finally:
        dlg.findChild(po.ProjectOverview).shutdown()
        dlg.deleteLater()
