"""FR-06 (FRIENDLY-AUDIT): "Precisa de você" não garante o que não leu.

Antes: com a coleta do GitHub falhando (sem login, gh ausente, rede), o painel dizia "nenhum PR esperando por você
agora": uma garantia falsa. Agora, se a última leitura falhou, diz "Não foi possível ler o GitHub agora", com a ação do
FR-04 quando o motivo é login ou instalação. "nenhum PR" só aparece depois de uma leitura que deu certo.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar import inbox as ib  # noqa: E402
from agent_bar.cockpit.notifications import NotificationCenter  # noqa: E402
from agent_bar.cockpit.ui.notifications_area import NeedsYouPanel, github_reading  # noqa: E402
from agent_bar.models import AgentStatus, Level  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def core_with(inbox, gh_label: str = "Conectado"):
    return SimpleNamespace(inbox=inbox, snap=SimpleNamespace(github=AgentStatus("GitHub", Level.RED, gh_label, "x")))


MADE: list = []


@pytest.fixture(autouse=True)
def _dispose():
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    while MADE:
        w = MADE.pop()
        w.center.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def panel(tmp_path, core):
    p = NeedsYouPanel(NotificationCenter(tmp_path / "n.sqlite"), reading_fn=lambda: github_reading(core))
    MADE.append(p)
    p.refresh()
    return p


def test_a_failed_collection_never_says_no_pr_is_waiting(tmp_path):
    core = core_with(ib.Inbox("indisponivel", "gh sem login"), "Sem acesso")
    p = panel(tmp_path, core)
    text = p.summary.text()
    assert "nenhum PR" not in text and "Não foi possível ler o GitHub agora" in text
    assert not p.connect_box.isHidden() and p.connect_box.primary.text() == "Conectar"   # a ação do FR-04


def test_a_failed_collection_without_gh_offers_install(tmp_path):
    p = panel(tmp_path, core_with(ib.Inbox("indisponivel", "gh nao encontrado"), "Nao instalado"))
    assert "nenhum PR" not in p.summary.text() and p.connect_box.primary.text() == "Instalar"


def test_a_failure_that_is_not_login_has_no_connect_button_but_still_no_promise(tmp_path):
    p = panel(tmp_path, core_with(ib.Inbox("indisponivel", "API rate limit exceeded"), "Logado, leituras falhando"))
    assert "nenhum PR" not in p.summary.text() and "Não foi possível ler o GitHub agora" in p.summary.text()
    assert p.connect_box.isHidden()


def test_before_the_first_collection_it_says_it_is_still_reading(tmp_path):
    p = panel(tmp_path, SimpleNamespace(inbox=None, snap=None))
    assert "nenhum PR" not in p.summary.text() and "lendo o GitHub" in p.summary.text()


def test_a_successful_empty_collection_says_no_pr(tmp_path):
    p = panel(tmp_path, core_with(ib.Inbox("lido", "0 PR(s) esperando você")))
    assert "nenhum PR" in p.summary.text() and p.connect_box.isHidden()


def test_with_approvals_none_and_a_collection_error_the_text_has_no_promise(tmp_path):
    """O critério literal: snapshot com approvals=None e erro de coleta."""
    board = SimpleNamespace(error="falha na coleta: gh sem login", rows=[], reviewer="")
    core = core_with(ib.human_inbox(board), "Sem acesso")
    core.snap.approvals = None
    assert "nenhum PR" not in panel(tmp_path, core).summary.text()
