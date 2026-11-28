"""project_overview.py — visão geral do projeto (§7.1), progresso por frente (§7.2) e por SPEC (§7.3). P0.

Mostra só o que `project_status` leu das fontes. Sem fonte: "desconhecido". Sem percentual: contagens. A coleta roda
fora da thread de UI (ADR 0003) e a falha aparece na linha de estado, nunca some.

Ponto de entrada para o cabeçalho / seletor de workspace (a Frente B liga em window.py): ``open_project_overview``.
Sem cor própria: herda o tema; os tokens do Design System (DS-01) entram quando o tema estiver em main.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import project_status as ps

Collect = Callable[[], ps.Overview]
UNKNOWN = ps.UNKNOWN
SPEC_COLUMNS = (ps.PLANEJADO, ps.EM_IMPLEMENTACAO, ps.IMPLEMENTADO, ps.EM_REVISAO, ps.INTEGRADO, ps.COMPROVADO,
                ps.BLOQUEADO, ps.UNKNOWN)


def _n(value: int | None) -> str:
    return UNKNOWN if value is None else str(value)


def _hhmm(t: float) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(t))


def default_collect() -> ps.Overview:
    from agent_bar.cockpit.github import GhClient
    return ps.collect_overview(GhClient(), "Plan2Space", now=time.time())


class _Bridge(QObject):
    done = Signal(int, object, object)


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fill(t: QTableWidget, rows: list[list[str]]) -> None:
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem(val)
            if c > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setItem(r, c, item)


class ProjectOverview(QWidget):
    """A Central de acompanhamento. ``collect=None`` só exibe o que ``set_overview`` receber (testes)."""

    def __init__(self, collect: Collect | None = default_collect, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.collect = collect
        self.overview: ps.Overview | None = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cockpit-overview")
        self.req = 0
        self.bridge = _Bridge()
        self.bridge.done.connect(self._done)

        self.title = QLabel("Acompanhamento do projeto")
        self.title.setStyleSheet("font-size:16px;font-weight:600")
        self.status = QLabel("ainda não lido")
        self.status.setWordWrap(True)
        self.refresh_btn = QPushButton("Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh_btn.setEnabled(collect is not None)

        self.tiles = QGridLayout()
        self._tiles: dict[str, QLabel] = {}
        names = ("Repositórios monitorados", "Agentes ativos", "Tarefas em andamento", "Tarefas concluídas",
                 "Tarefas bloqueadas", "PRs abertos", "PRs aguardando revisão", "PRs com CI falhando",
                 "PRs mergeados (recentes)", "SPECs com tarefa em andamento", "Evidências publicadas", "Saúde")
        for i, name in enumerate(names):
            lab = QLabel(f"{name}\n{UNKNOWN}")
            lab.setFrameShape(QLabel.Shape.StyledPanel)
            lab.setMargin(8)
            lab.setWordWrap(True)
            self._tiles[name] = lab
            self.tiles.addWidget(lab, i // 4, i % 4)

        self.repos = _table(["Repositório", "Estado", "PRs abertos", "Aguardando revisão", "CI falhando",
                             "Mergeados (recentes)", "Última atividade"])
        self.fronts = _table(["Frente", "Tarefas", "Em andamento", "Concluídas", "Bloqueadas", "Planejadas",
                              "SPECs vinculadas"])
        self.specs = _table(["SPEC", "Tarefas", *SPEC_COLUMNS])
        tabs = QTabWidget()
        tabs.addTab(self.repos, "Repositórios")
        tabs.addTab(self.fronts, "Frentes")
        tabs.addTab(self.specs, "SPECs")

        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(self.status)
        lay.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(self.tiles)
        lay.addWidget(tabs, 1)

    # ------------------------------------------------------------------------------------------------- coleta
    def refresh(self) -> None:
        if self.collect is None:
            return
        self.req += 1
        rid = self.req
        self.status.setText("lendo GitHub e Project #1…")
        fut = self.pool.submit(self.collect)
        fut.add_done_callback(lambda f, r=rid: self.bridge.done.emit(r, None if f.exception() else f.result(),
                                                                     f.exception()))

    def _done(self, rid: int, result: object, err: object) -> None:
        if rid != self.req:
            return
        if err is not None:
            prev = f" · mostrando a leitura de {_hhmm(self.overview.read_at)}" if self.overview else ""
            self.status.setText(f"a leitura falhou: {err}{prev}")
            return
        self.set_overview(result)  # type: ignore[arg-type]

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)

    # ------------------------------------------------------------------------------------------------ exibição
    def _tile(self, name: str, value: str) -> None:
        self._tiles[name].setText(f"{name}\n{value}")

    def set_overview(self, ov: ps.Overview) -> None:
        self.overview = ov
        gh, proj = ov.github, ov.project
        totals = gh.totals()
        partial = " (parcial)" if gh.totals_partial else ""
        self.status.setText(f"Última atualização: {_hhmm(ov.read_at)} · "
                            + " · ".join(f"{k}: {v}" for k, v in ov.health))
        new = gh.new_repos or []
        if gh.new_repos is None:
            found = " (descoberta de novos: desconhecido)"
        else:
            found = f" ({len(new)} novo)" if new else ""
        self._tile("Repositórios monitorados", f"{len(gh.repos)}{found}")
        self._tile("Agentes ativos", _n(ov.active_agents))
        c = proj.counts if proj.state == "ok" else {}
        self._tile("Tarefas em andamento", _n(c.get(ps.T_IN_PROGRESS)))
        self._tile("Tarefas concluídas", _n(c.get(ps.T_DONE)))
        self._tile("Tarefas bloqueadas", _n(c.get(ps.T_BLOCKED)))
        self._tile("PRs abertos", _n(totals["open_prs"]) + partial)
        self._tile("PRs aguardando revisão", _n(totals["awaiting_review"]) + partial)
        self._tile("PRs com CI falhando", _n(totals["ci_failing"]) + partial)
        self._tile("PRs mergeados (recentes)", _n(totals["merged_recent"]) + partial)
        in_impl = None if proj.state != "ok" else sum(
            1 for s in proj.specs if s.states.get(ps.EM_IMPLEMENTACAO) or s.states.get(ps.IMPLEMENTADO))
        self._tile("SPECs com tarefa em andamento", _n(in_impl))
        self._tile("Evidências publicadas", UNKNOWN)            # coletor do evidence lab/: próximo PR
        bad = [k for k, v in ov.health if v != "ok"]
        self._tile("Saúde", "ok" if not bad else "atenção: " + ", ".join(bad))

        _fill(self.repos, [[r.repo,
                            ("ok" if r.state == "ok" else UNKNOWN) + (" · novo" if r.repo in new else ""),
                            _n(r.open_prs), _n(r.awaiting_review), _n(r.ci_failing), _n(r.merged_recent),
                            r.last_activity or UNKNOWN] for r in gh.repos])
        for row, r in enumerate(gh.repos):
            if r.error:
                self.repos.item(row, 1).setToolTip(r.error)
        _fill(self.fronts, [[f.front, str(f.total), str(f.counts.get(ps.T_IN_PROGRESS, 0)),
                             str(f.counts.get(ps.T_DONE, 0)), str(f.counts.get(ps.T_BLOCKED, 0)),
                             str(f.counts.get(ps.T_PLANNED, 0)), ", ".join(f.specs) or "—"] for f in proj.fronts])
        _fill(self.specs, [[s.spec, str(s.total), *(str(s.states.get(k, 0)) for k in SPEC_COLUMNS)]
                           for s in proj.specs])


def open_project_overview(parent: QWidget | None = None, collect: Collect | None = default_collect,
                          show: bool = True) -> QDialog:
    """Abre a visão geral numa janela não-modal (sem criar uma 5ª área) e dispara a primeira leitura."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Acompanhamento do projeto — Plan2Space")
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    view = ProjectOverview(collect, dlg)
    dlg.finished.connect(lambda _=0: view.shutdown())
    QVBoxLayout(dlg).addWidget(view)
    dlg.resize(1100, 760)
    if show:
        dlg.show()
        view.refresh()
    return dlg
