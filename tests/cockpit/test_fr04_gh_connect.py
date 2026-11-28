"""FR-04 (FRIENDLY-AUDIT): erro do GitHub com ação, o mesmo componente em PRs, Actions e Saúde.

Antes: sem login, a aba PRs mostrava a tabela vazia, o painel de aprovação preto, botões desabilitados e a linha
vermelha "[auth] gh sem login: rode `gh auth login` num terminal" (que supõe saber abrir um terminal). Agora o
componente "GitHub não conectado" substitui a tabela e o painel: uma frase e os botões Conectar (ou Instalar, quando o
gh nem existe) e Verificar de novo. Nenhum "[auth]" nem "rc=" visível.
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from agent_bar.cockpit.github import GhClient, GhError  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
REPO = "o/r"


class FailingGh(GhClient):
    def __init__(self, kind: str) -> None:
        super().__init__(exe=["nao-usado"])
        self.kind = kind
        self.reads = 0

    def _fail(self, *a, **k):
        self.reads += 1
        if self.kind == "ok":
            return []
        raise GhError(self.kind, {"auth": "gh sem login: rode `gh auth login` num terminal",
                                  "not_found": "gh nao encontrado no PATH"}[self.kind])

    prs = runs = _fail


def wait(cond, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def visible_text(root) -> str:
    out = []
    for w in [root, *root.findChildren(QtWidgets.QWidget)]:
        if not w.isVisibleTo(root) and w is not root:
            continue
        if isinstance(w, (QtWidgets.QLabel, QtWidgets.QAbstractButton)):
            out.append(w.text())
    return "\n".join(out)


def dispose(w):
    from PySide6.QtCore import QCoreApplication, QEvent
    w.shutdown()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def prs_tab(gh, **kw):
    from agent_bar.cockpit.ui.github_tabs import PrsTab
    return PrsTab(gh, repos=[REPO], **kw)


def actions_tab(gh, **kw):
    from agent_bar.cockpit.ui.github_tabs import ActionsTab
    return ActionsTab(gh, repos=[REPO], **kw)


@pytest.mark.parametrize("make", [prs_tab, actions_tab], ids=["prs", "actions"])
def test_without_login_the_tab_offers_connect_instead_of_an_empty_table(make):
    tab = make(FailingGh("auth"))
    try:
        tab.resize(1000, 640)
        tab.show()
        tab.refresh()
        assert wait(lambda: tab.connect_box.isVisibleTo(tab))
        assert not tab.body.isVisibleTo(tab)                                  # tabela (e painel de aprovação) fora
        if hasattr(tab, "approval"):
            assert not tab.approval.isVisibleTo(tab)
        assert tab.connect_box.primary.text() == "Conectar" and tab.connect_box.retry_btn.text() == "Verificar de novo"
        assert "Conecte sua conta do GitHub" in tab.connect_box.label.text()
        text = visible_text(tab)
        assert "[auth]" not in text and "rc=" not in text and "gh auth login" not in text, text
    finally:
        dispose(tab)


@pytest.mark.parametrize("make", [prs_tab, actions_tab], ids=["prs", "actions"])
def test_without_gh_the_tab_says_not_installed_and_offers_install(make):
    tab = make(FailingGh("not_found"))
    try:
        tab.show()
        tab.refresh()
        assert wait(lambda: tab.connect_box.isVisibleTo(tab))
        assert "não instalado" in tab.connect_box.label.text()
        assert tab.connect_box.primary.text() == "Instalar"
        assert "[not_found]" not in visible_text(tab)
    finally:
        dispose(tab)


def test_connect_opens_the_login_terminal_and_retry_reads_again():
    from agent_bar.cockpit.ui.gh_connect import GhConnect
    opened, urls = [], []
    gh = FailingGh("auth")
    tab = prs_tab(gh)
    tab.connect_box.open_terminal = lambda: opened.append(True) or True
    tab.connect_box.open_url = urls.append
    try:
        tab.show()
        tab.refresh()
        assert wait(lambda: tab.connect_box.isVisibleTo(tab))
        tab.connect_box.primary.click()
        assert opened == [True] and urls == []
        gh.kind = "ok"                                                        # o usuário entrou no terminal
        before = gh.reads
        tab.connect_box.retry_btn.click()
        assert wait(lambda: gh.reads > before and not tab.connect_box.isVisibleTo(tab))
        assert tab.body.isVisibleTo(tab)                                      # leu: a tabela volta
    finally:
        dispose(tab)
    assert GhConnect is not None


def test_when_no_terminal_can_be_opened_the_command_is_copied():
    from agent_bar.cockpit.ui.gh_connect import GhConnect
    box = GhConnect("os PRs", open_terminal=lambda: False)
    box.show_kind("auth")
    box.primary.click()
    assert QtWidgets.QApplication.clipboard().text() == "gh auth login"
    assert "copiado" in box.label.text()


def test_install_opens_the_install_page():
    from agent_bar.cockpit.ui.gh_connect import INSTALL_URL, GhConnect
    urls = []
    box = GhConnect("os PRs", open_url=urls.append)
    box.show_kind("not_found")
    box.primary.click()
    assert urls == [INSTALL_URL]


def test_health_github_card_shows_the_same_component():
    from types import SimpleNamespace

    from agent_bar.cockpit import health as H
    from agent_bar.cockpit.ui import settings_tab as S
    from agent_bar.models import AgentStatus, Level
    for label, kind, primary in (("Sem acesso", "auth", "Conectar"), ("Nao instalado", "not_found", "Instalar")):
        snap = SimpleNamespace(github=AgentStatus("GitHub", Level.RED if kind == "auth" else Level.GRAY, label, "x"),
                               pr_queue=SimpleNamespace(error="falhou", rows=[], synced=""))
        row = H.github_row(snap)
        assert row.action == kind
        w = S._row_widget(row)                                                # noqa: SLF001
        boxes = w.findChildren(GhConnectType())
        assert len(boxes) == 1 and boxes[0].primary.text() == primary


def GhConnectType():                                                          # noqa: N802
    from agent_bar.cockpit.ui.gh_connect import GhConnect
    return GhConnect
