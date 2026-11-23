"""chat_sessions.py — abas de sessao no Chat (SPEC P2S-STUDIO-WORKSPACE-UX, F5): varias conversas simultaneas, cada
uma com o seu agente, num so lugar.

- "+ Nova sessão" abre outra conversa; fechar a aba encerra o agente dela (fechamento obrigatorio) e cancela, com
  motivo, so os pedidos de permissao daquela conversa;
- um Permission Inbox e um broker para todas as abas: pedido de qualquer aba aparece e e respondido nas abas
  Permissoes/Notificacoes; a mudanca chega a todas as abas;
- limite medido por provedor (DEFAULT_LIMITS do Session Manager, ajustavel por AGENT_BAR_SESSION_LIMITS): abrir alem
  dele e recusado com o motivo, antes de subir outro processo;
- vigia (1 s): o titulo da aba mostra o estado — ● trabalhando, ⏸ espera sua permissao, ○ pronta, ⚠ erro, · sem agente.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QTabWidget, QVBoxLayout, QWidget

from agent_bar.cockpit import profiles as prof
from agent_bar.cockpit.permission_broker import PermissionBroker
from agent_bar.cockpit.permission_inbox import PermissionInbox
from agent_bar.cockpit.store import data_dir
from agent_bar.cockpit.ui.chat import ChatView
from agent_bar.platform.sessions import DEFAULT_LIMIT, DEFAULT_LIMITS, limits_from_env

PROFILE_PROVIDER = {"claude": "claude-acp", "ollama": "ollama", "demo": "demo"}


class ChatSessions(QWidget):
    def __init__(self, make_view: Callable[..., ChatView] = ChatView, *, inbox: PermissionInbox | None = None,
                 limits: dict[str, int] | None = None, **view_kwargs) -> None:
        super().__init__()
        self.make_view = make_view
        self.view_kwargs = view_kwargs
        self.inbox = inbox or PermissionInbox(data_dir() / "permissions.sqlite")
        self._own_inbox = inbox is None
        self.inbox.cancel_open("o Studio foi reiniciado: o agente que pediu nao espera mais a resposta")
        self.broker = PermissionBroker(self.inbox, on_change=self._permission_changed)
        self.limits = {**DEFAULT_LIMITS, **limits_from_env(), **(limits or {})}
        self.views: list[ChatView] = []
        self.notify_fn: Callable[..., None] | None = None
        self.visible_fn: Callable[[], bool] = lambda: False
        self.permission_listeners: list[Callable[[str], None]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.new_btn = QPushButton("+ Nova sessão")
        self.new_btn.setToolTip("Outra conversa ao mesmo tempo, com o seu próprio agente")
        self.tabs.setCornerWidget(self.new_btn)
        lay.addWidget(self.tabs)
        self.new_btn.clicked.connect(lambda: self.add_session())
        self.tabs.tabCloseRequested.connect(self.close_session)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.add_session()

    # ------------------------------------------------------------------------------------------------ abas
    def current(self) -> ChatView:
        w = self.tabs.currentWidget()
        return w if isinstance(w, ChatView) else self.views[0]

    def add_session(self) -> ChatView:
        view = self.make_view(inbox=self.inbox, broker=self.broker, session_gate=self._gate, **self.view_kwargs)
        view.notify_fn = self._notify
        view.visible_fn = lambda v=view: self.visible_fn() and self.current() is v
        self.views.append(view)
        idx = self.tabs.addTab(view, self._title(view))
        self.tabs.setCurrentIndex(idx)
        return view

    def close_session(self, index: int) -> None:
        view = self.tabs.widget(index)
        if not isinstance(view, ChatView):
            return
        self.tabs.removeTab(index)
        self.views.remove(view)
        view.shutdown()                                                      # encerra o agente dela (obrigatorio)
        view.deleteLater()
        if not self.views:                                                   # sempre ha uma conversa aberta
            self.add_session()

    def live_count(self, profile_key: str, exclude: ChatView | None = None) -> int:
        return sum(1 for v in self.views if v is not exclude and v.client is not None and v.profile().key == profile_key)

    def _gate(self, profile: prof.AgentProfile) -> str | None:
        provider = PROFILE_PROVIDER.get(profile.key, profile.key)
        limit = int(self.limits.get(provider, DEFAULT_LIMIT))
        asking = next((v for v in self.views if v.client is None and v.profile().key == profile.key), None)
        if self.live_count(profile.key, exclude=asking) >= limit:
            return (f"{profile.label}: {limit} sessões abertas (limite medido para este provedor). Feche uma aba de "
                    "sessão antes de abrir outra.")
        return None

    # ------------------------------------------------------------------------------------------------ estado
    @staticmethod
    def _title(view: ChatView) -> str:
        p = view.profile()
        if view.broker is not None and any(view._perm_cards.get(eid) and view.broker.is_open(eid) for eid in view._perm_cards):
            mark = "⏸"
        elif view.busy:
            mark = "●"
        elif not view.banner.isHidden():
            mark = "⚠"
        elif view.client is not None and view.ready:
            mark = "○"
        else:
            mark = "·"
        ws = Path(view.workspace()).name if view.workspace() else "?"
        return f"{mark} {p.label} · {ws}"

    def _tick(self) -> None:
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ChatView):
                title = self._title(view)
                if self.tabs.tabText(i) != title:
                    self.tabs.setTabText(i, title)
        self.broker.expire()                                                 # um vigia para o broker compartilhado

    def _permission_changed(self, entry_id: str) -> None:
        for v in list(self.views):
            v._permission_changed(entry_id)
        for fn in list(self.permission_listeners):
            try:
                fn(entry_id)
            except Exception:                                                 # noqa: BLE001
                pass

    def _notify(self, *a, **kw) -> None:
        if self.notify_fn is not None:
            self.notify_fn(*a, **kw)

    def answer_permission(self, entry_id: str, choice: str) -> bool:
        """Da aba Permissoes/Notificacoes: responde pela conversa que mostra o cartao (ou pela atual)."""
        owner = next((v for v in self.views if entry_id in v._perm_cards), self.current())
        return owner.answer_permission(entry_id, choice)

    def shutdown(self, close_store: bool = True) -> None:
        """Encerra as conversas e responde (cancelled) o que estava aberto. `close_store=False`: quem chama ainda vai
        parar o runtime compartilhado e so depois fechar o banco (`close_store()`), para nenhum callback tardio do
        agente encontrar o banco fechado (#75)."""
        self.timer.stop()
        for v in list(self.views):
            v.shutdown()
        self.broker.cancel_all("o Studio foi fechado")
        if close_store:
            self.close_store()

    def close_store(self) -> None:
        if self._own_inbox and not self.inbox.closed:
            self.inbox.cancel_open("o Studio foi fechado")
            self.inbox.close()
