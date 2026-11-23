"""window.py — janela do cockpit ACP (D2): QMainWindow redimensionavel com abas Chat, Permissões, Fluxos, Revisão visual, Biblioteca, PRs, Actions e Dev."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QTabWidget

from agent_bar.cockpit.acp import AsyncRuntime
from agent_bar.cockpit.github import GhClient, configured_repos
from agent_bar.cockpit.notifications import NotificationCenter
from agent_bar.cockpit.ui.chat import ChatView
from agent_bar.cockpit.ui.dev_tab import DevTab
from agent_bar.cockpit.ui.github_tabs import ActionsTab, PrsTab
from agent_bar.cockpit.ui.library_tab import LibraryTab
from agent_bar.cockpit.ui.notifications_tab import NotificationsTab
from agent_bar.cockpit.ui.permissions_tab import PermissionsTab
from agent_bar.cockpit.ui.review_tab import ReviewTab
from agent_bar.cockpit.ui.workflows_tab import WorkflowsTab
from agent_bar.targets import load_targets

TAB_CSS = ("QTabWidget::pane{border:0;background:#16161a}QTabBar::tab{background:#202026;color:#bdbdc4;padding:6px 14px;"
           "border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px}"
           "QTabBar::tab:selected{background:#2c2c34;color:#ffffff}")


class CockpitWindow(QMainWindow):
    def __init__(self, gh: GhClient | None = None, repos: list[str] | None = None, discover_fn=None, specs_root=None,
                 dev_kwargs: dict | None = None, review_kwargs: dict | None = None, **chat_kwargs) -> None:
        super().__init__()
        self.setWindowTitle("P2S Studio — Cockpit")
        self.resize(980, 720)
        self.runtime = AsyncRuntime("cockpit")
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_CSS)
        self.library: LibraryTab | None = None
        self.chat = ChatView(runtime=self.runtime, context_provider=lambda: self.library.context() if self.library else "",
                             **chat_kwargs)
        self.library = LibraryTab(self.chat.workspace, **({"discover_fn": discover_fn} if discover_fn else {}))
        wf_kwargs = {k: chat_kwargs[k] for k in ("profiles_fn", "ask_permission") if k in chat_kwargs}
        self.workflows = WorkflowsTab(self.runtime, self.chat.workspace, context_fn=self.library.context,
                                      specs_root=specs_root, **wf_kwargs)
        self.tabs.addTab(self.chat, "Chat")
        self.tabs.addTab(self.workflows, "Fluxos")
        self.review = ReviewTab(**(review_kwargs or {}))
        self.tabs.addTab(self.review, "Revisão visual")
        self.review.packages.send_to_chat.connect(self.open_correction)
        self.tabs.addTab(self.library, "Biblioteca")
        gh = gh or GhClient()
        repos = repos or configured_repos(load_targets().org)
        self.prs = PrsTab(gh, repos=repos)
        self.actions = ActionsTab(gh, repos=repos)
        self.tabs.addTab(self.prs, "PRs")
        self.tabs.addTab(self.actions, "Actions")
        self.dev = DevTab(use_workspace=self._use_sandbox, **(dev_kwargs or {}))
        self.tabs.addTab(self.dev, "Dev")
        self.permissions = PermissionsTab(self.chat.inbox)                    # F4: o mesmo inbox que o Chat grava
        self.tabs.insertTab(1, self.permissions, "Permissões")
        self.center = NotificationCenter(self.chat.inbox.path)              # F3: mesmo SQLite do Inbox
        self.notifications = NotificationsTab(self.center, inbox=self.chat.inbox)
        self.tabs.insertTab(2, self.notifications, "Notificações")
        self.tabs.setCornerWidget(self.notifications.bell, Qt.Corner.TopRightCorner)
        self.notifications.bell.show()
        self.notifications.bell.clicked.connect(lambda: self.tabs.setCurrentWidget(self.notifications))
        self.notifications.open_link.connect(self.open_link)
        self.chat.notify_fn = self._notify
        self.chat.visible_fn = lambda: self.isActiveWindow() and self.tabs.currentWidget() is self.chat
        self.workflows.notify_fn = self._notify
        self.prs.review_requested.connect(self.open_review)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.setStyleSheet("QMainWindow{background:#16161a}")
        self._closed = False

    def _notify(self, category: str, title: str, body: str = "", session_id: str = "", dedupe_key: str | None = None,
                state: str = "NEW") -> None:
        self.center.publish(category, title, body=body, session_id=session_id, dedupe_key=dedupe_key, state=state,
                            link="chat" if category in ("AGENT", "ERROR") else ("workflows" if category == "TASK" else ""))
        self.notifications.refresh()

    def open_link(self, link: str) -> None:
        """Leva a origem de uma notificacao: `permissions:<id>`, `chat`, `workflows`."""
        kind, _, ref = link.partition(":")
        target = {"permissions": self.permissions, "chat": self.chat, "workflows": self.workflows}.get(kind)
        if target is None:
            return
        self.tabs.setCurrentWidget(target)
        if target is self.permissions and ref:
            self.permissions.select(ref)

    def _tab_changed(self, idx: int) -> None:
        tab = self.tabs.widget(idx)
        if isinstance(tab, (PrsTab, ActionsTab)) and tab.req == 0:            # primeira visita: carrega
            tab.refresh()
        elif tab is self.library:                                            # workspace pode ter mudado no Chat
            self.library.ensure_loaded()
        elif tab is self.review and self.review.req == 0:                    # primeira visita: lê o evidence
            self.review.reload()

    def _use_sandbox(self, workspace: str) -> None:
        """F6: o Chat passa a trabalhar no sandbox (o agente nunca edita o checkout direto)."""
        self.chat.prefill(workspace, "")

    def open_correction(self, workspace: str, text: str) -> None:
        """F7.4: o pedido de correção vai para o Chat preenchido (no checkout do motor, se existir); o envio é manual."""
        self.tabs.setCurrentWidget(self.chat)
        if workspace:
            self.chat.prefill(workspace, text)
        else:
            self.chat.input.setPlainText(text)

    def open_review(self, workspace: str, prompt: str) -> None:
        """Revisao assistida (R21): conversa nova no checkout do repo com o prompt pronto. Enviar e com o Felipe."""
        self.tabs.setCurrentWidget(self.chat)
        self.chat.prefill(workspace, prompt)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.notifications.shutdown()
        self.permissions.shutdown()
        self.workflows.shutdown()
        self.review.shutdown()
        self.dev.shutdown()
        self.prs.shutdown()
        self.library.shutdown()
        self.actions.shutdown()
        self.chat.shutdown()
        self.center.close()
        self.runtime.stop()

    def closeEvent(self, e) -> None:                                            # noqa: N802
        self.shutdown()
        super().closeEvent(e)
