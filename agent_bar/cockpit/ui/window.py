"""window.py — janela do cockpit ACP (D2): QMainWindow redimensionavel com abas Chat, Permissões, Fluxos, Revisão visual, Biblioteca, PRs, Actions e Dev."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QTabWidget

from agent_bar.cockpit import window_state as WS
from agent_bar.cockpit.acp import AsyncRuntime
from agent_bar.cockpit.github import GhClient, configured_repos
from agent_bar.cockpit.notifications import NotificationCenter, image_notification
from agent_bar.cockpit.run_vision import RunVision
from agent_bar.cockpit.ui.chat import ChatView
from agent_bar.cockpit.ui.chat_sessions import ChatSessions
from agent_bar.cockpit.ui.dev_tab import DevTab
from agent_bar.cockpit.ui.github_tabs import ActionsTab, PrsTab
from agent_bar.cockpit.ui.library_tab import LibraryTab
from agent_bar.cockpit.ui.notifications_tab import NotificationsTab
from agent_bar.cockpit.ui.permissions_tab import PermissionsTab
from agent_bar.cockpit.ui.review_tab import ReviewTab
from agent_bar.cockpit.ui.workflows_tab import WorkflowsTab
from agent_bar.platform.image_ledger import ImageLedger
from agent_bar.platform.sessions import SessionManager
from agent_bar.targets import load_targets

TAB_CSS = ("QTabWidget::pane{border:0;background:#16161a}QTabBar::tab{background:#202026;color:#bdbdc4;padding:6px 14px;"
           "border-top-left-radius:6px;border-top-right-radius:6px;margin-right:2px}"
           "QTabBar::tab:selected{background:#2c2c34;color:#ffffff}")


class PageScroll(QScrollArea):
    """Uma aba dentro de uma área de rolagem: acima do mínimo da aba ela ocupa todo o espaço; abaixo dele (altura OU
    largura) aparece a barra de rolagem em vez de espremer widgets uns sobre os outros. O QScrollArea acompanha sozinho
    o mínimo quando a aba cresce depois (a galeria termina de carregar, uma legenda aumenta)."""

    def __init__(self, page) -> None:
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidget(page)


class ScrollTabs(QTabWidget):
    """Abas cujas PÁGINAS rolam e cuja barra de abas fica sempre fixa no topo da janela (UX-04). Para quem usa, as
    páginas continuam sendo os próprios widgets: `widget`, `currentWidget`, `setCurrentWidget` e `indexOf` desembrulham."""

    def addTab(self, page, *args):                                               # noqa: N802
        return super().addTab(PageScroll(page), *args)

    def insertTab(self, index, page, *args):                                     # noqa: N802
        return super().insertTab(index, PageScroll(page), *args)

    @staticmethod
    def _page(w):
        return w.page if isinstance(w, PageScroll) else w

    def widget(self, index):
        return self._page(super().widget(index))

    def currentWidget(self):                                                     # noqa: N802
        return self._page(super().currentWidget())

    def indexOf(self, page) -> int:                                              # noqa: N802
        return next((i for i in range(self.count()) if self.widget(i) is page), -1)

    def setCurrentWidget(self, page) -> None:                                    # noqa: N802
        i = self.indexOf(page)
        if i >= 0:
            self.setCurrentIndex(i)


class CockpitWindow(QMainWindow):
    def __init__(self, gh: GhClient | None = None, repos: list[str] | None = None, discover_fn=None, specs_root=None,
                 dev_kwargs: dict | None = None, review_kwargs: dict | None = None,
                 window_store: WS.WindowStateStore | None = None, **chat_kwargs) -> None:
        super().__init__()
        self.setWindowTitle("P2S Studio — Cockpit")
        self.window_store = window_store or WS.WindowStateStore()
        self.restore_window()
        self.runtime = AsyncRuntime("cockpit")
        self.tabs = ScrollTabs()                                                # UX-04: páginas rolam, abas fixas
        self.tabs.setStyleSheet(TAB_CSS)
        self.library: LibraryTab | None = None
        inbox = chat_kwargs.pop("inbox", None)
        self.sessions = ChatSessions(runtime=self.runtime, inbox=inbox,              # F5: abas de sessao no Chat
                                     context_provider=lambda: self.library.context() if self.library else "",
                                     **chat_kwargs)
        self.library = LibraryTab(lambda: self.chat.workspace(), **({"discover_fn": discover_fn} if discover_fn else {}))
        wf_kwargs = {k: chat_kwargs[k] for k in ("profiles_fn", "ask_permission") if k in chat_kwargs}
        self.workflows = WorkflowsTab(self.runtime, lambda: self.chat.workspace(), context_fn=self.library.context,
                                      specs_root=specs_root, **wf_kwargs)
        self.tabs.addTab(self.sessions, "Chat")
        self.tabs.addTab(self.workflows, "Fluxos")
        self.review = ReviewTab(**(review_kwargs or {}))
        self.tabs.addTab(self.review, "Revisão visual")
        self.review.packages.send_to_chat.connect(self.open_correction)
        self.review.request_chat.connect(self.open_correction)               # S01C-F1-11: comentário da galeria
        self.tabs.addTab(self.library, "Biblioteca")
        gh = gh or GhClient()
        repos = repos or configured_repos(load_targets().org)
        self.prs = PrsTab(gh, repos=repos)
        self.actions = ActionsTab(gh, repos=repos)
        self.tabs.addTab(self.prs, "PRs")
        self.tabs.addTab(self.actions, "Actions")
        self.dev = DevTab(use_workspace=self._use_sandbox, **(dev_kwargs or {}))
        self.tabs.addTab(self.dev, "Dev")
        self.permissions = PermissionsTab(self.sessions.inbox, answer_fn=self.sessions.answer_permission,
                                          is_open_fn=self.sessions.broker.is_open)  # um inbox e um broker para todas as abas
        self.tabs.insertTab(1, self.permissions, "Permissões")
        self.center = NotificationCenter(self.sessions.inbox.path)          # F3: mesmo SQLite do Inbox
        # S01C-R3: toda imagem entregue a um agente fica registrada (tabela propria, mesmo SQLite) e aparece nas
        # Notificacoes; so publica aqui (thread-safe), a aba se atualiza pelo proprio timer
        self.image_ledger = ImageLedger(self.sessions.inbox.path,
                                        on_change=lambda rows: self.center.publish(**image_notification(rows)))
        # "avaliar este run" (Revisão visual): SessionManager da plataforma com ESTE ledger (um registro só)
        self.platform_sessions = SessionManager({"ollama": _ollama_adapter}, image_ledger=self.image_ledger)
        self.review.set_vision(RunVision(self.platform_sessions, self.image_ledger))
        self.notifications = NotificationsTab(self.center, inbox=self.sessions.inbox, answer_fn=self.sessions.answer_permission,
                                              is_open_fn=self.sessions.broker.is_open)
        self.sessions.permission_listeners += [lambda _id: self.permissions.refresh(), lambda _id: self.notifications.refresh()]
        self.tabs.insertTab(2, self.notifications, "Notificações")
        self.tabs.setCornerWidget(self.notifications.bell, Qt.Corner.TopRightCorner)
        self.notifications.bell.show()
        self.notifications.bell.clicked.connect(lambda: self.tabs.setCurrentWidget(self.notifications))
        self.notifications.open_link.connect(self.open_link)
        self.sessions.notify_fn = self._notify
        self.sessions.visible_fn = lambda: self.isActiveWindow() and self.tabs.currentWidget() is self.sessions
        self.workflows.notify_fn = self._notify
        self.workflows.inbox = self.sessions.inbox                           # pedidos dos Fluxos no mesmo Inbox
        self.prs.review_requested.connect(self.open_review)
        self.tabs.currentChanged.connect(self._tab_changed)
        # UX-04: abaixo do mínimo de uma aba (a Revisão visual pede ~1064×943 no Windows) a PÁGINA rola em vez de
        # espremer os widgets uns sobre os outros; a barra de abas nunca sai da janela
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
        target = {"permissions": self.permissions, "chat": self.sessions, "workflows": self.workflows}.get(kind)
        if target is None:
            return
        self.tabs.setCurrentWidget(target)
        if target is self.permissions and ref:
            self.permissions.select(ref)

    @property
    def chat(self) -> ChatView:
        """A conversa da aba de sessao atual (compatibilidade: o resto da janela fala com "o Chat")."""
        return self.sessions.current()

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
        self.tabs.setCurrentWidget(self.sessions)
        if workspace:
            self.chat.prefill(workspace, text)
        else:
            self.chat.input.setPlainText(text)

    def open_review(self, workspace: str, prompt: str) -> None:
        """Revisao assistida (R21): conversa nova no checkout do repo com o prompt pronto. Enviar e com o Felipe."""
        self.tabs.setCurrentWidget(self.sessions)
        self.chat.prefill(workspace, prompt)

    def shutdown(self) -> None:
        if self._closed:
            return
        if self.isVisible():                                                    # "Encerrar"/troca de versão pelo núcleo
            self.save_window()
        self._closed = True
        self.notifications.shutdown()
        self.permissions.shutdown()
        self.workflows.shutdown()
        self.review.shutdown()
        self.dev.shutdown()
        self.prs.shutdown()
        self.library.shutdown()
        self.actions.shutdown()
        self.sessions.shutdown(close_store=False)                           # #75: conversas fechadas, banco ainda aberto
        self.runtime.stop()                                                  # nenhum callback do agente chega depois daqui
        self.sessions.close_store()                                          # so agora os bancos
        self.platform_sessions.close_all()                                   # antes do ledger: nenhum envio depois
        self.image_ledger.close()
        self.center.close()

    # ------------------------------------------------------------------------------------------------ janela (UX-04)
    def restore_window(self) -> WS.WindowState:
        """Reabre onde estava; monitor que sumiu → monitor visível; nunca maior que a área útil."""
        screens = screen_rects()
        st = WS.restore(self.window_store.load(), screens)
        # UX-04: 900×600, cedendo numa área útil menor (a moldura nunca passa da barra de tarefas)
        self.setMinimumSize(*WS.min_size(WS.screen_of(st, screens)))
        self.resize(st.w, st.h)
        self.move(st.x, st.y)
        self._normal, self._prev_normal = (st.x, st.y, st.w, st.h), None        # o tamanho normal, mesmo se maximizada
        if st.maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        return st

    def _remember_normal(self) -> None:
        """Posição da MOLDURA e tamanho do conteúdo enquanto a janela está normal: é o que volta ao sair do maximizado.
        (normalGeometry() dá o canto do conteúdo, não da moldura: salvar dele desceria a janela uma barra de título a
        cada ciclo maximizar/reabrir.)"""
        if self.windowState() == Qt.WindowState.WindowNoState and self.isVisible():
            now = (self.pos().x(), self.pos().y(), self.width(), self.height())
            if now != getattr(self, "_normal", None):
                self._prev_normal, self._normal = getattr(self, "_normal", None), now

    def changeEvent(self, e) -> None:                                           # noqa: N802
        """O Windows entrega o resize do maximizar ANTES de o estado virar maximizado: o último "normal" registrado é
        o tamanho maximizado; volta para o anterior."""
        super().changeEvent(e)
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.Type.WindowStateChange and self.isMaximized():
            last = getattr(self, "_normal", None)
            if last and (last[2], last[3]) == (self.width(), self.height()) and getattr(self, "_prev_normal", None):
                self._normal = self._prev_normal

    def moveEvent(self, e) -> None:                                             # noqa: N802
        super().moveEvent(e)
        self._remember_normal()

    def resizeEvent(self, e) -> None:                                           # noqa: N802
        super().resizeEvent(e)
        self._remember_normal()

    def save_window(self) -> WS.WindowState:
        self._remember_normal()
        x, y, w, h = getattr(self, "_normal", None) or (self.pos().x(), self.pos().y(), self.width(), self.height())
        st = WS.WindowState(x, y, w, h, self.isMaximized())
        try:
            self.window_store.save(st)
        except OSError:                                                        # salvar a janela nunca impede fechar
            pass
        return st

    def closeEvent(self, e) -> None:                                            # noqa: N802
        if not self._closed:
            self.save_window()
        self.shutdown()
        super().closeEvent(e)


def screen_rects() -> list[WS.Rect]:
    """Áreas úteis dos monitores (lógicas), o principal primeiro."""
    primary = QGuiApplication.primaryScreen()
    screens = [primary] + [s for s in QGuiApplication.screens() if s is not primary]
    out = []
    for sc in screens:
        g = sc.availableGeometry()
        out.append(WS.Rect(g.x(), g.y(), g.width(), g.height()))
    return out


def _ollama_adapter(*_workspace):
    """Adaptador Ollama no endpoint descoberto (OLLAMA_HOST ou o padrão), como o Chat."""
    from agent_bar.platform.discovery import discover
    from agent_bar.platform.ollama import OllamaAdapter
    endpoint = next(p["endpoint"] for p in discover(which=lambda _n: None) if p["provider_id"] == "ollama")
    return OllamaAdapter(endpoint, timeout=120)
