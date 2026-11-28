"""window.py — janela do cockpit ACP (D2): QMainWindow redimensionavel com abas Chat, Permissões, Fluxos, Revisão visual, Biblioteca, PRs, Actions e Dev."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent_bar import controls as ctl
from agent_bar.cockpit import window_state as WS
from agent_bar.cockpit.acp import AsyncRuntime
from agent_bar.cockpit.evaluations import EvaluationStore
from agent_bar.cockpit.evidence import evidence_dir
from agent_bar.cockpit.gallery import engine_dir, library_dir
from agent_bar.cockpit.github import GhClient, configured_repos
from agent_bar.cockpit.notifications import NotificationCenter, image_notification
from agent_bar.cockpit.run_vision import RunVision
from agent_bar.cockpit.ui.chat import ChatView
from agent_bar.cockpit.ui.chat_sessions import ChatSessions
from agent_bar.cockpit.ui.dev_tab import DevTab
from agent_bar.cockpit.ui.github_tabs import ActionsTab, PrsTab
from agent_bar.cockpit.ui.library_tab import LibraryTab
from agent_bar.cockpit.ui.notifications_area import NeedsYouPanel, NotificationsArea, github_reading
from agent_bar.cockpit.ui.notifications_tab import NotificationsTab
from agent_bar.cockpit.ui.permissions_tab import PermissionsTab
from agent_bar.cockpit.ui.review_tab import ReviewTab
from agent_bar.cockpit.ui.settings_tab import SettingsTab
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
                 dev_kwargs: dict | None = None, review_kwargs: dict | None = None, runtime_pin=None, core=None,
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
                                     context_provider=self._chat_context, context_sent=self._chat_context_sent,
                                     **chat_kwargs)
        self.library = LibraryTab(lambda: self.chat.workspace(), **({"discover_fn": discover_fn} if discover_fn else {}))
        wf_kwargs = {k: chat_kwargs[k] for k in ("profiles_fn", "ask_permission") if k in chat_kwargs}
        self.workflows = WorkflowsTab(self.runtime, lambda: self.chat.workspace(), context_fn=self.library.context,
                                      specs_root=specs_root, **wf_kwargs)
        self.review = ReviewTab(**{"evaluation_store": EvaluationStore(protected=(evidence_dir(), engine_dir(), library_dir())),
                                   **(review_kwargs or {})})
        self.review.packages.send_to_chat.connect(self.open_correction)
        self.review.request_chat.connect(self.open_correction)               # S01C-F1-11: comentário da galeria
        gh = gh or GhClient()
        repos = repos or configured_repos(load_targets().org)
        self.prs = PrsTab(gh, repos=repos,                                     # UX-07: aprovação do núcleo
                          approvals_fn=lambda: getattr(getattr(core, "snap", None), "approvals", None))
        self.actions = ActionsTab(gh, repos=repos)
        self.dev = DevTab(use_workspace=self._use_sandbox, **(dev_kwargs or {}))
        self.permissions = PermissionsTab(self.sessions.inbox, answer_fn=self.sessions.answer_permission,
                                          is_open_fn=self.sessions.broker.is_open)  # um inbox e um broker para todas as abas
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
        # UX-08: Permissões e "Precisa de você" dentro de Notificações (uma área só para o que pede o Felipe)
        self.needs = NeedsYouPanel(self.center, reading_fn=lambda: github_reading(core),   # FR-06: sem garantia falsa
                                   retry_fn=lambda: getattr(getattr(core, "worker", None), "refresh", lambda **_k: None)(force=True))
        self.notifications_area = NotificationsArea(self.notifications, self.needs, self.permissions)
        self.sessions.permission_listeners += [lambda _id: self.permissions.refresh(), lambda _id: self.notifications.refresh(),
                                               lambda _id: self.notifications_area.update_counts()]
        # UX-07 (D-UX-2): exatamente 4 áreas principais, nessa ordem
        for page, title in ((self.sessions, "Chat"), (self.review, "Design"), (self.prs, "PRs"),
                            (self.notifications_area, "Notificações")):
            self.tabs.addTab(page, title)
        # Configurações → Avançado: o que saiu da navegação principal continua a um clique (VS-I1)
        self.advanced = ScrollTabs()
        self.advanced.setStyleSheet(TAB_CSS)
        for page, title in ((self.workflows, "Fluxos"), (self.dev, "Dev"), (self.library, "Biblioteca"),
                            (self.actions, "Actions")):
            self.advanced.addTab(page, title)
        corner = QWidget()                                                     # sino + engrenagem no canto das abas
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(0, 0, 6, 0)
        cl.setSpacing(4)
        self.gear = QToolButton()
        self.gear.setText("⚙")
        self.gear.setToolTip("Configurações: Geral, Saúde do sistema, Diagnóstico e Avançado")
        self.gear.setAccessibleName("Configurações")
        self.gear.setStyleSheet("QToolButton{color:#cfcfd4;font-size:16px;border:0;padding:2px 6px}"
                                "QToolButton:hover{color:#ffffff;background:#2a2a30;border-radius:4px}")
        cl.addWidget(self.notifications.bell)
        cl.addWidget(self.gear)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self.notifications.bell.show()
        self.notifications.bell.clicked.connect(lambda: self.show_page(self.notifications))
        self.gear.clicked.connect(lambda: self.show_page(self.settings))
        self.notifications.open_link.connect(self.open_link)
        self.sessions.notify_fn = self._notify
        self.sessions.visible_fn = lambda: self.isActiveWindow() and self.current_page() is self.sessions
        self.workflows.notify_fn = self._notify
        self.workflows.inbox = self.sessions.inbox                           # pedidos dos Fluxos no mesmo Inbox
        self.prs.review_requested.connect(self.open_review)
        self.tabs.currentChanged.connect(lambda _i: self._page_shown(self.current_page()))
        self.advanced.currentChanged.connect(lambda _i: self._page_shown(self.current_page()))
        # UX-04: abaixo do mínimo de uma aba (a Revisão visual pede ~1064×943 no Windows) a PÁGINA rola em vez de
        # espremer os widgets uns sobre os outros; a barra de abas nunca sai da janela
        body = QWidget()                                                       # UX-02: aviso do runtime acima das abas
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.runtime_banner = QLabel("")
        self.runtime_banner.setWordWrap(True)
        self.runtime_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.runtime_banner.setStyleSheet("QLabel{background:#3a1d1f;color:#ffb4b4;border-bottom:1px solid #6b2a2e;"
                                          "padding:8px 12px;font-size:12px}")
        lay.addWidget(self.runtime_banner)
        self.stack = QStackedWidget()                                          # áreas | Configurações
        self.stack.addWidget(self.tabs)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(body)
        self.set_runtime_pin(runtime_pin)
        # UX-05: barra de estado com a fonte, o commit e a hora da leitura do que a Revisão visual mostra
        self.status_sources = QLabel("fontes ainda não lidas (abra a Revisão visual)")
        self.status_sources.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # nunca alarga a janela
        self.status_sources.setStyleSheet("color:#8f8f98;font-size:11px;padding:0 6px")
        self.statusBar().setStyleSheet("QStatusBar{background:#141417;border-top:1px solid #26262c}")
        self.statusBar().addWidget(self.status_sources, 1)
        self.review.sources_changed.connect(self._sources_changed)
        # UX-06: Configurações (Saúde do sistema, Diagnóstico) dentro da janela principal; a Central da barra não abre
        self.core = core
        self.settings = SettingsTab(core, chat_fn=lambda: self.chat, review=self.review)   # a sessão aberta AGORA
        self.settings.add_section("Avançado", self.advanced)
        self.settings.sections.currentRowChanged.connect(lambda _r: self._page_shown(self.current_page()))
        page = QWidget()                                                       # Configurações: página da janela
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 6, 10, 0)
        self.settings_back = QPushButton("← Voltar")
        self.settings_back.setStyleSheet("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;"
                                         "border-radius:5px;padding:4px 12px}QPushButton:hover{background:#34343c}")
        title = QLabel("Configurações")
        title.setStyleSheet("color:#ffffff;font-size:15px;font-weight:600;padding-left:8px")
        head.addWidget(self.settings_back)
        head.addWidget(title)
        head.addStretch(1)
        pl.addLayout(head)
        pl.addWidget(self.settings, 1)
        self.settings_page = page
        self.stack.addWidget(page)
        self.settings_back.clicked.connect(lambda: self.stack.setCurrentWidget(self.tabs))
        self.setStyleSheet("QMainWindow{background:#16161a}")
        self._closed = False
        self.close_to_tray = None                                              # o núcleo liga (D-UX-1: fechar = bandeja)
        self.quit_app = None

    def _sources_changed(self, text: str) -> None:
        self.status_sources.setText(text)
        self.status_sources.setToolTip(text.replace("  │  ", "\n"))
        bad = any(st.status != "AO_VIVO" for st in self.review.data_states.values())
        self.status_sources.setStyleSheet(f"color:{'#ffb4b4' if bad else '#8f8f98'};font-size:11px;padding:0 6px")

    def set_runtime_pin(self, st) -> None:
        """Divergência comprovada do agent-runtime (instalado x pyproject x uv.lock): faixa vermelha na janela principal,
        selecionável, com os SHAs e o comando. "Não deu para saber" não vira alarme (fica no log e em Notificações não)."""
        bad = st is not None and st.ok is False
        self.runtime_banner.setText(f"Runtime divergente: {st.message}" if bad else "")
        self.runtime_banner.setVisible(bad)

    def _notify(self, category: str, title: str, body: str = "", session_id: str = "", dedupe_key: str | None = None,
                state: str = "NEW") -> None:
        self.center.publish(category, title, body=body, session_id=session_id, dedupe_key=dedupe_key, state=state,
                            link="chat" if category in ("AGENT", "ERROR") else ("workflows" if category == "TASK" else ""))
        self.notifications.refresh()

    def show_page(self, obj) -> None:
        """Abre o objeto onde ele mora agora (UX-07/08): uma das 4 áreas, uma seção de Notificações, Configurações, ou
        Configurações → Avançado."""
        if obj in (self.notifications, self.needs, self.permissions):
            self.stack.setCurrentWidget(self.tabs)
            self.tabs.setCurrentWidget(self.notifications_area)
            self.notifications_area.show_section(obj)
        elif self.tabs.indexOf(obj) >= 0:
            self.stack.setCurrentWidget(self.tabs)
            self.tabs.setCurrentWidget(obj)
        elif obj is self.settings:
            self.stack.setCurrentWidget(self.settings_page)
        elif self.advanced.indexOf(obj) >= 0:
            self.stack.setCurrentWidget(self.settings_page)
            self.settings.show_section("Avançado")
            self.advanced.setCurrentWidget(obj)
        else:
            raise ValueError(f"objeto sem destino na navegação: {obj!r}")
        self._page_shown(self.current_page())

    def current_page(self):
        """O objeto que o Felipe está vendo: a área atual, Configurações, ou a subpágina de Avançado."""
        if self.stack.currentWidget() is self.tabs:
            page = self.tabs.currentWidget()
            return page.sections.currentWidget() if page is self.notifications_area else page
        if self.settings.pages.currentWidget() is self.advanced:
            return self.advanced.currentWidget()
        return self.settings

    def open_link(self, link: str) -> None:
        """Leva a origem de uma notificacao: `permissions:<id>`, `chat`, `workflows`, ou um PR (`https://…`, no navegador)."""
        if link.startswith(("http://", "https://")):
            ctl.open_url(link)
            return
        kind, _, ref = link.partition(":")
        target = {"permissions": self.permissions, "chat": self.sessions, "workflows": self.workflows}.get(kind)
        if target is None:
            return
        self.show_page(target)
        if target is self.permissions and ref:
            self.permissions.select(ref)

    def design_context(self) -> str:
        """UX-13: a seleção do Design, só se o Felipe marcou "Anexar" (o texto é o do preview)."""
        review = getattr(self, "review", None)
        return review.design.attach_text() if review is not None else ""

    def _chat_context(self) -> list[tuple[str, str]]:
        """O que o Chat anexa ao próximo prompt: a Biblioteca e/ou o Design, cada um pelo texto exato do seu preview."""
        lib = self.library.context() if self.library else ""
        return [(name, text) for name, text in (("biblioteca", lib), ("Design", self.design_context())) if text]

    def _chat_context_sent(self, names: list[str]) -> None:
        """A escolha de anexar o Design vale para UM envio (revisão do #124): depois dele, desmarca."""
        if "Design" in names and getattr(self, "review", None) is not None:
            self.review.design.attach_box.setChecked(False)

    @property
    def chat(self) -> ChatView:
        """A conversa da aba de sessao atual (compatibilidade: o resto da janela fala com "o Chat")."""
        return self.sessions.current()

    def _page_shown(self, tab) -> None:
        """Primeira visita carrega (nas 4 áreas e em Avançado). Visita = a página visível MUDOU (o sinal da aba e o
        `show_page` podem chegar juntos)."""
        if tab is getattr(self, "_last_page", None):
            return
        self._last_page = tab
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
        self.show_page(self.sessions)
        if workspace:
            self.chat.prefill(workspace, text)
        else:
            self.chat.input.setPlainText(text)

    def open_review(self, workspace: str, prompt: str) -> None:
        """Revisao assistida (R21): conversa nova no checkout do repo com o prompt pronto. Enviar e com o Felipe."""
        self.show_page(self.sessions)
        self.chat.prefill(workspace, prompt)

    def shutdown(self) -> None:
        if self._closed:
            return
        if self.isVisible():                                                    # "Encerrar"/troca de versão pelo núcleo
            self.save_window()
        self._closed = True
        self.notifications.shutdown()
        self.notifications_area.shutdown()
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
        """D-UX-1: com o núcleo e a bandeja, fechar ESCONDE (agentes, sessões e coleta seguem; o ícone da bandeja e o
        atalho trazem a mesma janela de volta). Sem bandeja, fechar encerra o app pelo núcleo. Sozinha (testes,
        compatibilidade), fecha como antes. Em todos os casos a geometria é salva antes (UX-04)."""
        if not self._closed:
            self.save_window()
        if self.close_to_tray is not None and self.close_to_tray():
            e.ignore()
            self.hide()
            return
        if self.quit_app is not None:
            self.quit_app()                                                    # núcleo: Cockpit, coleta, bandeja, processo
        else:
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
