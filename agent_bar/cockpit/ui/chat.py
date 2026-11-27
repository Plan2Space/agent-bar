"""chat.py — aba Chat do cockpit (F2): streaming coalescido, ToolCard/PlanCard, permissao, historico e workspace.

Os callbacks do cliente ACP chegam na thread asyncio; aqui eles viram sinais Qt (entrega enfileirada na thread da UI).
O handler de update so acumula texto ou atualiza um cartao: nada de I/O na thread da UI (R32).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import permission_mode as PM
from agent_bar.cockpit import profiles as prof
from agent_bar.cockpit import roles as R
from agent_bar.cockpit.acp import (
    AcpClient,
    AsyncRuntime,
    PermissionRequest,
    Update,
    classify,
    find_config,
    mode_is_safe,
)
from agent_bar.cockpit.library import compose_prompt
from agent_bar.cockpit.permission_broker import PermissionBroker
from agent_bar.cockpit.permission_inbox import InboxClosed, PermissionInbox
from agent_bar.cockpit.store import Chat, ChatStore, data_dir
from agent_bar.cockpit.ui.composer import Composer, load_enter_sends, save_enter_sends
from agent_bar.cockpit.ui.widgets import (
    ExecutorOffer,
    Notice,
    PermissionCard,
    PlanCard,
    TextBlock,
    ToolCard,
    UserBubble,
)
from agent_bar.platform.usage import (
    UsageSnapshot,
    from_acp_prompt_usage,
    from_acp_usage_update,
    usage_line,
)

log = logging.getLogger("agent_bar.cockpit.chat")

FLUSH_MS = 50
AUTOSCROLL_PX = 80
def _choices(option: dict) -> list[tuple[str, str]]:
    """(valor exato, texto da UI). O "default" mostra a descricao (ex.: "Default (recommended) — Opus 5.5")."""
    items = []
    for item in option.get("options") or []:
        if isinstance(item, dict) and isinstance(item.get("options"), list):
            items.extend(x for x in item["options"] if isinstance(x, dict))
        elif isinstance(item, dict):
            items.append(item)
    out = []
    for x in items:
        if "value" not in x:
            continue
        value = str(x["value"])
        text = str(x.get("name") or value)
        if value == "default" and x.get("description"):
            text = f"{text} — {x['description']}"
        out.append((value, text))
    return out


DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


def _default_provider_client(profile, workspace: str, **kw):
    """Cliente do Chat para provedor nao-ACP. Hoje: Ollama no endpoint descoberto (OLLAMA_HOST ou o padrao)."""
    from agent_bar.cockpit.provider_client import ProviderClient
    from agent_bar.platform.discovery import discover
    from agent_bar.platform.ollama import OllamaAdapter
    if profile.adapter not in ("ollama", "ollama-executor"):
        raise ValueError(f"adaptador desconhecido: {profile.adapter}")
    endpoint = next(p["endpoint"] for p in discover(which=lambda _n: None) if p["provider_id"] == "ollama")
    if profile.adapter == "ollama-executor":                                 # P2S-LOCAL-EXECUTOR-SKETCHUP-POC
        from agent_bar.platform.sketchup_sample import executor_adapter
        return ProviderClient(executor_adapter(endpoint), **kw)
    # o modelo padrão é uma PREFERÊNCIA explícita e estável, não a ordem do /api/tags (o 1º era um modelo de 9 GB com
    # thinking, que com a GPU ocupada passava minutos sem emitir nada); configurável por AGENT_BAR_OLLAMA_MODEL
    preferred = os.environ.get("AGENT_BAR_OLLAMA_MODEL", "").strip() or DEFAULT_OLLAMA_MODEL
    return ProviderClient(OllamaAdapter(endpoint, timeout=30, preferred_model=preferred), **kw)


def _confirm_irreversible_dialog(req: PermissionRequest, parent: QWidget) -> bool:
    from agent_bar.cockpit.devmode import irreversible_request
    from agent_bar.cockpit.ui.widgets import IrreversibleDialog
    gate = IrreversibleDialog(req, irreversible_request(req.tool_call or {}) or "operação irreversível", parent)
    gate.exec()
    return bool(gate.confirmed)


RISKY_SUFFIX = " (decide sem pedir permissão)"


def confirm_risky_mode(name: str, parent: QWidget) -> bool:
    """Modo que decide sozinho so com escolha explicita; o padrao do dialogo e "nao"."""
    box = QMessageBox(QMessageBox.Icon.Warning, "Modo sem pedir permissão",
                      f"No modo \"{name}\" o agente decide sozinho e não pede sua permissão antes de agir "
                      "(editar arquivos, rodar comandos).\n\nUsar este modo nesta conversa?",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes

COMBO_CSS = "QComboBox{background:#26262c;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:2px 6px}"
BTN_CSS = ("QPushButton{background:#2a2a30;color:#e6e6e6;border:1px solid #3a3a44;border-radius:5px;padding:4px 10px}"
           "QPushButton:hover{background:#34343c}QPushButton:disabled{color:#666}")


class FitCombo(QComboBox):
    """Combo que não alarga a página (UX-16 run 6): a largura mínima vem de `chars` caracteres, não do item mais
    comprido. O texto inteiro do item atual fica na dica (`full_tip`) e a lista aberta mostra os itens inteiros."""

    def __init__(self, chars: int = 10, full_tip: bool = False) -> None:
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(chars)
        self._hint = ""
        if full_tip:
            self.currentIndexChanged.connect(lambda _i: self._full_tip())

    def setToolTip(self, text: str) -> None:                                   # noqa: N802
        self._hint = text
        super().setToolTip(text)

    def _full_tip(self) -> None:
        text = self.currentText()
        data = self.currentData()
        if isinstance(data, str) and data and data != text:
            text = f"{text}\n{data}"
        super().setToolTip(f"{text}\n\n{self._hint}" if self._hint else text)

    def showPopup(self) -> None:                                                # noqa: N802
        view = self.view()
        view.setMinimumWidth(min(view.sizeHintForColumn(0) + 24, 900))
        super().showPopup()


class _Bridge(QObject):
    update = Signal(int, object)
    permission = Signal(int, object, object)
    done = Signal(int, str, object, object)          # geracao, etiqueta, resultado, excecao
    exited = Signal(int, object)


class ChatView(QWidget):
    def __init__(self, store: ChatStore | None = None, runtime: AsyncRuntime | None = None, *,
                 client_factory: Callable[..., AcpClient] = AcpClient,
                 ask_permission: Callable[[PermissionRequest, QWidget], str | None] | None = None,
                 confirm_irreversible: Callable[[PermissionRequest, QWidget], bool] | None = None,
                 confirm_mode: Callable[[str, QWidget], bool] = confirm_risky_mode,
                 profiles_fn: Callable[[], list[prof.AgentProfile]] = prof.profiles,
                 workspaces_fn: Callable[[], list[Path]] = prof.workspaces,
                 context_provider: Callable[[], str] | None = None,
                 provider_factory: Callable[..., object] | None = None,
                 inbox: PermissionInbox | None = None, broker: PermissionBroker | None = None,
                 session_gate: Callable[[prof.AgentProfile], str | None] | None = None,
                 permission_mode_fn: Callable[[], str] | None = None) -> None:
        super().__init__()
        self.permission_mode_fn = permission_mode_fn or PM.current           # Felipe: bypass sempre (reversível)
        self.context_provider = context_provider                          # F4: biblioteca (texto exato do preview)
        self.store = store or ChatStore()
        self.rt = runtime or AsyncRuntime("cockpit-chat")
        self._own_rt = runtime is None
        self.client_factory = client_factory
        self.provider_factory = provider_factory or _default_provider_client
        self.inbox = inbox if inbox is not None else PermissionInbox(data_dir() / "permissions.sqlite")
        self._own_inbox = inbox is None
        self._own_broker = broker is None                                    # abas de sessao compartilham um broker
        self.broker = broker if broker is not None else PermissionBroker(self.inbox, on_change=self._permission_changed)
        self.session_gate = session_gate                                     # limite de sessoes do provedor (F5)
        self.confirm_irreversible = confirm_irreversible or _confirm_irreversible_dialog
        self._perm_cards: dict[str, PermissionCard] = {}
        self.permission_listeners: list[Callable[[str], None]] = []          # abas que mostram o mesmo pedido
        self._expire_timer = QTimer(self)
        self._expire_timer.timeout.connect(self.broker.expire)               # vencido = recusa ao agente, nunca pendurado
        if self._own_broker:                                                 # broker compartilhado: o container vigia
            self._expire_timer.start(2000)
        self.inbox.cancel_open("o Studio foi reiniciado: o agente que pediu nao espera mais a resposta")
        self.ask_permission = ask_permission
        self.confirm_mode = confirm_mode
        self.no_safe_mode = False                                            # agente sem modo que pede permissao: envio bloqueado
        self._modes: dict = {}
        self._draft = ""
        self._last_turn_text: list[str] = []
        self.notify_fn: Callable[..., None] | None = None                    # F3: a janela liga ao Notification Center
        self.visible_fn: Callable[[], bool] = lambda: False
        self._config: list[dict] = []
        self._config_pending = False
        self.client: AcpClient | None = None
        self._closing: list = []                                             # close() de clientes descartados
        self.gen = 0
        self.ready = False
        self.busy = False
        self.pending_prompt: str | None = None
        self.chat: Chat = self.store.new()
        self.cards: dict[str, ToolCard] = {}
        self.tools_observed: set[str] = set()          # ferramentas que ESTA sessão usou (UX-06: Executor pronto)
        self.plan_card: PlanCard | None = None
        self.text_block: TextBlock | None = None
        self.thought_block: TextBlock | None = None
        self._buf: list[str] = []
        self._tbuf: list[str] = []
        self.flush_count = 0
        self._turn_text: list[str] = []
        self._turn_thought: list[str] = []
        self.bridge = _Bridge()
        # SEMPRE pela fila de eventos, na ordem em que foram emitidos: se o agente responde rapido, o future ja esta pronto
        # quando o add_done_callback e anexado e o callback roda na propria thread da UI; com AutoConnection o "done"
        # virava chamada direta e passava na frente dos chunks ainda na fila (turno terminava sem o texto).
        queued = Qt.ConnectionType.QueuedConnection
        self.bridge.update.connect(self._on_update, queued)
        self.bridge.permission.connect(self._on_permission, queued)
        self.bridge.done.connect(self._on_done, queued)
        self.bridge.exited.connect(self._on_exit, queued)
        self._wait_timer = QTimer(self)                                       # bug do Felipe: nunca tela parada sem sinal
        self._wait_timer.setInterval(1000)
        self._wait_timer.timeout.connect(self._tick)
        self._turn_t0 = 0.0
        self._phase = ""
        self.flush_timer = QTimer(self)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.setInterval(FLUSH_MS)
        self.flush_timer.timeout.connect(self.flush)
        self.profiles = profiles_fn()
        self._build(workspaces_fn())
        self._refresh_history()

    # ------------------------------------------------------------------------------------------------- layout
    def _build(self, spaces: list[Path]) -> None:
        self.setStyleSheet("background:#16161a")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.agent_box = FitCombo(14, full_tip=True)
        for p in self.profiles:
            ok, why = p.available()
            self.agent_box.addItem(R.label(p) if ok else f"{R.role_of(p)} · {p.label} — indisponível: {why}", p.key)
            if not ok:
                self.agent_box.model().item(self.agent_box.count() - 1).setEnabled(False)
        first_ok = next((i for i, p in enumerate(self.profiles) if p.available()[0]), 0)
        self.agent_box.setCurrentIndex(first_ok)
        self.workspace_box = FitCombo(10, full_tip=True)
        for s in spaces:
            self.workspace_box.addItem(s.name, str(s))
        self.workspace_box.addItem("Escolher pasta…", "")
        self.mode_box = FitCombo(6)
        self.mode_box.setToolTip("Modos anunciados pelo agente. O padrão pede permissão.")
        self.model_box, self.effort_box = FitCombo(10), FitCombo(6)
        self.model_box.setToolTip("Modelos anunciados pelo agente. Vale o que o agente confirmar.")
        self.effort_box.setToolTip("Esforços que o agente anuncia para ESTE modelo. Vale o que o agente confirmar.")
        self.confirmed = QLabel("")
        self.confirmed.setStyleSheet("color:#7ee2a8;font-size:11px")
        self.history_box = FitCombo(10)
        self.history_box.setToolTip("Conversas salvas")
        self.new_btn = QPushButton("Nova conversa")
        for w in (self.agent_box, self.workspace_box, self.mode_box, self.history_box, self.model_box, self.effort_box):
            w.setStyleSheet(COMBO_CSS)
        self.new_btn.setStyleSheet(BTN_CSS)
        for label, w, stretch in (("Agente", self.agent_box, 2), ("Workspace", self.workspace_box, 2),
                                  ("Modo", self.mode_box, 1), ("Histórico", self.history_box, 2)):
            cap = QLabel(label)
            cap.setStyleSheet("color:#8f8f98;font-size:11px")
            head.addWidget(cap)
            head.addWidget(w, stretch)
            if w is self.mode_box:
                self.mode_label = cap
        head.addWidget(self.new_btn)
        lay.addLayout(head)
        cfg = QHBoxLayout()                                                   # modelo e esforco: so o que o agente anuncia
        self.model_label, self.effort_label = QLabel("Modelo"), QLabel("Esforço")
        for cap, w, stretch in ((self.model_label, self.model_box, 2), (self.effort_label, self.effort_box, 1)):
            cap.setStyleSheet("color:#8f8f98;font-size:11px")
            cfg.addWidget(cap)
            cfg.addWidget(w, stretch)
        cfg.addWidget(self.confirmed, 3)
        lay.addLayout(cfg)
        self.usage: UsageSnapshot | None = None                                # F6: o que o provedor informou, com estado
        self.usage_label = QLabel(usage_line(None))
        self.usage_label.setAccessibleName("Uso de contexto e tokens")
        self.usage_label.setToolTip("Medido = informado pelo provedor · estimado = calculado, não cobrado · "
                                    "desatualizado = deixou de valer. Nada aparece como zero quando não foi informado.")
        self.usage_label.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.usage_label)
        self.banner = QFrame()
        self.banner.setStyleSheet("QFrame{background:#3a1717;border:1px solid #7a2a2a;border-radius:6px}")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(8, 4, 8, 4)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.banner_label.setStyleSheet("color:#ffd0d0;font-size:12px;border:0")
        self.reconnect_btn = QPushButton("Reconectar")
        self.reconnect_btn.setStyleSheet(BTN_CSS)
        bl.addWidget(self.banner_label, 1)
        bl.addWidget(self.reconnect_btn)
        self.banner.setVisible(False)
        lay.addWidget(self.banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript = QWidget()
        self.tlay = QVBoxLayout(self.transcript)
        self.tlay.setContentsMargins(4, 4, 4, 4)
        self.tlay.setSpacing(6)
        self.tlay.addStretch(1)
        self.scroll.setWidget(self.transcript)
        lay.addWidget(self.scroll, 1)
        self.empty = Notice("Converse com um agente de código no workspace escolhido. Enter envia, Shift+Enter quebra linha. "
                            "Toda ferramenta que pedir permissão abre um diálogo; fechar o diálogo recusa.")
        self._add(self.empty)
        bottom = QHBoxLayout()
        self.prefs_path = data_dir() / "preferences.json"
        self.input = Composer(enter_sends=load_enter_sends(self.prefs_path))
        self.input.setFixedHeight(84)
        self.input.setStyleSheet("QPlainTextEdit{background:#1e1e23;color:#e6e6e6;border:1px solid #3a3a44;"
                                 "border-radius:6px;font-size:13px}")
        self.send_btn = QPushButton("Enviar")
        self.send_btn.setStyleSheet(BTN_CSS)
        self.enter_box = QCheckBox("Enter envia")
        self.enter_box.setToolTip("Desligado: Enter quebra linha e Ctrl+Enter envia")
        self.enter_box.setChecked(self.input.enter_sends)
        self.enter_box.setStyleSheet("color:#8f8f98;font-size:11px")
        side = QVBoxLayout()
        side.addWidget(self.send_btn)
        side.addWidget(self.enter_box)
        bottom.addWidget(self.input, 1)
        bottom.addLayout(side)
        lay.addLayout(bottom)
        self.status = QLabel("desconectado")
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        self.permission_label = QLabel(PM.label(self.permission_mode_fn()))  # o Felipe sabe em que modo está
        self.permission_label.setStyleSheet("color:#8f8f98;font-size:11px")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.permission_label)
        lay.addLayout(status_row)
        self.send_btn.clicked.connect(self._send_or_stop)
        self.input.submit.connect(self._submit_from_keyboard)                 # Enter / Ctrl+Enter (F2 da UX v1)
        self.enter_box.toggled.connect(self._enter_pref_changed)
        self.new_btn.clicked.connect(self.new_chat)
        self.reconnect_btn.clicked.connect(self.reconnect)
        self.agent_box.currentIndexChanged.connect(lambda _i: self._restart("agente trocado"))
        self.workspace_box.activated.connect(self._workspace_activated)
        self.mode_box.activated.connect(self._mode_activated)
        self.model_box.activated.connect(lambda _i: self._config_activated("model", self.model_box))
        self.effort_box.activated.connect(lambda _i: self._config_activated("thought_level", self.effort_box))
        self.history_box.activated.connect(self._history_activated)
        self.chat.workspace = self.workspace()
        self.chat.agent = self.profile().key

    # ------------------------------------------------------------------------------------------------ helpers
    def profile(self) -> prof.AgentProfile:
        key = self.agent_box.currentData()
        return next((p for p in self.profiles if p.key == key), self.profiles[0])

    def workspace(self) -> str:
        return str(self.workspace_box.currentData() or "")

    def _near_bottom(self) -> bool:
        sb = self.scroll.verticalScrollBar()
        return sb.maximum() - sb.value() <= AUTOSCROLL_PX

    def _add(self, w: QWidget) -> None:
        stick = self._near_bottom()
        self.tlay.insertWidget(self.tlay.count() - 1, w)
        if stick:
            QTimer.singleShot(0, self.scroll, self._scroll_to_end)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if not busy and self._phase:                                         # falhou antes do turno (ex.: conexão)
            self._phase = ""
            self._wait_timer.stop()
        self.send_btn.setText("Parar" if busy else "Enviar")
        self._enable_config()

    # ---------------------------------------------------------------------------------------- modelo e esforco
    CONFIG_BOXES = (("model", "model_box", "model_label"), ("thought_level", "effort_box", "effort_label"))

    def _populate_config(self, options: list) -> None:
        """Preenche os seletores com as opcoes ANUNCIADAS e seleciona o valor que o agente diz estar valendo."""
        self._config = [o for o in options if isinstance(o, dict)]
        for cid, box_name, label_name in self.CONFIG_BOXES:
            box, label = getattr(self, box_name), getattr(self, label_name)
            opt = find_config(self._config, cid)
            box.blockSignals(True)
            box.clear()
            if opt is not None:
                for value, text in _choices(opt):
                    box.addItem(text, value)
                idx = box.findData(str(opt.get("currentValue", "")))
                if idx >= 0:
                    box.setCurrentIndex(idx)
            box.blockSignals(False)
            box.setVisible(opt is not None)
            label.setVisible(opt is not None)
        self._enable_config()
        self.confirmed.setText(self._confirmed_text())

    def _confirmed_text(self) -> str:
        parts = []
        for cid, title in (("model", "modelo"), ("thought_level", "esforço")):
            opt = find_config(self._config, cid)
            if opt is not None:
                current = str(opt.get("currentValue", ""))
                parts.append(f"{title} {dict(_choices(opt)).get(current, current)}")
        return ("confirmado pelo agente: " + " · ".join(parts)) if parts else ""

    def _enable_config(self) -> None:
        on = self.ready and not self.busy and self.client is not None and not self._config_pending
        for _cid, box_name, _label in self.CONFIG_BOXES:
            box = getattr(self, box_name)
            box.setEnabled(on and box.count() > 0)

    def _config_activated(self, cid: str, box: QComboBox) -> None:
        opt = find_config(self._config, cid)
        value = box.currentData()
        if self.client is None or not self.ready or opt is None or value is None or value == opt.get("currentValue"):
            return
        self._config_pending = True
        self._enable_config()
        self.confirmed.setText(f"aguardando confirmação do agente: {opt.get('name', cid)} = {box.currentText()}…")
        gen = self.gen
        self.client.set_config_option(str(opt["id"]), str(value)).add_done_callback(
            lambda f, g=gen: self._emit_done(g, f"config:{cid}", f))

    def _show_error(self, exc: BaseException | None, text: str = "") -> None:
        from agent_bar.platform.adapter import AdapterError
        kind = classify(exc) if exc is not None else "error"
        if isinstance(exc, AdapterError):                                    # Ollama e outros provedores da plataforma
            kind = "provedor"
        hint = {"provedor": "o provedor local falhou","not_found": "o executável do agente não está no PATH", "auth": "o agente pede login na CLI dele",
                "timeout": "o agente não respondeu a tempo", "acp_down": "o processo do agente terminou"}.get(kind, "falha")
        detail = text or (str(exc) if exc else "")
        self.banner_label.setText(f"[{kind}] {hint}. {detail}"[:400])
        self.banner.setVisible(True)
        self.status.setText(f"erro: {kind}")

    # ---------------------------------------------------------------------------------------------- conexao
    def ensure_client(self) -> None:
        if self.client is not None:
            return
        ws = self.workspace()
        if not ws:
            self._show_error(None, "escolha um workspace")
            return
        refusal = self.session_gate(self.profile()) if self.session_gate is not None else None
        if refusal:                                                          # limite medido do provedor (F5)
            self._set_busy(False)
            self.pending_prompt = None
            self._restore_draft()
            self._show_error(None, refusal)
            return
        self.gen += 1
        gen = self.gen
        self.tools_observed = set()                                          # cliente novo: nada observado
        self._set_usage(None)                                                  # cliente novo: nada medido ainda
        self.no_safe_mode = False
        self._populate_config([])                                            # nada do agente anterior fica nos seletores
        self.ready = False
        p = self.profile()
        self.banner.setVisible(False)
        self.status.setText(f"conectando a {p.label} em {Path(ws).name}…")
        if p.adapter != "acp":                                               # provedor da plataforma (F1/F2): mesmo Chat
            self.client = self.provider_factory(
                p, ws, on_update=lambda u, g=gen: self.bridge.update.emit(g, u),
                on_permission=lambda req, respond, g=gen: self.bridge.permission.emit(g, req, respond),
                on_exit=lambda code, g=gen: self.bridge.exited.emit(g, code))
            fut = self.client.connect(None)
            fut.add_done_callback(lambda f, g=gen: self._emit_done(g, "connect", f))
            return
        self.client = self.client_factory(
            list(p.argv), ws, runtime=self.rt,
            on_update=lambda u, g=gen: self.bridge.update.emit(g, u),
            on_permission=lambda req, respond, g=gen: self.bridge.permission.emit(g, req, respond),
            on_exit=lambda code, g=gen: self.bridge.exited.emit(g, code),
            stderr_path=data_dir() / "logs" / f"agent-{p.key}.stderr.log")
        # Bypass NÃO usa o bypassPermissions do agente: nele o Claude não manda session/request_permission e nada seria
        # registrado. A sessão fica sempre no modo que pede; quem aprova sozinho, e registra, é o nosso broker.
        fut = self.client.connect(self.chat.session_id or None)
        fut.add_done_callback(lambda f, g=gen: self._emit_done(g, "connect", f))

    def _emit_done(self, gen: int, tag: str, fut) -> None:
        exc = fut.exception()
        self.bridge.done.emit(gen, tag, None if exc else fut.result(), exc)

    def _drop_client(self) -> None:
        self._config_pending = False
        if self.client is not None:
            self._closing = [f for f in self._closing if not f.done()]
            self._closing.append(self.client.close())
        self.client = None
        self.ready = False
        self.gen += 1                                                      # sinais do cliente antigo passam a ser ignorados

    def _restart(self, why: str) -> None:
        self.flush()
        had = self.client is not None
        self._drop_client()
        if self.busy:
            self._set_busy(False)
        self.chat.workspace = self.workspace()
        self.chat.agent = self.profile().key
        self.chat.session_id = ""
        if had or why:
            self._add(Notice(f"— {why}: nova conexão com {self.profile().label} em {Path(self.workspace()).name or '?'} "
                             "(histórico visual mantido) —"))
        self.ensure_client()

    def reconnect(self) -> None:
        self._drop_client()
        self.ensure_client()

    def _workspace_activated(self, idx: int) -> None:
        if not self.workspace_box.itemData(idx):
            folder = QFileDialog.getExistingDirectory(self, "Workspace do agente")
            if not folder:
                return
            self.workspace_box.insertItem(idx, Path(folder).name, str(Path(folder).resolve()))
            self.workspace_box.setCurrentIndex(idx)
        if self.workspace() != self.chat.workspace:
            self._restart("workspace trocado")

    def _mode_activated(self, _idx: int) -> None:
        mode = str(self.mode_box.currentData() or "")
        if self.client is None or not self.ready or not mode or mode == self.chat.mode:
            return
        if not mode_is_safe(mode, self._modes):
            name = self.mode_box.currentText().replace("⚠ ", "").replace(RISKY_SUFFIX, "")
            if not self.confirm_mode(name, self):
                self._select_mode(self.chat.mode)                            # recusou: volta ao modo confirmado pelo agente
                return
        gen = self.gen
        self.client.set_mode(mode).add_done_callback(lambda f, g=gen, m=mode: self._emit_done(g, f"mode:{m}", f))

    def _show_permission_mode(self) -> None:
        """Mostra o modo de permissão do Studio. Em bypass a sessão do agente continua no modo que pede (o broker
        responde allow e registra cada pedido); nada a trocar no agente."""
        self.permission_label.setText(PM.label(self.permission_mode_fn()))

    def _select_mode(self, mode: str) -> None:
        idx = self.mode_box.findData(mode)
        if idx >= 0:
            self.mode_box.setCurrentIndex(idx)

    def _populate_modes(self, modes: dict) -> None:
        """Mostra o modo que o AGENTE confirmou (o cliente ja trocou para o seguro no connect); nunca um modo so na tela."""
        self._modes = dict(modes)
        self.mode_box.clear()
        current = str(modes.get("currentModeId", ""))
        for m in modes.get("availableModes") or []:
            mid = str(m.get("id", ""))
            risky = not mode_is_safe(mid, modes)
            self.mode_box.addItem(("⚠ " if risky else "") + str(m.get("name", mid)) + (RISKY_SUFFIX if risky else ""), mid)
        self._select_mode(current)
        self.chat.mode = current
        has_modes = self.mode_box.count() > 0                                # provedor sem modos de permissao: nada a mostrar
        self.mode_box.setVisible(has_modes)
        self.mode_label.setVisible(has_modes)

    # ---------------------------------------------------------------------------------------------- envio
    def _send_or_stop(self) -> None:
        if self.busy:
            self.stop()
        else:
            self.send(self.input.toPlainText())

    def send(self, text: str) -> None:
        text = text.strip()
        if not text or self.busy:
            return
        if self.no_safe_mode:
            self._add(Notice("envio bloqueado: o agente não anuncia um modo que peça permissão", "#ff6b6b"))
            return
        if self._refuse_execution_in_conversation(text):
            return
        self._draft = text                                                   # volta para a caixa se o envio falhar
        self.input.clear()
        self.empty.setVisible(False)
        self._add(UserBubble(text))
        context = self.context_provider() if self.context_provider else ""
        if context:
            self._add(Notice(f"biblioteca anexada ao prompt: {len(context)} caracteres (texto exato na aba Biblioteca)", "#4aa8ff"))
            self.chat.add("user", text, context_chars=len(context))
        else:
            self.chat.add("user", text)
        full = compose_prompt(context, text)
        self.store.save(self.chat)
        self._refresh_history()
        self._set_busy(True)
        self._start_wait()                                                   # sinal desde o envio, antes do 1º token
        self.text_block = self.thought_block = None
        self._turn_text, self._turn_thought = [], []
        if self.client is None or not self.ready:
            self.pending_prompt = full
            self.ensure_client()
            return
        self._prompt(full)

    def _refuse_execution_in_conversation(self, text: str) -> bool:
        """UX-11: pedido de execução (SketchUp, build, render) numa Conversa não vai ao modelo como se ele pudesse. A
        recusa diz o motivo e oferece os Executores disponíveis; "Enviar só como conversa" é escolha explícita."""
        p = self.profile()
        if getattr(self, "_as_conversation", False) or R.role_of(p) != R.CONVERSA or not R.wants_execution(text):
            return False
        executors = [q for q in self.profiles if R.role_of(q) in (R.EXECUTOR_CLAUDE, R.EXECUTOR_LOCAL) and q.available()[0]]
        self.input.setPlainText(text)                                        # o texto volta para a caixa
        choices = [(f"Usar {R.role_of(q)}", lambda k=q.key: self._use_agent(k)) for q in executors]
        choices.append(("Enviar só como conversa", lambda t=text: self._send_as_conversation(t)))
        self._add(ExecutorOffer(R.refusal(p, executors), choices))
        return True

    def _use_agent(self, key: str) -> None:
        text = self.input.toPlainText()
        i = self.agent_box.findData(key)
        if i >= 0:
            self.agent_box.setCurrentIndex(i)                                # troca o agente; o envio continua manual
        self.input.setPlainText(text)

    def _send_as_conversation(self, text: str) -> None:
        self._as_conversation = True
        try:
            self.send(text)
        finally:
            self._as_conversation = False

    def _prompt(self, text: str) -> None:
        assert self.client is not None
        gen = self.gen
        self._tick()
        self.client.prompt(text).add_done_callback(lambda f, g=gen: self._emit_done(g, "prompt", f))

    # ------------------------------------------------------------------------------- estado vivo do turno
    def _model_in_use(self) -> str:
        opt = find_config(getattr(self, "_config", []), "model")
        return str((opt or {}).get("currentValue") or "")

    def _start_wait(self) -> None:
        self._turn_t0 = time.monotonic()
        self._phase = "wait"
        self._wait_timer.start()
        self._tick()

    PHASES = {"wait": None, "think": "pensando…", "answer": "respondendo…", "tool": "usando ferramenta…"}

    def _tick(self) -> None:
        """Fase e tempo desde o envio, a cada segundo: carregando/aguardando → pensando → respondendo."""
        if not self._phase:
            return
        p = self.profile()
        wait = "carregando o modelo / aguardando a 1ª resposta…" if p.adapter != "acp" else "aguardando o agente…"
        if not self.ready:
            wait = "conectando…"
        phase = self.PHASES.get(self._phase) or wait
        model = self._model_in_use()
        n = int(time.monotonic() - self._turn_t0)
        self.status.setText(f"{p.label}" + (f" · {model}" if model else "") + f" · {phase} {n} s")

    def _stop_wait(self) -> None:
        if not self._phase:
            return
        n = int(time.monotonic() - self._turn_t0)
        self._phase = ""
        self._wait_timer.stop()
        model = self._model_in_use()
        self.status.setText(f"{self.profile().label}" + (f" · {model}" if model else "") + f" · turno em {n} s")
        fn = getattr(self.client, "cpu_share", None)
        if callable(fn) and model:                                           # /api/ps: local e barato, fora da UI
            gen = self.gen

            def check() -> None:
                try:
                    share = fn(model)
                except Exception:                                            # noqa: BLE001 - aviso é opcional
                    share = None
                self.bridge.done.emit(gen, "cpu", (model, share), None)
            threading.Thread(target=check, name="ollama-ps", daemon=True).start()

    def stop(self) -> None:
        for eid in list(self._perm_cards):                                   # nenhum cartão fica esperando um clique
            if self.broker.is_open(eid):
                self.broker.cancel(eid, "turno parado pelo Felipe")
        if self.client is not None:
            self.client.cancel()
        self.status.setText("cancelando…")

    # ------------------------------------------------------------------------------------- sinais (thread UI)
    def _on_done(self, gen: int, tag: str, result: object, exc: object) -> None:
        if gen != self.gen:
            return
        if tag == "cpu":
            model, share = result if isinstance(result, tuple) else ("", None)
            if isinstance(share, float) and share > 0:
                self._add(Notice(f"{model} está {round(share * 100)}% na CPU (a GPU pode estar ocupada por outro programa): "
                                 "as respostas ficam lentas.", "#ffc233"))
            return
        if tag == "connect":
            if exc is not None:
                self.client = None
                self._set_busy(False)
                self.pending_prompt = None
                self._restore_draft()
                self._show_error(exc)  # type: ignore[arg-type]
                return
            info = result if isinstance(result, dict) else {}
            self.ready = True
            if self.chat.session_id and not info.get("loaded"):                # R16: agente sem session/load
                self._add(Notice("— o agente não recarrega sessões: sessão nova, histórico anterior só visual —", "#ffc233"))
            self.chat.session_id = str(info.get("session_id") or "")
            self._populate_modes(info.get("modes") or {})
            self._populate_config(info.get("config_options") or [])
            if info.get("mode_forced_from"):
                self._add(Notice(f"o agente abriu no modo \"{info['mode_forced_from']}\", que decide sem pedir permissão; "
                                 "o Studio trocou para o modo que pede permissão", "#ffc233"))
            if info.get("safe_mode") is False:
                self.no_safe_mode = True
                self.pending_prompt = None
                self._set_busy(False)
                self._add(Notice("este agente não anuncia um modo que peça permissão: envio bloqueado", "#ff6b6b"))
                self.status.setText("bloqueado: agente sem modo que peça permissão")
                return
            self.status.setText(f"conectado · {self.profile().label} · sessão {self.chat.session_id}")
            self._show_permission_mode()
            if self.pending_prompt:
                text, self.pending_prompt = self.pending_prompt, None
                self._prompt(text)
            return
        if tag.startswith("config:"):
            self._config_pending = False
            if exc is not None:                                               # volta ao que o agente confirmou antes
                self._populate_config(self.client.config_options if self.client else [])
                self._show_error(exc)  # type: ignore[arg-type]
            else:
                self._populate_config(result if isinstance(result, list) else [])
                if self.usage is not None:
                    self._set_usage(self.usage.stale("configuração trocada: aguardando nova medição"))
                if (getattr(self.client, "config_opens_new_session", False) and self.chat.messages
                        and getattr(self, "_new_session_notice_at", -1) != len(self.chat.messages)):
                    self._new_session_notice_at = len(self.chat.messages)       # um aviso entre dois turnos, nao um por seletor
                    self._add(Notice("— o provedor abriu uma sessão nova com essa configuração: as mensagens anteriores "
                                     "ficam na tela, mas o modelo não as recebe —", "#ffc233"))
            return
        if tag.startswith("mode:"):
            if exc is not None:
                self._select_mode(self.chat.mode)
                self._show_error(exc)  # type: ignore[arg-type]
            else:
                self.chat.mode = tag.split(":", 1)[1]
                self._select_mode(self.chat.mode)
            return
        if tag == "prompt":
            self.flush()
            self._finish_turn(None if exc else str(result), exc)  # type: ignore[arg-type]

    # ---------------------------------------------------------------------------------------------- permissoes
    def answer_permission(self, entry_id: str, choice: str) -> bool:
        """Resposta de qualquer superficie (cartao, aba Permissoes, Notificacoes). Irreversivel so depois do codigo."""
        req = self.broker.request(entry_id)
        if req is None:
            return False
        entry = self.inbox.get(entry_id)
        allowing = choice != "deny" and not str(next((o.get("kind") for o in req.options
                                                      if str(o.get("optionId")) == choice), choice)).startswith("reject")
        if allowing and entry.policy == "IRREVERSIBLE" and not self.confirm_irreversible(req, self):
            return False                                                     # sem o codigo: continua aberto
        try:
            self.broker.answer(entry_id, choice, by="human")
        except Exception as exc:                                             # noqa: BLE001 - mostra e segue
            self._show_error(None, f"permissão não registrada: {exc}")
            return False
        return True

    def _permission_changed(self, entry_id: str) -> None:
        card = self._perm_cards.get(entry_id)
        try:
            entry = self.inbox.get(entry_id)
        except KeyError:
            return
        if card is not None and entry.status != "PENDING":
            text = {"ALLOWED": f"permitido ({entry.decision}) por {entry.decided_by}",
                    "DENIED": f"negado por {entry.decided_by}",
                    "EXPIRED": "sem resposta no prazo: recusado ao agente",
                    "CANCELLED": f"cancelado: {entry.result or ''}"}.get(entry.status, entry.status)
            card.set_resolved(text, entry.status == "ALLOWED")
            self.status.setText(f"permissão “{entry.title}”: {text}")
        for fn in list(self.permission_listeners):
            try:
                fn(entry_id)
            except Exception:                                                 # noqa: BLE001
                log.exception("ouvinte de permissao falhou")

    def _scroll_to_end(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _submit_from_keyboard(self) -> None:
        """Enter nunca para o agente: com turno em curso, o texto fica na caixa como rascunho (so o botao Parar para)."""
        if self.busy:
            self.status.setText("agente trabalhando… o rascunho fica na caixa (Parar interrompe o turno)")
            return
        self.send(self.input.toPlainText())

    def _restore_draft(self) -> None:
        if self._draft and not self.input.toPlainText().strip():
            self.input.setPlainText(self._draft)
            cursor = self.input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input.setTextCursor(cursor)

    def _enter_pref_changed(self, on: bool) -> None:
        self.input.set_enter_sends(on)
        try:
            save_enter_sends(self.prefs_path, on)
        except OSError:
            log.warning("preferencia do Enter nao foi salva")

    def _finish_turn(self, stop_reason: str | None, exc: BaseException | None) -> None:
        self._stop_wait()
        if self._turn_thought:
            self.chat.add("thought", "".join(self._turn_thought))
        self._last_turn_text = list(self._turn_text)
        if self._turn_text:
            self.chat.add("agent", "".join(self._turn_text))
        for card in self.cards.values():
            if not card.property("saved"):
                self.chat.add("tool", **card.summary())
                card.setProperty("saved", True)
        if self.plan_card is not None:
            self.chat.add("plan", entries=self.plan_card.entries)
        if exc is not None:
            self._show_error(exc)
            self.chat.add("notice", f"erro: {classify(exc)}")
            if not self._turn_text:                                          # o agente nao respondeu nada: rascunho de volta
                self._restore_draft()
        elif stop_reason and stop_reason != "end_turn":
            self._add(Notice(f"— turno terminou: {stop_reason} —", "#ffc233"))
            self.chat.add("notice", f"turno terminou: {stop_reason}")
        self.plan_card = None
        self.cards = {}
        self.store.save(self.chat)
        self._refresh_history()
        self._set_busy(False)
        if exc is None:
            self.status.setText(f"pronto · {stop_reason}")
        self._notify_turn(stop_reason, exc)

    def _notify_turn(self, stop_reason: str | None, exc: BaseException | None) -> None:
        """Erro sempre vira notificacao; fim de turno so quando o Felipe nao esta olhando o Chat."""
        if self.notify_fn is None:
            return
        who = self.profile().label
        try:
            if exc is not None:
                self.notify_fn("ERROR", f"{who}: o turno falhou", f"{classify(exc)}: {str(exc)[:200]}", self.chat.session_id)
            elif not self.visible_fn():
                preview = "".join(self._last_turn_text)[:160]
                self.notify_fn("AGENT", f"{who} terminou ({stop_reason})", preview, self.chat.session_id)
        except Exception:                                                     # noqa: BLE001 - notificacao nao derruba o Chat
            log.exception("notificacao do turno falhou")

    def _on_exit(self, gen: int, code: object) -> None:
        if gen != self.gen:
            return
        self.client = None
        self.ready = False
        if self.busy:
            self._set_busy(False)
        self.banner_label.setText(f"[acp_down] o processo do agente terminou (código {code}).")
        self.banner.setVisible(True)

    def _on_permission(self, gen: int, req: PermissionRequest, respond) -> None:
        if gen != self.gen:
            respond(None)
            return
        self.flush()
        try:
            entry = self.inbox.record(req.tool_call or {}, provider=self.profile().key, session_id=req.session_id,
                                      workspace=self.workspace(),
                                      mode_decides_alone=bool(self._modes.get("availableModes"))
                                      and not mode_is_safe(self.chat.mode, self._modes))
        except InboxClosed:                                                  # #75: Studio fechando; o agente nunca fica pendurado
            respond(None)
            return
        if entry.repeat_note:                                                # explica pedido repetido antes do dialogo
            self._add(Notice(f"pedido repetido: {entry.repeat_note}", "#ffc233"))
        if entry.status != "PENDING":                                        # politica ja decidiu (proibido ou regra)
            choice = None if entry.status == "DENIED" else req.strongest_once()
            respond(choice)
            self._add(Notice(f"permissão para “{entry.title}”: {entry.status.lower()} por {entry.decided_by}",
                             "#3ddc84" if choice else "#ff9a9b"))
            return
        mode = self.permission_mode_fn()
        self.permission_label.setText(PM.label(mode))
        if mode == PM.BYPASS:                                               # Felipe: bypass sempre, com registro
            choice = req.strongest_once() or next((str(o["optionId"]) for o in req.options
                                                   if str(o.get("kind", "")).startswith("allow")), None)
            recorded = False
            if choice:
                try:
                    self.inbox.decide(entry.id, "allow_once", by="bypass")      # auditoria: Inbox + Notificações
                    recorded = True
                except Exception:                                             # noqa: BLE001 - sem registro, pergunta
                    log.exception("auto-aprovação não gravada no inbox: o pedido vai para o Felipe")
            if recorded:
                respond(choice)
                what = ("operação IRREVERSÍVEL executada sem confirmação (bypass)" if entry.policy == "IRREVERSIBLE"
                        else "aprovada automaticamente (bypass)")
                self._add(Notice(f"permissão para “{entry.title}”: {what}",
                                 "#ffb347" if entry.policy == "IRREVERSIBLE" else "#3ddc84"))
                for fn in self.permission_listeners:
                    fn(entry.id)
                return
        if self.ask_permission is None:                                     # padrao: cartao nao modal (UX F4b)
            self.broker.register(entry.id, req, respond)
            card = PermissionCard(req, entry, lambda option, eid=entry.id: self.answer_permission(eid, option))
            self._perm_cards[entry.id] = card
            self._add(card)
            self.status.setText("o agente espera sua permissão (cartão abaixo, aba Permissões ou Notificações)")
            return
        choice = self.ask_permission(req, self)
        kind = next((str(o.get("kind")) for o in req.options if str(o.get("optionId")) == str(choice)), None)
        try:
            self.inbox.decide(entry.id, kind or choice, by="human")
        except Exception:                                                     # noqa: BLE001 - a resposta ao agente vem primeiro
            log.exception("decisao nao gravada no inbox")
        respond(choice)
        title = (req.tool_call or {}).get("title", "ferramenta")
        self._add(Notice(f"permissão para “{title}”: {choice or 'recusada (diálogo fechado)'}",
                         "#3ddc84" if choice and not choice.startswith("reject") else "#ff9a9b"))

    def _on_update(self, gen: int, u: Update) -> None:
        if gen != self.gen:
            return
        if u.kind == "agent_message_chunk":
            if self._phase:
                self._phase = "answer"
            self._buf.append(u.text)
            self._turn_text.append(u.text)
            self._kick()
        elif u.kind == "agent_thought_chunk":
            if self._phase and self._phase != "answer":
                self._phase = "think"
            self._tbuf.append(u.text)
            self._turn_thought.append(u.text)
            self._kick()
        elif u.kind in ("tool_call", "tool_call_update"):
            self.flush()
            tid = str(u.data.get("toolCallId", ""))
            if u.kind == "tool_call":
                self.tools_observed.add(str(u.data.get("title") or u.data.get("kind") or "ferramenta")[:60])
            if u.data.get("status") in ("completed", "failed"):             # resultado da execucao no inbox
                try:
                    self.inbox.finish_tool_call(u.session_id, tid, str(u.data["status"]))
                except InboxClosed:
                    pass
            card = self.cards.get(tid)
            if card is None:
                card = ToolCard(tid)
                self.cards[tid] = card
                self._add(card)
            card.apply(u.data)
            self.text_block = None
        elif u.kind == "plan":
            self.flush()
            if self.plan_card is None:
                self.plan_card = PlanCard()
                self._add(self.plan_card)
            self.plan_card.set_entries(u.data.get("entries") or [])
            self.text_block = None
        elif u.kind == "config_option_update" and isinstance(u.data.get("configOptions"), list):
            self._populate_config(u.data["configOptions"])
        elif u.kind == "current_mode_update" and u.data.get("currentModeId"):
            self.chat.mode = str(u.data["currentModeId"])                     # o agente e a fonte do modo atual
            self._select_mode(self.chat.mode)
        elif u.kind == "usage_update":                                         # ACP: used/size/cost do agente
            model = str((find_config(self._config, "model") or {}).get("currentValue") or "")
            self._set_usage(from_acp_usage_update(u.data, provider_id=self.profile().key, model_id=model))
        elif u.kind == "prompt_usage":                                         # ACP: totais da sessao na resposta do prompt
            model = str((find_config(self._config, "model") or {}).get("currentValue") or "")
            self._set_usage(from_acp_prompt_usage(u.data, provider_id=self.profile().key, model_id=model))
        elif u.kind == "usage_snapshot":                                       # provedor da plataforma (Ollama)
            try:
                self._set_usage(UsageSnapshot.from_dict(u.data.get("snapshot") or {}))
            except (KeyError, TypeError, ValueError):
                log.warning("usage_snapshot invalido descartado")

    def _set_usage(self, snap: UsageSnapshot | None) -> None:
        if snap is not None and self.usage is not None:
            snap = self.usage.merged_with(snap)
        self.usage = snap
        self.usage_label.setText(usage_line(snap))

    def _kick(self) -> None:
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def flush(self) -> None:
        """Despeja o texto acumulado em no maximo um setText por bloco (R12)."""
        self.flush_timer.stop()
        if not self._buf and not self._tbuf:
            return
        self.flush_count += 1
        if self._tbuf:
            if self.thought_block is None:
                self.thought_block = TextBlock(thought=True)
                self._add(self.thought_block)
            self.thought_block.append("".join(self._tbuf))
            self._tbuf.clear()
        if self._buf:
            if self.text_block is None:
                self.text_block = TextBlock()
                self._add(self.text_block)
            self.text_block.append("".join(self._buf))
            self._buf.clear()
        if self._near_bottom():
            QTimer.singleShot(0, self.scroll, self._scroll_to_end)

    # ---------------------------------------------------------------------------------------------- historico
    def _refresh_history(self) -> None:
        self.history_box.blockSignals(True)
        self.history_box.clear()
        self.history_box.addItem("(conversa atual)", "")
        for c in self.store.list()[:50]:
            if c.id != self.chat.id:
                self.history_box.addItem(c.title, c.id)
        self.history_box.setCurrentIndex(0)
        self.history_box.blockSignals(False)

    def _clear_transcript(self) -> None:
        while self.tlay.count() > 1:
            item = self.tlay.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.empty:
                w.deleteLater()
        self.tlay.insertWidget(0, self.empty)
        self.cards, self.plan_card, self.text_block, self.thought_block = {}, None, None, None

    def new_chat(self) -> None:
        self._drop_client()
        self._set_busy(False)
        self.chat = self.store.new(self.workspace(), self.profile().key)
        self._clear_transcript()
        self.empty.setVisible(True)
        self._refresh_history()
        self.ensure_client()

    def prefill(self, workspace: str, prompt: str) -> None:
        """Conversa nova no workspace dado, com o texto ja no campo de entrada (o envio e manual)."""
        i = self.workspace_box.findData(workspace)
        if i < 0:
            self.workspace_box.insertItem(0, Path(workspace).name, workspace)
            i = 0
        self.workspace_box.setCurrentIndex(i)
        self.new_chat()
        self.input.setPlainText(prompt)
        self.input.setFocus()

    def _history_activated(self, idx: int) -> None:
        cid = self.history_box.itemData(idx)
        if cid:
            self.open_chat(str(cid))

    def open_chat(self, chat_id: str) -> None:
        self._drop_client()
        self._set_busy(False)
        self.chat = self.store.load(chat_id)
        self._clear_transcript()
        self.empty.setVisible(False)
        self.render_messages(self.chat.messages)
        i = self.workspace_box.findData(self.chat.workspace)
        if i < 0 and self.chat.workspace:
            self.workspace_box.insertItem(0, Path(self.chat.workspace).name, self.chat.workspace)
            i = 0
        if i >= 0:
            self.workspace_box.setCurrentIndex(i)
        self._refresh_history()
        self.ensure_client()

    def render_messages(self, messages: list[dict]) -> None:
        for m in messages:
            role = m.get("role")
            if role == "user":
                self._add(UserBubble(str(m.get("text", ""))))
            elif role in ("agent", "thought"):
                b = TextBlock(thought=role == "thought")
                b.append(str(m.get("text", "")))
                self._add(b)
            elif role == "tool":
                card = ToolCard(str(m.get("id", "")))
                card.apply({k: m.get(k) for k in ("title", "kind", "status")})
                self._add(card)
            elif role == "plan":
                pc = PlanCard()
                pc.set_entries(m.get("entries") or [])
                self._add(pc)
            elif role == "notice":
                self._add(Notice(str(m.get("text", ""))))
        self._add(Notice("— histórico recarregado —"))

    def shutdown(self) -> None:
        """Encerra os clientes que esta aba iniciou (e so eles) e o runtime proprio."""
        self.gen += 1                       # #75: o que o agente ainda tem na fila vira geracao velha e e descartado
        self.pending_prompt = None          # (permissao velha recebe None = cancelled; nada toca o inbox fechado)
        if self.client is not None:
            self._closing.append(self.client.close())
        for fut in self._closing:                                          # inclusive clientes trocados antes (workspace/agente)
            try:
                fut.result(8)
            except Exception:                                              # noqa: BLE001 - encerrando de qualquer jeito
                log.exception("falha ao encerrar o cliente ACP")
        self._closing = []
        self.client = None
        self._expire_timer.stop()
        if self._own_broker:
            self.broker.cancel_all("a aba do Chat foi fechada")
        else:                                                                # so os pedidos DESTA conversa
            mine = set(self._perm_cards)                                     # por id do pedido: ids de sessao ACP se repetem
            self.broker.cancel_where(lambda eid, _req: eid in mine, "a aba da sessão foi fechada")
        if self._own_inbox:
            self.inbox.cancel_open("a aba do Chat foi fechada")
            self.inbox.close()
        if self._own_rt:
            self.rt.stop()
