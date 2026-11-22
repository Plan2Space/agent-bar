"""chat.py — aba Chat do cockpit (F2): streaming coalescido, ToolCard/PlanCard, permissao, historico e workspace.

Os callbacks do cliente ACP chegam na thread asyncio; aqui eles viram sinais Qt (entrega enfileirada na thread da UI).
O handler de update so acumula texto ou atualiza um cartao: nada de I/O na thread da UI (R32).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from agent_bar.cockpit import profiles as prof
from agent_bar.cockpit.acp import (
    AcpClient,
    AsyncRuntime,
    PermissionRequest,
    Update,
    classify,
    mode_is_safe,
)
from agent_bar.cockpit.library import compose_prompt
from agent_bar.cockpit.store import Chat, ChatStore, data_dir
from agent_bar.cockpit.ui.widgets import (
    Notice,
    PlanCard,
    TextBlock,
    ToolCard,
    UserBubble,
    ask_permission_dialog,
)

log = logging.getLogger("agent_bar.cockpit.chat")

FLUSH_MS = 50
AUTOSCROLL_PX = 80
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


class _Bridge(QObject):
    update = Signal(int, object)
    permission = Signal(int, object, object)
    done = Signal(int, str, object, object)          # geracao, etiqueta, resultado, excecao
    exited = Signal(int, object)


class ChatView(QWidget):
    def __init__(self, store: ChatStore | None = None, runtime: AsyncRuntime | None = None, *,
                 client_factory: Callable[..., AcpClient] = AcpClient,
                 ask_permission: Callable[[PermissionRequest, QWidget], str | None] = ask_permission_dialog,
                 confirm_mode: Callable[[str, QWidget], bool] = confirm_risky_mode,
                 profiles_fn: Callable[[], list[prof.AgentProfile]] = prof.profiles,
                 workspaces_fn: Callable[[], list[Path]] = prof.workspaces,
                 context_provider: Callable[[], str] | None = None) -> None:
        super().__init__()
        self.context_provider = context_provider                          # F4: biblioteca (texto exato do preview)
        self.store = store or ChatStore()
        self.rt = runtime or AsyncRuntime("cockpit-chat")
        self._own_rt = runtime is None
        self.client_factory = client_factory
        self.ask_permission = ask_permission
        self.confirm_mode = confirm_mode
        self.no_safe_mode = False                                            # agente sem modo que pede permissao: envio bloqueado
        self._modes: dict = {}
        self.client: AcpClient | None = None
        self._closing: list = []                                             # close() de clientes descartados
        self.gen = 0
        self.ready = False
        self.busy = False
        self.pending_prompt: str | None = None
        self.chat: Chat = self.store.new()
        self.cards: dict[str, ToolCard] = {}
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
        self.agent_box = QComboBox()
        for p in self.profiles:
            ok, why = p.available()
            self.agent_box.addItem(p.label if ok else f"{p.label} — indisponível: {why}", p.key)
            if not ok:
                self.agent_box.model().item(self.agent_box.count() - 1).setEnabled(False)
        first_ok = next((i for i, p in enumerate(self.profiles) if p.available()[0]), 0)
        self.agent_box.setCurrentIndex(first_ok)
        self.workspace_box = QComboBox()
        for s in spaces:
            self.workspace_box.addItem(s.name, str(s))
        self.workspace_box.addItem("Escolher pasta…", "")
        self.mode_box = QComboBox()
        self.mode_box.setToolTip("Modos anunciados pelo agente. O padrão pede permissão.")
        self.history_box = QComboBox()
        self.history_box.setToolTip("Conversas salvas")
        self.new_btn = QPushButton("Nova conversa")
        for w in (self.agent_box, self.workspace_box, self.mode_box, self.history_box):
            w.setStyleSheet(COMBO_CSS)
        self.new_btn.setStyleSheet(BTN_CSS)
        for label, w, stretch in (("Agente", self.agent_box, 2), ("Workspace", self.workspace_box, 2),
                                  ("Modo", self.mode_box, 1), ("Histórico", self.history_box, 2)):
            cap = QLabel(label)
            cap.setStyleSheet("color:#8f8f98;font-size:11px")
            head.addWidget(cap)
            head.addWidget(w, stretch)
        head.addWidget(self.new_btn)
        lay.addLayout(head)
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
        self.empty = Notice("Converse com um agente de código no workspace escolhido. Ctrl+Enter envia. "
                            "Toda ferramenta que pedir permissão abre um diálogo; fechar o diálogo recusa.")
        self._add(self.empty)
        bottom = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Escreva para o agente… (Ctrl+Enter envia)")
        self.input.setFixedHeight(84)
        self.input.setStyleSheet("QPlainTextEdit{background:#1e1e23;color:#e6e6e6;border:1px solid #3a3a44;"
                                 "border-radius:6px;font-size:13px}")
        self.send_btn = QPushButton("Enviar")
        self.send_btn.setStyleSheet(BTN_CSS)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)
        lay.addLayout(bottom)
        self.status = QLabel("desconectado")
        self.status.setStyleSheet("color:#8f8f98;font-size:11px")
        lay.addWidget(self.status)
        self.send_btn.clicked.connect(self._send_or_stop)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=self._send_or_stop)
        QShortcut(QKeySequence("Ctrl+Enter"), self.input, activated=self._send_or_stop)
        self.new_btn.clicked.connect(self.new_chat)
        self.reconnect_btn.clicked.connect(self.reconnect)
        self.agent_box.currentIndexChanged.connect(lambda _i: self._restart("agente trocado"))
        self.workspace_box.activated.connect(self._workspace_activated)
        self.mode_box.activated.connect(self._mode_activated)
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
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_btn.setText("Parar" if busy else "Enviar")

    def _show_error(self, exc: BaseException | None, text: str = "") -> None:
        kind = classify(exc) if exc is not None else "error"
        hint = {"not_found": "o executável do agente não está no PATH", "auth": "o agente pede login na CLI dele",
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
        self.gen += 1
        gen = self.gen
        self.no_safe_mode = False
        self.ready = False
        p = self.profile()
        self.banner.setVisible(False)
        self.status.setText(f"conectando a {p.label} em {Path(ws).name}…")
        self.client = self.client_factory(
            list(p.argv), ws, runtime=self.rt,
            on_update=lambda u, g=gen: self.bridge.update.emit(g, u),
            on_permission=lambda req, respond, g=gen: self.bridge.permission.emit(g, req, respond),
            on_exit=lambda code, g=gen: self.bridge.exited.emit(g, code),
            stderr_path=data_dir() / "logs" / f"agent-{p.key}.stderr.log")
        fut = self.client.connect(self.chat.session_id or None)
        fut.add_done_callback(lambda f, g=gen: self._emit_done(g, "connect", f))

    def _emit_done(self, gen: int, tag: str, fut) -> None:
        exc = fut.exception()
        self.bridge.done.emit(gen, tag, None if exc else fut.result(), exc)

    def _drop_client(self) -> None:
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
            self._add(Notice(f"— {why}: novo cliente ACP em {Path(self.workspace()).name or '?'} (histórico visual mantido) —"))
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
        self.text_block = self.thought_block = None
        self._turn_text, self._turn_thought = [], []
        if self.client is None or not self.ready:
            self.pending_prompt = full
            self.ensure_client()
            return
        self._prompt(full)

    def _prompt(self, text: str) -> None:
        assert self.client is not None
        gen = self.gen
        self.status.setText("agente trabalhando…")
        self.client.prompt(text).add_done_callback(lambda f, g=gen: self._emit_done(g, "prompt", f))

    def stop(self) -> None:
        if self.client is not None:
            self.client.cancel()
        self.status.setText("cancelando…")

    # ------------------------------------------------------------------------------------- sinais (thread UI)
    def _on_done(self, gen: int, tag: str, result: object, exc: object) -> None:
        if gen != self.gen:
            return
        if tag == "connect":
            if exc is not None:
                self.client = None
                self._set_busy(False)
                self.pending_prompt = None
                self._show_error(exc)  # type: ignore[arg-type]
                return
            info = result if isinstance(result, dict) else {}
            self.ready = True
            if self.chat.session_id and not info.get("loaded"):                # R16: agente sem session/load
                self._add(Notice("— o agente não recarrega sessões: sessão nova, histórico anterior só visual —", "#ffc233"))
            self.chat.session_id = str(info.get("session_id") or "")
            self._populate_modes(info.get("modes") or {})
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
            if self.pending_prompt:
                text, self.pending_prompt = self.pending_prompt, None
                self._prompt(text)
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

    def _finish_turn(self, stop_reason: str | None, exc: BaseException | None) -> None:
        if self._turn_thought:
            self.chat.add("thought", "".join(self._turn_thought))
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
        choice = self.ask_permission(req, self)
        respond(choice)
        title = (req.tool_call or {}).get("title", "ferramenta")
        self._add(Notice(f"permissão para “{title}”: {choice or 'recusada (diálogo fechado)'}",
                         "#3ddc84" if choice and not choice.startswith("reject") else "#ff9a9b"))

    def _on_update(self, gen: int, u: Update) -> None:
        if gen != self.gen:
            return
        if u.kind == "agent_message_chunk":
            self._buf.append(u.text)
            self._turn_text.append(u.text)
            self._kick()
        elif u.kind == "agent_thought_chunk":
            self._tbuf.append(u.text)
            self._turn_thought.append(u.text)
            self._kick()
        elif u.kind in ("tool_call", "tool_call_update"):
            self.flush()
            tid = str(u.data.get("toolCallId", ""))
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
        elif u.kind == "current_mode_update" and u.data.get("currentModeId"):
            self.chat.mode = str(u.data["currentModeId"])                     # o agente e a fonte do modo atual
            self._select_mode(self.chat.mode)

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
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

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
        if self.client is not None:
            self._closing.append(self.client.close())
        for fut in self._closing:                                          # inclusive clientes trocados antes (workspace/agente)
            try:
                fut.result(8)
            except Exception:                                              # noqa: BLE001 - encerrando de qualquer jeito
                log.exception("falha ao encerrar o cliente ACP")
        self._closing = []
        self.client = None
        if self._own_rt:
            self.rt.stop()
