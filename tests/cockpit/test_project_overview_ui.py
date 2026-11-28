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


def test_new_repo_that_failed_is_unknown_not_ok(view):
    """Review #158 (MUST): "ok · novo" não pode cobrir um repo novo cuja leitura falhou."""
    view.set_overview(_ov(fail_repos=("Plan2Space/repo-novo",)))
    row = next(r for r in range(view.repos.rowCount()) if view.repos.item(r, 0).text() == "Plan2Space/repo-novo")
    assert view.repos.item(row, 1).text() == "desconhecido · novo"


def test_discovery_failure_renders_as_unknown(view):
    from agent_bar.cockpit.github import GhClient
    from tests.cockpit.test_project_status import Recorded

    class ListDown(Recorded):
        def __call__(self, argv, timeout):
            if tuple(argv[1:3]) == ("repo", "list"):
                return 1, "", "HTTP 502: Bad Gateway"
            return super().__call__(argv, timeout)
    view.set_overview(ps.collect_overview(GhClient(exe=[__file__], runner=ListDown()), "Plan2Space", now=NOW))
    txt = _text(view)
    assert "Repositórios monitorados\n8 (descoberta de novos: desconhecido)" in txt
    assert "atenção: github" in txt


def _ov_sources(broken=False):
    from agent_bar.cockpit import project_sources as src
    from tests.cockpit.test_project_sources import EVID, SPECS, FakeGit
    g = FakeGit(lab=False, broken=broken)
    return ps.collect_overview(client(), "Plan2Space", now=NOW, specs=lambda: src.collect_specs(SPECS, now=NOW, git=g),
                               evidence=lambda: src.collect_evidence(EVID, now=NOW, git=g))


def test_evidence_tile_shows_published_runs_and_lab_unknown(view):
    view.set_overview(_ov_sources())
    assert "Evidências publicadas\n7 (lab/: desconhecido)" in _text(view)


def test_spec_catalog_tab_shows_declared_status_verbatim(view):
    ov = _ov_sources()
    view.set_overview(ov)
    assert view.catalog.rowCount() == len(ov.specs.specs)
    col = [view.catalog.horizontalHeaderItem(c).text() for c in range(view.catalog.columnCount())]
    assert "Declarado na SPEC" in col
    row = next(r for r in range(view.catalog.rowCount())
               if view.catalog.item(r, 0).text() == "plataforma/SPEC-EXEMPLO-002_modelo_de_sessoes.md")
    assert view.catalog.item(row, col.index("Declarado na SPEC")).text().startswith("vigente desde")


def test_sources_unavailable_are_unknown_in_the_view(view):
    view.set_overview(_ov_sources(broken=True))
    txt = _text(view)
    assert "Evidências publicadas\ndesconhecido" in txt and view.catalog.rowCount() == 0


def test_health_tile_counts_ok_with_ref_as_ok(view):
    view.set_overview(_ov_sources())
    tile = next(lab.text() for lab in view.findChildren(QtWidgets.QLabel) if lab.text().startswith("Saúde"))
    assert "engineering-specs" not in tile and "evidence" not in tile and "runtime" in tile


def test_text_columns_are_left_aligned_and_numbers_centered():
    from PySide6.QtCore import Qt
    t = po._table(["a", "b", "c"])
    po._fill(t, [["nome", "um título bem mais longo que catorze", "12"]])
    assert t.item(0, 1).textAlignment() & Qt.AlignmentFlag.AlignLeft.value
    assert t.item(0, 2).textAlignment() & Qt.AlignmentFlag.AlignHCenter.value
    t.deleteLater()


# ------------------------------------------------------------------------------------------------ timeline (§7.5)


def _ov_timeline(**kw):
    from agent_bar.cockpit import project_sources as src
    from tests.cockpit.test_project_sources import EVID, FakeGit
    return ps.collect_overview(client(**kw), "Plan2Space", now=NOW, timeline=True,
                               evidence=lambda: src.collect_evidence(EVID, now=NOW, git=FakeGit()))


def _rows(t):
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


def test_timeline_tab_lists_every_event_with_all_period(view):
    ov = _ov_timeline()
    view.set_overview(ov)
    view.tl_period.setCurrentText("tudo")
    assert view.timeline.rowCount() == len(ov.timeline)
    assert any(r[1] == "Evidência publicada" for r in _rows(view.timeline))


def test_timeline_repo_filter(view):
    view.set_overview(_ov_timeline())
    view.tl_period.setCurrentText("tudo")
    view.tl_repo.setCurrentText("Plan2Space/evidence")
    assert {r[2] for r in _rows(view.timeline)} == {"Plan2Space/evidence"}


def test_timeline_kind_filter(view):
    view.set_overview(_ov_timeline())
    view.tl_period.setCurrentText("tudo")
    view.tl_kind.setCurrentText("CI falhou")
    assert {r[1] for r in _rows(view.timeline)} == {"CI falhou"}


def test_timeline_period_is_relative_to_the_read_time(view):
    ov = _ov_timeline()
    view.set_overview(ov)
    view.tl_period.setCurrentText("24h")
    import datetime as dt

    from agent_bar.cockpit import project_timeline as tl
    now = dt.datetime.fromtimestamp(ov.read_at, dt.timezone.utc).isoformat()
    assert view.timeline.rowCount() == len(tl.filter_events(ov.timeline, now=now, period="24h"))


def test_undated_event_says_date_unknown(view):
    ov = _ov_timeline()
    from agent_bar.cockpit import project_timeline as tl
    ov = ps.Overview(ov.github, ov.project, ov.read_at, None, ov.health, None, None,
                     tl.evidence_events(("run-sem-data",)), {})
    view.set_overview(ov)
    view.tl_period.setCurrentText("tudo")
    assert _rows(view.timeline)[0][0] == "data desconhecida"


def test_timeline_reports_repos_it_could_not_read(view):
    view.set_overview(_ov_timeline(fail_repos=("Plan2Space/evidence",)))
    assert "não lido" in view.tl_status.text() and "evidence" in view.tl_status.text()
