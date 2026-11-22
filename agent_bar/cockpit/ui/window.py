"""window.py — janela do cockpit ACP (D2): QMainWindow redimensionavel com abas Chat, PRs e Actions."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from agent_bar.cockpit.acp import AsyncRuntime
from agent_bar.cockpit.github import GhClient, configured_repos
from agent_bar.cockpit.ui.chat import ChatView
from agent_bar.cockpit.ui.github_tabs import ActionsTab, PrsTab
from agent_bar.targets import load_targets

TAB_CSS = ("QTabWidget::pane{border:0;background:#16161a}QTabBar::tab{background:#202026;color:#bdbdc4;padding:6px 14px;"
           "border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px}"
           "QTabBar::tab:selected{background:#2c2c34;color:#ffffff}")


class CockpitWindow(QMainWindow):
    def __init__(self, gh: GhClient | None = None, repos: list[str] | None = None, **chat_kwargs) -> None:
        super().__init__()
        self.setWindowTitle("Cockpit ACP — Agent Bar")
        self.resize(980, 720)
        self.runtime = AsyncRuntime("cockpit")
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_CSS)
        self.chat = ChatView(runtime=self.runtime, **chat_kwargs)
        self.tabs.addTab(self.chat, "Chat")
        gh = gh or GhClient()
        repos = repos or configured_repos(load_targets().org)
        self.prs = PrsTab(gh, repos=repos)
        self.actions = ActionsTab(gh, repos=repos)
        self.tabs.addTab(self.prs, "PRs")
        self.tabs.addTab(self.actions, "Actions")
        self.prs.review_requested.connect(self.open_review)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.setStyleSheet("QMainWindow{background:#16161a}")
        self._closed = False

    def _tab_changed(self, idx: int) -> None:
        tab = self.tabs.widget(idx)
        if isinstance(tab, (PrsTab, ActionsTab)) and tab.req == 0:            # primeira visita: carrega
            tab.refresh()

    def open_review(self, workspace: str, prompt: str) -> None:
        """Revisao assistida (R21): conversa nova no checkout do repo com o prompt pronto. Enviar e com o Felipe."""
        self.tabs.setCurrentWidget(self.chat)
        self.chat.prefill(workspace, prompt)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.prs.shutdown()
        self.actions.shutdown()
        self.chat.shutdown()
        self.runtime.stop()

    def closeEvent(self, e) -> None:                                            # noqa: N802
        self.shutdown()
        super().closeEvent(e)
