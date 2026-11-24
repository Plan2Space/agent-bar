"""UX-01 (P2S-STUDIO-UX-CONSOLIDATION-F0 §9): o núcleo do app sem janela.

Coleta, bandeja, aviso "PR precisa de você", runtime e encerramento saem da barra flutuante. Com a barra desligada
(nenhuma `Bar` criada), um PR que passa a precisar do Felipe gera o aviso da bandeja E o item em Notificações; encerrar
sem barra para os agentes do Cockpit e a coleta.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from agent_bar import core as C  # noqa: E402
from agent_bar import settings as cfg  # noqa: E402
from agent_bar.cockpit.notifications import NotificationCenter  # noqa: E402
from tests.test_human_inbox import _inbox, _item  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class FakeCockpit:
    def __init__(self) -> None:
        self._closed, self.shown, self.shut = False, 0, 0
        self.chat = SimpleNamespace(ensure_client=lambda: None)

    def show(self) -> None:
        self.shown += 1

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:                                           # noqa: N802
        pass

    def shutdown(self) -> None:
        self.shut += 1
        self._closed = True


@pytest.fixture
def core(tmp_path, monkeypatch):
    inboxes = {"next": _inbox()}
    monkeypatch.setattr(C, "human_inbox", lambda _board: inboxes["next"])      # o board vira o Inbox que o teste dá
    collected = {"n": 0}

    def collect(**_kw):
        collected["n"] += 1
        return SimpleNamespace(approvals=None)
    monkeypatch.setattr(C.col, "collect_snapshot", collect)                     # coleta sem rede nem processos
    center = NotificationCenter(tmp_path / "permissions.sqlite")
    quits: list[int] = []
    before = set(QApplication.topLevelWidgets())                              # outras janelas de outros testes
    c = C.AppCore(cfg.Settings(local_interval_s=2), center=center, quit_fn=lambda: quits.append(1),
                  tray_available=lambda: True, cockpit_factory=FakeCockpit)
    shown: list[tuple] = []
    c.tray.showMessage = lambda *a: shown.append(a)                             # o aviso do Windows, registrado
    c.windows_before = before
    yield c, inboxes, shown, center, quits, collected
    c.worker.stop()
    if not center.closed:
        center.close()


def test_with_no_bar_a_pr_that_starts_needing_felipe_gives_the_tray_notice_and_a_notifications_item(core):
    c, inboxes, shown, center, _q, _n = core
    created = set(QApplication.topLevelWidgets()) - c.windows_before
    assert not [w for w in created if w.isVisible()]                            # o núcleo não abriu janela nenhuma
    inboxes["next"] = _inbox(_item(7))
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert len(shown) == 1 and "PR precisa de você" in shown[0][0]
    [n] = center.list(categories=["HUMAN_APPROVAL"])
    assert n.state == "ACTION_REQUIRED" and n.title.endswith("#7") and n.link.endswith("/pull/7")
    assert n.source == C.HUMAN_SOURCE
    c.on_snapshot(SimpleNamespace(approvals=None))                              # mesmo PR@HEAD: nada novo
    assert len(shown) == 1 and len(center.list(categories=["HUMAN_APPROVAL"])) == 1
    inboxes["next"] = _inbox(_item(7, head="b" * 40))                           # commit novo: avisa de novo
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert len(shown) == 2 and len(center.list(categories=["HUMAN_APPROVAL"])) == 2


def test_the_item_reaches_notifications_even_with_the_windows_notice_turned_off(core):
    c, inboxes, shown, center, _q, _n = core
    c.settings.notify_human = False
    inboxes["next"] = _inbox(_item(9))
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert shown == [] and len(center.list(categories=["HUMAN_APPROVAL"])) == 1


def test_the_collector_runs_with_no_bar(core):
    c, _i, _s, _c, _q, collected = core
    c.start()
    end = time.monotonic() + 5
    while time.monotonic() < end and collected["n"] < 2:
        _APP.processEvents()
        time.sleep(0.05)
    assert collected["n"] >= 2 and c.worker.alive()


def test_closing_without_a_bar_stops_the_cockpit_agents_and_the_collector_then_quits(core):
    c, _i, _s, center, quits, _n = core
    c.start()
    cockpit = c.open_cockpit()
    assert cockpit.shown == 1 and c.open_cockpit() is cockpit                   # segunda abertura: a mesma janela
    c.shutdown()
    assert cockpit.shut == 1 and not c.worker.alive() and quits == [1] and center.closed
    assert not c.tray.isVisible()
    c.shutdown()                                                                 # idempotente
    assert quits == [1] and cockpit.shut == 1


def test_the_bar_is_only_a_view_over_the_core(core, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BAR_SETTINGS", str(tmp_path / "settings.json"))
    from agent_bar import window as W
    c, inboxes, shown, center, quits, _n = core
    bar = W.Bar(c.settings, core=c)
    try:
        assert bar.worker is c.worker and bar.tray is c.tray and bar.notifier is c.notifier
        assert not c.worker.alive()                                              # quem dá a partida é o app, não a barra
        inboxes["next"] = _inbox(_item(11))
        c.on_snapshot(SimpleNamespace(approvals=None))                          # um aviso só: o do núcleo
        assert len(shown) == 1 and len(center.list(categories=["HUMAN_APPROVAL"])) == 1
        bar.action_cockpit()
        assert bar.cockpit is c.cockpit and c.cockpit.shown == 1
        bar.close_app()
        assert quits == [1] and c.cockpit.shut == 1
    finally:
        bar.details.close()
        bar.deleteLater()


def test_build_core_publishes_into_the_cockpit_notifications_database(tmp_path, monkeypatch):
    from agent_bar import app as A
    monkeypatch.setenv("AGENT_BAR_COCKPIT_DATA", str(tmp_path / "cockpit"))
    c = A.build_core(cfg.Settings())
    try:
        assert c.center is not None and c.center.path == str(tmp_path / "cockpit" / "permissions.sqlite")
    finally:
        c.worker.stop()
        c.center.close()
        if c.tray is not None:
            c.tray.hide()


# ------------------------------------------------- revisão do #98: aviso velho não fica pedindo ação para sempre
def _open(center):
    return [n for n in center.list(categories=["HUMAN_APPROVAL"]) if n.state == "ACTION_REQUIRED"]


def test_a_new_head_replaces_the_old_item_which_expires(core):
    c, inboxes, _s, center, _q, _n = core
    inboxes["next"] = _inbox(_item(7))
    c.on_snapshot(SimpleNamespace(approvals=None))
    inboxes["next"] = _inbox(_item(7, head="b" * 40))
    c.on_snapshot(SimpleNamespace(approvals=None))
    states = {n.dedupe_key: n.state for n in center.list(categories=["HUMAN_APPROVAL"])}
    assert [n.dedupe_key for n in _open(center)] == [C.human_key(_item(7).pr_ref, "b" * 40)]
    assert states[C.human_key(_item(7).pr_ref, _item(7).head_sha)] == "EXPIRED"


def test_a_pr_that_no_longer_needs_felipe_is_resolved_and_leaves_the_badge(core):
    c, inboxes, _s, center, _q, _n = core
    inboxes["next"] = _inbox(_item(7), _item(8))
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert len(_open(center)) == 2 and center.badge() == 2
    inboxes["next"] = _inbox(_item(8))                                            # o #7 foi mergeado/fechado/aprovado
    c.on_snapshot(SimpleNamespace(approvals=None))
    states = {n.title.rsplit("#", 1)[1]: n.state for n in center.list(categories=["HUMAN_APPROVAL"])}
    assert states == {"7": "RESOLVED", "8": "ACTION_REQUIRED"} and center.badge() == 1
    inboxes["next"] = _inbox()                                                    # nada mais pendente
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert _open(center) == [] and center.badge() == 0


def test_an_unreadable_inbox_closes_nothing(core):
    from agent_bar import inbox as ib
    c, inboxes, _s, center, _q, _n = core
    inboxes["next"] = _inbox(_item(7))
    c.on_snapshot(SimpleNamespace(approvals=None))
    inboxes["next"] = ib.Inbox("indisponivel", "gh fora do ar")
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert len(_open(center)) == 1                                                # não dá para saber: fica


def test_reconciling_never_touches_other_notifications(core):
    c, inboxes, _s, center, _q, _n = core
    center.publish("HUMAN_APPROVAL", "pedido de outra origem", state="ACTION_REQUIRED", source="outra",
                   dedupe_key="outra:1")
    center.publish("ERROR", "runtime", state="ACTION_REQUIRED", source="agent-bar:runtime", dedupe_key="rt:1")
    inboxes["next"] = _inbox()
    c.on_snapshot(SimpleNamespace(approvals=None))
    assert {n.dedupe_key: n.state for n in center.list()} == {"outra:1": "ACTION_REQUIRED", "rt:1": "ACTION_REQUIRED"}


def test_the_single_instance_requests_work_without_a_bar(core):
    import logging

    from agent_bar import app as A
    c, _i, _s, _center, quits, _n = core
    A._bring_forward(None, logging.getLogger("t"), c)                            # noqa: SLF001 - segunda abertura
    assert c.cockpit is not None and c.cockpit.shown == 1
    A._quit_for_update(None, logging.getLogger("t"), c)                          # noqa: SLF001 - versão nova pede para sair
    assert quits == [1] and c.cockpit.shut == 1 and not c.worker.alive()
