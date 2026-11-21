"""window.py — barra flutuante (PySide6) + painel expandido compacto (FP-058). So interface: coleta e controles vem de collectors/controls.
A coleta roda em thread propria; a UI so recebe `Snapshot` por sinal. Acoes demoradas (parar/reiniciar) rodam fora da thread de UI e
o resultado REAL aparece na linha de status do painel (nenhum botao aparenta sucesso quando a acao falhou)."""
from __future__ import annotations

import datetime as dt
import html
import math
import os
import threading
import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QProcess,
    QProcessEnvironment,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from agent_bar import collectors as col
from agent_bar import controls as ctl
from agent_bar import geometry as geo
from agent_bar import settings as cfg
from agent_bar.models import GroupedEvent, Level, Snapshot
from agent_bar.operations_view import OperationsView

COLORS = {Level.GREEN: "#3ddc84", Level.BLUE: "#4aa8ff", Level.YELLOW: "#ffc233", Level.RED: "#ff4d4f", Level.GRAY: "#8a8f98"}
LEVEL_NAME = {Level.GREEN: "conectado", Level.BLUE: "trabalhando", Level.YELLOW: "atencao", Level.RED: "erro", Level.GRAY: "desconectado / sem integracao"}
BTN_CSS = ("QPushButton{background:#2a2a30;color:#ddd;border:0;border-radius:6px;padding:4px 6px;font-size:11px}"
           "QPushButton:hover{background:#3a3a44}QPushButton:disabled{color:#666}")


AGENT_COLOR = {"Claude": "#d97757", "Codex": "#7aa2ff", "GPT": "#10a37f", "Listener": "#4aa8ff", "GitHub": "#e6e6e6"}
BAR_W = 640
_CUSTOM: dict[str, QPixmap | None] = {}


def icons_dir() -> Path:
    return Path(os.environ.get("AGENT_BAR_ICONS") or Path(os.environ.get("APPDATA", ".")) / "agent_bar" / "icons")


def custom_logo(name: str) -> QPixmap | None:
    """Logo oficial que o dono colocar em `%APPDATA%\\agent_bar\\icons\\<agente>.png` (claude.png, codex.png, gpt.png...) substitui o desenho padrao."""
    if name not in _CUSTOM:
        f = icons_dir() / f"{name.lower()}.png"
        pm = QPixmap(str(f)) if f.is_file() else None
        _CUSTOM[name] = pm if pm is not None and not pm.isNull() else None
    return _CUSTOM[name]


def _pen(color: str, width: float) -> QPen:
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _logo_claude(p: QPainter, size: float, phase: int, color: str) -> None:
    """Estrela de 8 pontas que gira e pulsa: o "cozinhando" do Claude Code."""
    p.translate(size / 2, size / 2)
    p.rotate(phase * 7 % 360)
    pulse = 0.72 + 0.28 * math.sin(phase * 0.4)
    p.setPen(_pen(color, size * 0.12))
    for i in range(8):
        p.save()
        p.rotate(i * 45)
        p.drawLine(QPointF(0, -size * 0.10), QPointF(0, -size * 0.44 * (pulse if i % 2 else 1.0)))
        p.restore()


def _logo_codex(p: QPainter, size: float, phase: int, color: str) -> None:
    """Balao do Codex com o prompt `>_` e o cursor piscando."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(size * 0.05, size * 0.18, size * 0.9, size * 0.64), size * 0.28, size * 0.28)
    p.setPen(_pen("#0b1020", size * 0.09))
    p.drawPolyline([QPointF(size * 0.27, size * 0.36), QPointF(size * 0.42, size * 0.5), QPointF(size * 0.27, size * 0.64)])
    if (phase // 5) % 2 == 0:
        p.drawLine(QPointF(size * 0.5, size * 0.66), QPointF(size * 0.72, size * 0.66))


def _logo_gpt(p: QPainter, size: float, phase: int, color: str) -> None:
    """Roseta de 6 petalas girando devagar."""
    p.translate(size / 2, size / 2)
    p.rotate(phase * 3 % 360)
    p.setPen(_pen(color, size * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(6):
        p.save()
        p.rotate(i * 60)
        p.drawRoundedRect(QRectF(-size * 0.11, -size * 0.44, size * 0.22, size * 0.5), size * 0.11, size * 0.11)
        p.restore()


def _logo_loop(p: QPainter, size: float, phase: int, color: str) -> None:
    """Dois arcos girando: o laco de revisao em andamento."""
    p.setPen(_pen(color, size * 0.13))
    p.setBrush(Qt.BrushStyle.NoBrush)
    box = QRectF(size * 0.14, size * 0.14, size * 0.72, size * 0.72)
    start = phase * 18 % 360
    p.drawArc(box, int(start * 16), 110 * 16)
    p.drawArc(box, int((start + 180) % 360 * 16), 110 * 16)


def _logo_orbit(p: QPainter, size: float, phase: int, color: str) -> None:
    """Anel com um ponto em orbita: consultando o GitHub."""
    p.setPen(_pen("#4a4f57", size * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(size * 0.12, size * 0.12, size * 0.76, size * 0.76))
    a = math.radians(phase * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(size / 2 + math.cos(a) * size * 0.38, size / 2 + math.sin(a) * size * 0.38), size * 0.11, size * 0.11)


LOGO_PAINTERS = {"Claude": _logo_claude, "Codex": _logo_codex, "GPT": _logo_gpt, "Listener": _logo_loop, "GitHub": _logo_orbit}


class StatusIcon(QWidget):
    """Bolinha na cor da CONEXAO (verde conectado, cinza desconectado/sem integracao, ambar atencao, vermelho erro); TRABALHANDO = o logo animado do agente."""
    SIZE = 22

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.name, self.level, self.working, self.phase = name, Level.GRAY, False, 0
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)       # arrastar a barra pelo icone

    def set_state(self, level: Level, working: bool) -> None:
        if (level, working) != (self.level, self.working):
            self.level, self.working = level, working
            self.update()

    def paintEvent(self, _e) -> None:                                           # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.working:
            logo = custom_logo(self.name)
            if logo is not None:
                p.setOpacity(0.55 + 0.45 * abs(math.sin(self.phase * 0.25)))
                p.drawPixmap(self.rect(), logo)
            else:
                LOGO_PAINTERS[self.name](p, float(self.SIZE), self.phase, AGENT_COLOR[self.name])
        else:
            d = self.SIZE * 0.52
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS[self.level]))
            p.drawEllipse(QRectF((self.SIZE - d) / 2, (self.SIZE - d) / 2, d, d))
        p.end()


class Worker(QObject):
    """Coleta periodica fora da thread de UI. Local a cada `local_interval_s`; GitHub e Project #1 com cache/TTL proprios."""
    snapshot = Signal(object)

    def __init__(self, settings: cfg.Settings, gov: Path | None = None):
        super().__init__()
        self.settings, self.gov = settings, gov
        self._stop, self._now = threading.Event(), threading.Event()
        self._cache = col.GithubCache(ttl_s=settings.github_interval_s)
        self._backlog = col.GithubCache(ttl_s=settings.backlog_interval_s)
        self._queue = col.GithubCache(ttl_s=settings.github_interval_s)
        from agent_bar.approvals import ApprovalBoard
        self._approvals = col.BackgroundCache(ttl_s=settings.github_interval_s,                      # varias chamadas por PR: nunca atrasa a barra
                                              placeholder=ApprovalBoard(error="coletando aprovações (primeira leitura)"),
                                              on_done=lambda: self._now.set(),
                                              on_error=lambda exc: ApprovalBoard(error=f"coleta de aprovações falhou: {type(exc).__name__}: {exc}"))
        self._thread = threading.Thread(target=self._loop, name="agent-bar-collector", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def refresh(self, force: bool = False) -> None:
        if force:
            self._cache.invalidate()
            self._backlog.invalidate()
            self._queue.invalidate()
            self._approvals.invalidate()
        self._now.set()

    def stop(self) -> None:
        self._stop.set()
        self._now.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.snapshot.emit(col.collect_snapshot(self.gov, cache=self._cache, backlog_cache=self._backlog, queue_cache=self._queue,
                                                   approval_cache=self._approvals, claude_login=col.claude_login_state, codex_login=col.codex_login_state))
            except Exception:                                                  # noqa: BLE001 — coleta nunca derruba a UI
                traceback.print_exc()
            self._now.wait(self.settings.local_interval_s)
            self._now.clear()


def dot(level: Level, name: str | None = None) -> str:
    """Bolinha da cor da conexao; quando o agente esta TRABALHANDO (azul) o glifo de trabalho na cor do proprio agente."""
    if level is Level.BLUE:
        return f'<span style="color:{AGENT_COLOR.get(name or "", COLORS[Level.BLUE])};font-size:13px">&#10043;</span>'
    return f'<span style="color:{COLORS[level]};font-size:13px">&#9679;</span>'


def hhmm(iso_text: str | None) -> str:
    t = col.parse_iso(iso_text)
    return "-" if t is None else t.astimezone().strftime("%H:%M:%S")


def esc(text: str) -> str:
    return html.escape(text or "")


def screen_areas() -> list[geo.Rect]:
    """Area util (sem barra de tarefas) de cada monitor, em pixels logicos (Qt ja aplica a escala DPI de cada monitor)."""
    out = []
    for s in QGuiApplication.screens():
        g = s.availableGeometry()
        out.append(geo.Rect(g.left(), g.top(), g.width(), g.height()))
    return out


QUEUE_LABEL = {"pronto": ("#3ddc84", "pronto{s} para você mergear"), "corrigir": ("#ffc233", "a corrigir"), "conflito": ("#ff6b6d", "com conflito"),
               "ci": ("#ff6b6d", "com CI falhando"), "aguarda": ("#8fb4ff", "aguardando revisão"), "rascunho": ("#8a8f98", "rascunho")}


def age_text(s: int) -> str:
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else f"{s // 60}min" if s >= 60 else f"{s}s"


def board_html(b) -> str:
    """Project selecionado (nome, link, id), contagens e itens lidos AGORA. Erro = INDISPONIVEL com o motivo, sem numero nenhum."""
    tag = " <span style='color:#ffc233'><b>LEGADO</b></span>" if b.board == "legacy" else ""
    url = b.url or f"https://github.com/orgs/{b.org}/projects/{b.number}"
    name = esc(b.title or f"{b.org} Project #{b.number}")
    head = (f"<b>Board</b>{tag} <a href=\"{esc(url)}\" style='color:#8fb4ff'>{name}</a> "
            f"<span style='color:#6d6d75'>#{b.number} · {esc(b.node_id or 'id ?')}</span>")
    if b.error:
        return head + f"<br><b style='color:#ff6b6d'>INDISPONÍVEL</b> <span style='color:#ff9a9b'>{esc(b.error)}</span>"
    cols = " &middot; ".join(f"{esc(k)} <b>{v}</b>" for k, v in b.counts.items())
    order = {k: i for i, k in enumerate(b.counts)}
    rows = sorted(b.items, key=lambda i: (order.get(i.status, 99), i.task_id or "~", i.ref))
    items = "<br>".join(f"&bull; <b>{esc(i.task_id or '-')}</b> <span style='color:#9a9aa2'>{esc(i.status)} · {esc(i.ref)}</span>" for i in rows[:30])
    more = f"<br><span style='color:#6d6d75'>+{len(rows) - 30} itens</span>" if len(rows) > 30 else ""
    return (head + f"<br>{cols} <span style='color:#6d6d75'>({b.total} itens · lido {hhmm(b.synced)})</span>"
            + (f"<br>{items}{more}" if items else ""))


def queue_html(q) -> str:
    """Fila de PRs: contagens por situacao, os que pedem acao com link e o erro explicito quando a leitura falha (nunca um 0 inventado)."""
    if q.error and not q.rows:
        return f"<b>PRs</b> <span style='color:#ff9a9b'>indisponível: {esc(q.error)}</span>"
    counts = q.counts()
    parts = []
    for key, (color, text) in QUEUE_LABEL.items():
        if key in counts:
            n = counts[key]
            parts.append(f"<span style='color:{color}'><b>{n} {text.format(s='s' if n != 1 else '')}</b></span>")
    stale = f" <span style='color:#ffc233'>(desatualizado desde {hhmm(q.synced)}; erro: {esc(q.error)})</span>" if q.error else ""
    head = f"<b>PRs</b> {len(q.rows)} abertos · " + " · ".join(parts) + stale
    acts = [r for r in q.rows if r.state in ("pronto", "corrigir", "conflito", "ci")][:6]
    def link(r) -> str:                                                           # so abre links https://github.com/ (mesma lista de permissao do botao "PR atual")
        name = f"{r.repo.split('/')[-1]}#{r.number}" if r.repo else f"#{r.number}"
        return f"<a href=\"{esc(r.url)}\" style='color:#8fb4ff'>{esc(name)}</a>" if r.url.startswith("https://github.com/") else esc(name)
    lines = [f"&bull; {link(r)} <span style='color:{QUEUE_LABEL[r.state][0]}'>{QUEUE_LABEL[r.state][1].format(s='')}</span> "
             f"<span style='color:#9a9aa2'>{esc(r.title[:44])}</span>" for r in acts]
    return head + ("<br>" + "<br>".join(lines) if lines else "")


def screen_bounds() -> list[geo.Rect]:
    """Monitor INTEIRO (inclui a area da barra de tarefas): a barra flutuante pode ficar sobre a barra de tarefas; so o painel respeita `screen_areas`."""
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append(geo.Rect(g.left(), g.top(), g.width(), g.height()))
    return out


def raise_topmost(hwnd: int) -> None:
    """Reafirma HWND_TOPMOST sem ativar nem mover: a barra de tarefas do Windows volta para a frente das janelas 'sempre no topo' quando recebe clique."""
    if os.name != "nt" or QGuiApplication.platformName() == "offscreen":
        return
    import ctypes
    ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010 | 0x0200)   # NOMOVE|NOSIZE|NOACTIVATE|NOOWNERZORDER


TB_CSS = ("QPushButton{background:transparent;color:#cfcfd4;border:0;border-radius:4px;font-size:13px;padding:0}"
          "QPushButton:hover{background:#3a3a44}")
TB_CLOSE_CSS = TB_CSS + "QPushButton:hover{background:#c0392b;color:white}"


class TitleBar(QWidget):
    """Barra de titulo fixa do painel: arrasta o painel pelo botao esquerdo; sempre visivel (fica fora da area com rolagem)."""
    HEIGHT = 30

    def __init__(self, panel: "Details"):
        super().__init__()
        self.panel = panel
        self._off: QPoint | None = None
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(2)
        self.title = QLabel("Agent Control")
        self.title.setStyleSheet("color:#f0f0f0;font-size:12px;font-weight:600")
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.btn_min = self._btn("\u2014", "Recolher o painel (so a barra de titulo); a barra flutuante continua", panel.toggle_collapsed)
        self.btn_max = self._btn("\u25a1", "Restaurar / ajustar o tamanho ao conteudo", panel.restore_fit)
        self.btn_close = self._btn("\u2715", "Fechar SOMENTE o painel (nao para o listener, Claude nem Codex)", panel.close_panel, TB_CLOSE_CSS)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)

    def _btn(self, text: str, tip: str, fn: Callable[[], None], css: str = TB_CSS) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(26, 22)
        b.setToolTip(tip)
        b.setStyleSheet(css)
        b.setCursor(Qt.CursorShape.ArrowCursor)
        b.clicked.connect(lambda _=False: fn())
        return b

    def mousePressEvent(self, e) -> None:                                       # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._off = e.globalPosition().toPoint() - self.panel.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:                                        # noqa: N802
        if self._off is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.panel.move_to(e.globalPosition().toPoint() - self._off, user=True)

    def mouseReleaseEvent(self, e) -> None:                                     # noqa: N802
        self._off = None


class Details(QWidget):
    WIDTH = 560
    FULL_WIDTH = 920

    def __init__(self, bar: "Bar"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("Harness Control Center FP-061")
        self.bar = bar
        self._seen_top: str | None = None
        self.user_moved = False                                  # depois que o usuario move o painel, ele NAO segue mais a barra
        self.collapsed = False
        self.full_mode = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(self.WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        # corpo com rolagem interna (a barra de titulo e o rodape ficam fixos e sempre acessiveis)
        self.body = QWidget()
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(2)
        self.banner, self.agents, self.health, self.backlog = QLabel(), QLabel(), QLabel(), QLabel()
        for w in (self.banner, self.agents, self.health, self.backlog):
            w.setTextFormat(Qt.TextFormat.RichText)
            w.setWordWrap(True)
            w.setMargin(0)
            w.setStyleSheet("color:#e6e6e6;font-size:11px")
            lay.addWidget(w)
        self.legend = QLabel(f"{dot(Level.GREEN)} conectado &middot; {dot(Level.GRAY)} desconectado / sem integração &middot; {dot(Level.YELLOW)} atenção &middot; "
                             f"{dot(Level.RED)} erro &middot; logo animado = trabalhando")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet("color:#8f8f98;font-size:10px;padding:2px 0 4px 0")
        lay.insertWidget(2, self.legend)                                         # logo abaixo da lista de agentes
        self.queue = QLabel()
        self.queue.setTextFormat(Qt.TextFormat.RichText)
        self.queue.setWordWrap(True)
        self.queue.setOpenExternalLinks(True)                    # links dos PRs abrem no navegador
        self.backlog.setOpenExternalLinks(True)                  # link do Project selecionado
        self.backlog.setTextFormat(Qt.TextFormat.RichText)
        self.queue.setStyleSheet("color:#e6e6e6;font-size:11px;padding:4px 0")
        lay.addWidget(self.queue)
        self.terminal_title = QLabel("<b>Terminal do passo</b> <span style='color:#6d6d75'>(somente leitura; &quot;Parar passo&quot; interrompe)</span>")
        self.terminal_title.setTextFormat(Qt.TextFormat.RichText)
        self.terminal_title.setStyleSheet("color:#e6e6e6;font-size:11px;padding-top:4px")
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setMaximumBlockCount(500)
        self.terminal.setFixedHeight(200)
        self.terminal.setStyleSheet("QPlainTextEdit{background:#0e0e11;color:#cfcfd4;border:1px solid #2a2a30;border-radius:6px;"
                                    "font-family:Consolas,'Cascadia Mono',monospace;font-size:11px}")
        for w in (self.terminal_title, self.terminal):
            w.setVisible(False)
            lay.addWidget(w)
        self.events = QListWidget()
        self.events.setStyleSheet("QListWidget{background:#17171a;color:#cfcfd4;border:0;font-size:11px}QListWidget::item{padding:1px 4px}")
        self.events.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.events.setToolTip("Duplo clique: abre o log original")
        self.events.itemDoubleClicked.connect(lambda _i: bar.action_logs())
        lay.addWidget(self.events)
        self.overview = QLabel("Event Store: indisponível · mensagens: indisponíveis")
        self.overview.setWordWrap(True)
        self.overview.setStyleSheet("color:#9facba;font-size:11px;padding:6px 2px")
        lay.addWidget(self.overview)
        self.operations = OperationsView(self.body)
        self.operations.approve_button.clicked.connect(bar.action_approve)
        self.operations.setVisible(False)
        lay.addWidget(self.operations)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent}QScrollArea>QWidget>QWidget{background:transparent}")
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)
        self.footer = QWidget()
        flay = QVBoxLayout(self.footer)
        flay.setContentsMargins(12, 2, 12, 9)
        flay.setSpacing(3)
        self.status = QLabel("pronto")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9a9aa2;font-size:11px")
        flay.addWidget(self.status)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.buttons: dict[str, QPushButton] = {}
        specs = [("repo", "Org GitHub", bar.action_repo), ("project", f"Project #{ctl._TARGETS.project}", bar.action_project), ("pr", "PR atual", bar.action_pr),
                 ("logs", "Logs", bar.action_logs), ("diag", "Copiar diagn\u00f3stico", bar.action_copy_diag),
                 ("refresh", "Atualizar", bar.action_refresh), ("restart", "Reiniciar DRY_RUN", bar.action_restart), ("stop", "Parar", bar.action_stop),
                 ("progress", "Dar andamento (Claude)", bar.action_progress)]
        for i, (key, text, fn) in enumerate(specs):
            b = QPushButton(text)
            b.setStyleSheet(BTN_CSS)
            b.clicked.connect(lambda _=False, f=fn: f())
            self.buttons[key] = b
            grid.addWidget(b, i // 4, i % 4)
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            for key in ("restart", "stop", "progress"):
                self.buttons[key].setEnabled(False)
                self.buttons[key].setToolTip("Instância FP-061 somente leitura: controle desativado")
        flay.addLayout(grid)
        self.mode_button = QPushButton("Central completa  ↗")
        self.mode_button.setStyleSheet(BTN_CSS)
        self.mode_button.clicked.connect(self.toggle_full_mode)
        flay.addWidget(self.mode_button)
        outer.addWidget(self.footer)
        QShortcut(QKeySequence("Esc"), self, activated=self.close_panel)       # Esc recolhe (fecha o painel; NAO encerra a barra)

    # ----------------------------------------------------------------------------------------------- janela / limites
    def keyPressEvent(self, e) -> None:                                         # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:                                      # noqa: N802
        m = QMenu(self)
        m.addAction("Fechar painel", self.close_panel)
        m.addAction("Recolher / restaurar", self.toggle_collapsed)
        m.addSeparator()
        m.addAction("Encerrar barra", self.bar.close_app)
        m.exec(e.globalPos())

    def area(self) -> geo.Rect:
        return geo.pick_screen(screen_areas(), self.x(), self.y(), self.width(), self.height())

    def clamp(self) -> None:
        nx, ny = geo.clamp_window(self.x(), self.y(), self.width(), self.height(), self.area())
        if (nx, ny) != (self.x(), self.y()):
            self.move(nx, ny)

    def move_to(self, pos: QPoint, user: bool = False) -> None:
        """Move (limitado ao monitor onde fica). `user=True`: a partir daqui o painel e independente da barra."""
        if user:
            self.user_moved = True
        area = geo.pick_screen(screen_areas(), pos.x(), pos.y(), self.width(), self.height())
        x, y = geo.clamp_window(pos.x(), pos.y(), self.width(), self.height(), area)
        self.move(x, y)

    def place_near_bar(self) -> None:
        if self.user_moved:
            self.clamp()
            return
        b = self.bar
        x, y = geo.place_below(b.x(), b.y(), b.height(), self.width(), self.height(), screen_areas())
        self.move(x, y)

    def fit(self) -> None:
        """Altura = conteudo, limitada a area util do monitor; o excedente rola por dentro (titulo e botoes continuam visiveis)."""
        if self.collapsed:
            self.setFixedHeight(TitleBar.HEIGHT)
            return
        lay = self.body.layout()
        inner_w = self.width() - 24
        body_h = lay.totalHeightForWidth(inner_w) if lay.hasHeightForWidth() else lay.sizeHint().height()
        desired = TitleBar.HEIGHT + body_h + 12 + self.footer.sizeHint().height()
        if not self.full_mode:
            desired = min(desired, 580)
        self.setFixedHeight(geo.fit_height(desired, self.area()))
        self.clamp()

    def toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        self.scroll.setVisible(not self.collapsed)
        self.footer.setVisible(not self.collapsed)
        self.fit()
        self.clamp()

    def restore_fit(self) -> None:
        """'□': se recolhido, restaura; senao reajusta ao conteudo e volta a posicionar na tela."""
        if self.collapsed:
            self.toggle_collapsed()
        else:
            self.fit()
            self.clamp()

    def close_panel(self) -> None:
        """Fecha SOMENTE o painel. Nao toca no listener, no Claude nem no Codex (nem na coleta da barra)."""
        self.hide()

    def toggle_full_mode(self) -> None:
        """Expanded and full are views of this same panel and collector."""
        self.full_mode = not self.full_mode
        self.setFixedWidth(self.FULL_WIDTH if self.full_mode else self.WIDTH)
        self.operations.setVisible(self.full_mode)
        for widget in (self.health, self.backlog, self.events):
            widget.setVisible(not self.full_mode)
        self.mode_button.setText("Painel expandido  ↙" if self.full_mode else "Central completa  ↗")
        title = "Harness Control Center" if self.full_mode else "Agent Control"
        if self.bar.snap and self.bar.snap.observation.source == "demonstração":
            title += " · DEMONSTRAÇÃO"
        self.titlebar.title.setText(title)
        self.fit()
        self.place_near_bar()

    def open_panel(self) -> None:
        self.fit()
        self.place_near_bar()
        self.show()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.start()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, _e) -> None:                                           # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(22, 22, 26, 238))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 14, 14)

    def set_status(self, ok: bool, text: str) -> None:
        color = "#3ddc84" if ok else "#ff6b6d"
        self.status.setText(f'<span style="color:{color}">{"&#10003;" if ok else "&#10007;"}</span> {esc(text)}  <span style="color:#6d6d75">{dt.datetime.now():%H:%M:%S}</span>')
        self.status.setTextFormat(Qt.TextFormat.RichText)

    def _event_text(self, e: GroupedEvent) -> str:
        span = f"  ×{e.count} ({hhmm(e.first_at)}–{hhmm(e.last_at)})" if e.count > 1 else ""
        reason = f"  {e.reason}" if e.reason and e.count == 1 else ""
        return f"{hhmm(e.last_at)}  {e.action}  {e.pr_label()}{span}{reason}"[:150]

    def term_append(self, text: str) -> None:
        """Uma linha no terminal do painel; mostra a area na primeira linha."""
        if self.terminal.isHidden():
            self.terminal_title.setVisible(True)
            self.terminal.setVisible(True)
            self.fit()
        self.terminal.appendPlainText(text)
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def update_snapshot(self, s: Snapshot) -> None:
        self.banner.setText(f'<b style="color:{"#ffc233" if s.mode == "DRY_RUN" else "#ff6b6d" if s.mode == "EXECUTE" else "#8a8f98"}">'
                            f'{esc(s.mode_banner())}</b> &middot; {esc(s.work)}')
        rows = []
        for a in s.agents():
            pid = f" <span style='color:#8a8f98'>pid {a.pid}</span>" if a.pid else ""
            rows.append(f"{dot(a.level, a.name)} <b>{a.name}</b> {esc(a.label)}{pid}<br><span style='color:#8f8f98'>&nbsp;&nbsp;&nbsp;{esc(a.detail)}</span>")
        self.agents.setText("<br>".join(rows))
        hb = "indisponível" if s.heartbeat_age_s is None else f"{s.heartbeat_age_s}s"
        ks = "indisponível" if s.kill_switch is None else ("ATIVO" if s.kill_switch else "inativo")
        err = f"<br><span style='color:#ff9a9b'>último erro: {esc(s.last_error)}</span>" if s.last_error else ""
        self.health.setText(f"<b>Saúde</b> heartbeat {hb} &middot; kill switch {ks} &middot; instâncias "
                            f"{'?' if s.concurrent_instances is None else s.concurrent_instances}<br>lock: {esc(s.lock_status)}<br>"
                            f"executor: {esc(s.executor_summary)}{err}")
        self.queue.setText(queue_html(s.pr_queue))
        self.backlog.setText(board_html(s.board))
        top = s.events[0].last_at if s.events else None
        self.events.clear()
        for e in s.events[:20]:
            it = QListWidgetItem(self._event_text(e))
            new = self._seen_top is not None and e.last_at > self._seen_top
            if e.error:
                it.setForeground(QColor("#ff6b6d"))
            elif new:
                it.setForeground(QColor("#4aa8ff"))
            if new or e.error:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.events.addItem(it)
        self._seen_top = top or self._seen_top
        obs = s.observation
        if obs.source == "demonstração":
            self.titlebar.title.setText(("Harness Control Center" if self.full_mode else "Agent Control") + " · DEMONSTRAÇÃO")
        else:
            self.titlebar.title.setText("Harness Control Center" if self.full_mode else "Agent Control")
        if obs.source in ("lido", "demonstração"):
            counts = " · ".join(f"{k} {obs.counts.get(k, 0)}" for k in ("PENDING", "RUNNING", "DLQ"))
            age = "sem eventos" if obs.last_event_age_s is None else f"último evento há {age_text(obs.last_event_age_s)}"
            self.overview.setText(f"<b>Event Store · {esc(obs.source.upper())}</b>  {counts} · outbox {obs.outbox_pending}"
                                  f"<br><span style='color:#8f8f98'>{esc(obs.db_path)} · schema {esc(obs.schema_version or '?')} · {age}"
                                  f" · lido não comprova produção</span>"
                                  f"<br>{esc(obs.messages_status)}")
        else:
            self.overview.setText(f"<b>Event Store · INDISPONÍVEL</b>  {esc(obs.reason)}"
                                  f"<br>{esc(obs.messages_status)}")
        self.operations.update_observation(obs)
        self.operations.update_runs(s.runs, s.benchmark)
        self.operations.update_approvals(s.approvals, observe_only=os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1")
        rows_h = self.events.sizeHintForRow(0) if self.events.count() else 0
        self.events.setFixedHeight(min(max(self.events.count(), 1), 6) * max(rows_h, 20) + 6)       # sem espaco vazio; rola acima de 6
        self.fit()


class Bar(QWidget):
    result = Signal(bool, str)

    def __init__(self, settings: cfg.Settings, gov: Path | None = None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("Agent Bar FP-061")
        self.settings, self.gov = settings, gov or col.default_gov_dir()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(settings.opacity)
        self.resize(BAR_W, 65)
        self.labels: dict[str, QLabel] = {}
        self.icons: dict[str, StatusIcon] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 12, 6)
        lay.setSpacing(14)
        for name in ("Claude", "Codex", "GPT", "Listener", "GitHub"):
            icon = StatusIcon(name)
            lb = QLabel(name)
            lb.setStyleSheet("color:#f0f0f0;font-size:13px;font-weight:600")
            self.icons[name], self.labels[name] = icon, lb
            chip = QHBoxLayout()
            chip.setSpacing(5)
            chip.addWidget(icon)
            chip.addWidget(lb)
            lay.addLayout(chip)
        self.pr = QLabel("PR -")
        self.pr.setStyleSheet("color:#bdbdc4;font-size:12px")
        lay.addWidget(self.pr)
        lay.addStretch(1)
        self.play = QPushButton("▶")                                             # o mesmo "Dar andamento" do painel, a um clique da barra
        self.play.setFixedSize(28, 28)
        self.play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play.setStyleSheet("QPushButton{background:#2a2a30;color:#3ddc84;border:0;border-radius:14px;font-size:13px}"
                                "QPushButton:hover{background:#3a3a44}QPushButton:disabled{color:#555}")
        self.play.setToolTip("Dar andamento nos PRs da FP-060 (Claude)")
        self.play.clicked.connect(lambda _=False: self.action_progress())
        lay.addWidget(self.play)
        self.gear = QLabel("&#9881;")
        self.gear.setTextFormat(Qt.TextFormat.RichText)
        self.gear.setStyleSheet("color:#bdbdc4;font-size:17px")
        lay.addWidget(self.gear)
        self.snap: Snapshot | None = None
        self._drag: QPoint | None = None
        self._moved = False
        self._levels: dict[str, Level] = {}
        self.anim_timer = QTimer(self)                                               # anima os logos so enquanto alguem trabalha
        self.anim_timer.setInterval(90)
        self.anim_timer.timeout.connect(self._tick_animation)
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            self.play.setEnabled(False)
            self.play.setToolTip("Instância FP-061 somente leitura: controle desativado")
        self.step_process: QProcess | None = None                                    # o passo FP-060 em curso (processo FILHO deste painel)
        self._step_buf = b""
        self._step_stopped = False
        self._refresh_pending: float | None = None
        self.tray = QSystemTrayIcon(self) if QSystemTrayIcon.isSystemTrayAvailable() else None
        self.worker = Worker(settings, self.gov)
        self.details = Details(self)
        self.worker.snapshot.connect(self.on_snapshot)
        self.result.connect(self.details.set_status)
        self._place()
        self.topmost_timer = QTimer(self)                                           # a barra de tarefas retoma o topo ao ser clicada: a barra o reafirma sem roubar o foco
        self.topmost_timer.setInterval(500)
        self.topmost_timer.timeout.connect(self._keep_on_top)
        self.topmost_timer.start()
        self.worker.start()

    # -------------------------------------------------------------------------------------------------- posicao/pintura
    def _place(self) -> None:
        if self.settings.x is not None and self.settings.y is not None:
            self.move(self._clamped(QPoint(self.settings.x, self.settings.y)))      # sobre a barra de tarefas e permitido; fora do monitor (layout mudou) volta para dentro
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.width() - 24, g.top() + 24)
        for screen in QGuiApplication.screens():                                     # monitor/resolucao mudou: a barra nunca fica fora da tela
            screen.geometryChanged.connect(lambda *_: self._reclamp())

    def _reclamp(self) -> None:
        self.move(self._clamped(self.pos()))
        self._sync_details()

    def _keep_on_top(self) -> None:
        raise_topmost(int(self.winId()))
        if self.details.isVisible():
            raise_topmost(int(self.details.winId()))

    def paintEvent(self, _e) -> None:                                           # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(20, 20, 24, 225))
        p.setPen(QColor(70, 70, 80, 200))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)

    # ------------------------------------------------------------------------------------------------------- mouse
    def mousePressEvent(self, e) -> None:                                       # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag, self._moved = e.globalPosition().toPoint() - self.frameGeometry().topLeft(), False

    def mouseMoveEvent(self, e) -> None:                                        # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self._moved = True
            self.move(self._clamped(e.globalPosition().toPoint() - self._drag))
            self._sync_details()

    def mouseReleaseEvent(self, e) -> None:                                     # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                self.toggle_details()
            else:
                self.settings.x, self.settings.y = self.x(), self.y()
                cfg.save(self.settings)
            self._drag = None

    def contextMenuEvent(self, e) -> None:                                      # noqa: N802
        m = QMenu(self)
        m.addAction("Expandir painel" if not self.details.isVisible() else "Fechar painel", self.toggle_details)
        m.addAction("Atualizar agora", self.action_refresh)
        n = QAction("Notificacoes", m, checkable=True, checked=self.settings.notifications)
        n.toggled.connect(self.set_notifications)
        m.addAction(n)
        m.addAction("Minimizar para icone", self.toggle_minimized)
        m.addSeparator()
        m.addAction("Encerrar barra (fecha a barra inteira)", self.close_app)
        m.exec(e.globalPos())

    # ---------------------------------------------------------------------------------------------------- painel/estado
    def toggle_details(self) -> None:
        if self.details.isVisible():
            self.details.close_panel()
        else:
            self.details.open_panel()

    def close_panel(self) -> None:
        """Fecha SO o painel (acao distinta de `close_app`, que encerra a barra)."""
        self.details.close_panel()

    def _clamped(self, pos: QPoint) -> QPoint:
        area = geo.pick_screen(screen_bounds(), pos.x(), pos.y(), self.width(), self.height())
        x, y = geo.clamp_window(pos.x(), pos.y(), self.width(), self.height(), area, margin=0)
        return QPoint(x, y)

    def _sync_details(self) -> None:
        """Enquanto o usuario nao moveu o painel, ele acompanha a barra; depois disso e independente (so respeita os limites da tela)."""
        if self.details.isVisible() and not self.details.user_moved:
            self.details.place_near_bar()

    def keyPressEvent(self, e) -> None:                                         # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def toggle_minimized(self) -> None:
        self.settings.minimized = not self.settings.minimized
        cfg.save(self.settings)
        mini = self.settings.minimized
        for w in (*self.labels.values(), *self.icons.values(), self.pr, self.play, self.gear):
            w.setVisible(not mini)
        self.resize(44 if mini else BAR_W, 44 if mini else 65)
        if mini:
            self.details.hide()

    def set_notifications(self, on: bool) -> None:
        self.settings.notifications = on
        cfg.save(self.settings)

    def report(self, r: ctl.Result) -> None:
        self.result.emit(r.ok, r.message)

    # ------------------------------------------------------------------------------------------------ acoes (botoes)
    def action_repo(self) -> None:
        self.report(ctl.open_url(ctl.REPO_URL))

    def action_project(self) -> None:
        self.report(ctl.open_url(ctl.PROJECT_URL))

    def action_pr(self) -> None:
        self.report(ctl.open_url(self.snap.pr_url if self.snap else None))

    def action_logs(self) -> None:
        p = ctl.latest_log(self.gov)
        self.report(ctl.open_path(p) if p else ctl.Result(False, "nenhum log encontrado"))

    def action_copy_diag(self) -> None:
        if not self.snap:
            self.report(ctl.Result(False, "sem coleta ainda: nada para copiar"))
            return
        text = ctl.diagnostic_text(self.snap)
        QApplication.clipboard().setText(text)
        ok = QApplication.clipboard().text() == text                              # prova: le de volta
        self.report(ctl.Result(ok, f"diagnostico copiado ({len(text)} caracteres)" if ok else "falha ao copiar para a area de transferencia"))

    def action_refresh(self) -> None:
        self._refresh_pending = True
        self.worker.refresh(force=True)
        QTimer.singleShot(15000, self._refresh_timeout)

    def _refresh_timeout(self) -> None:
        if self._refresh_pending:
            self._refresh_pending = None
            self.report(ctl.Result(False, "atualizar: sem resposta da coleta em 15 s"))

    def _run_async(self, fn: Callable[[], ctl.Result]) -> None:
        def go() -> None:
            try:
                r = fn()
            except Exception as exc:                                             # noqa: BLE001 — falha vira mensagem, nunca sucesso falso
                r = ctl.Result(False, f"erro inesperado: {type(exc).__name__}: {exc}")
            self.result.emit(r.ok, r.message)
            self.worker.refresh(force=False)
        threading.Thread(target=go, name="agent-bar-control", daemon=True).start()

    def action_approve(self) -> None:
        """S00-14: aprova o HEAD mostrado. Confirmacao aqui (thread da UI); nova leitura do HEAD/CI e o comentario, fora dela."""
        from agent_bar import approvals as ap
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            self.report(ctl.Result(False, "instância somente leitura: aprovação desativada"))
            return
        row = self.details.operations.selected_approval
        if row is None or not row.can_approve:
            self.report(ctl.Result(False, f"aprovação indisponível: {row.blocked_reason if row else 'nenhum PR aguardando'}"))
            return
        if not self.confirm(ap.confirmation_text(row)):
            self.report(ctl.Result(False, "aprovação cancelada: nada foi postado"))
            return
        self.details.set_status(True, f"conferindo o HEAD de {row.pr_ref} antes de postar...")

        def go() -> ctl.Result:
            ok, msg = ap.approve(col.run_gh, row, confirm=lambda _text: True)      # ja confirmado acima, na thread da UI
            self.worker.refresh(force=True)
            return ctl.Result(ok, msg)
        self._run_async(go)

    def action_restart(self) -> None:
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            self.report(ctl.Result(False, "instância FP-061 somente leitura: controle desativado"))
            return
        if self.confirm("Reiniciar o listener em DRY_RUN? Nao executa nada; recusa com kill switch, execucao ativa ou -Execute."):
            self.details.set_status(True, "reiniciando em DRY_RUN...")
            self._run_async(lambda: ctl.restart_listener_dry_run(confirmed=True, gov=self.gov))

    def _sync_animation(self) -> None:
        working = any(i.working for i in self.icons.values())
        if working and not self.anim_timer.isActive():
            self.anim_timer.start()
        elif not working and self.anim_timer.isActive():
            self.anim_timer.stop()

    def _tick_animation(self) -> None:
        for icon in self.icons.values():
            if icon.working:
                icon.phase += 1
                icon.update()

    def _set_step_ui(self, running: bool) -> None:
        """Os dois botoes (barra e painel) mostram o mesmo estado do passo."""
        self.details.buttons["progress"].setText("Parar passo" if running else "Dar andamento (Claude)")
        self.play.setText("■" if running else "▶")
        self.play.setToolTip("Parar o passo em andamento" if running else "Dar andamento nos PRs da FP-060 (Claude)")

    def action_progress(self) -> None:
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            self.report(ctl.Result(False, "instância FP-061 somente leitura: controle desativado"))
            return
        if self.step_process is not None:                                           # o botao vira "Parar passo" enquanto roda
            if self.confirm("Interromper o passo em andamento? O que ele já publicou nos PRs continua lá."):
                self.stop_step()
            return
        if ctl.step_running():
            self.report(ctl.Result(False, "já existe um passo da FP-060 rodando em outra janela: feche-o antes de iniciar outro"))
            return
        if not self.confirm("Dar um passo nos PRs da FP-060 aqui no painel? Uma sessão do Claude (modo sem tela) corrige achados do revisor e mostra o "
                            "progresso no terminal do painel; NUNCA faz merge."):
            return
        cmd = ctl.progress_command()
        if cmd is None:
            self.report(ctl.Result(False, "claude CLI ou arquivo da missão não encontrado (veja AGENT_BAR_MISSION)"))
            return
        self.start_step(*cmd)

    def start_step(self, program: str, args: list[str], cwd) -> None:
        p = QProcess(self)
        p.setProgram(program)
        p.setArguments(args)
        p.setWorkingDirectory(str(cwd))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for name in ("GH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):             # o filho nao herda token do GitHub nem o contexto de outra sessao Claude
            env.remove(name)
        env.insert("PYTHONUTF8", "1")
        p.setProcessEnvironment(env)
        if os.name == "nt" and hasattr(p, "setCreateProcessArgumentsModifier"):
            p.setCreateProcessArgumentsModifier(lambda a: setattr(a, "flags", a.flags | 0x08000000))     # CREATE_NO_WINDOW: sem console extra
        p.readyReadStandardOutput.connect(self._step_read)
        p.finished.connect(lambda code, _status: self._step_done(code))
        p.errorOccurred.connect(self._step_error)
        self.step_process, self._step_buf, self._step_stopped = p, b"", False
        self._set_step_ui(True)
        self.details.term_append("== iniciando o passo da FP-060 ==")
        self.details.set_status(True, "passo em andamento (terminal abaixo)")
        p.start()
        p.closeWriteChannel()                                                        # sem entrada: o claude nao espera 3 s por dados no stdin

    def _step_read(self) -> None:
        p = self.step_process
        if p is None:
            return
        self._step_buf += bytes(p.readAllStandardOutput())
        *lines, self._step_buf = self._step_buf.split(b"\n")
        for raw in lines:
            for out in ctl.format_stream_line(raw.decode("utf-8", errors="replace")):
                self.details.term_append(out)

    def _step_done(self, code: int) -> None:
        if self.step_process is None:
            return
        self._step_read()
        for out in ctl.format_stream_line(self._step_buf.decode("utf-8", errors="replace")):
            self.details.term_append(out)
        self.details.term_append("== passo interrompido ==" if self._step_stopped else f"== passo encerrado (código {code}) ==")
        self._step_finalize()

    def _step_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart and self.step_process is not None:     # `finished` nao e emitido quando nem iniciou
            self.details.term_append(f"== erro ao iniciar o passo: {self.step_process.errorString()} ==")
            self._step_finalize()

    def _step_finalize(self) -> None:
        proc, self.step_process, self._step_buf, self._step_stopped = self.step_process, None, b"", False
        if proc is not None:
            proc.deleteLater()
        self._set_step_ui(False)
        self.details.set_status(True, "passo terminado")
        self.worker.refresh(force=True)

    def stop_step(self) -> None:
        p = self.step_process
        if p is None:
            return
        self._step_stopped = True
        pid = int(p.processId())
        if not pid or not ctl.kill_tree(pid):                                       # por PID, so a arvore do passo que este painel iniciou
            p.kill()

    def action_stop(self) -> None:
        if os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1":
            self.report(ctl.Result(False, "instância FP-061 somente leitura: controle desativado"))
            return
        if self.confirm("Parar o listener (Tarefa Agendada)? Recusa se houver execucao ativa."):
            self.details.set_status(True, "parando o listener...")
            self._run_async(lambda: ctl.stop_listener(confirmed=True, gov=self.gov))

    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Confirmar", text) == QMessageBox.StandardButton.Yes

    def close_app(self) -> None:
        self.stop_step()                                                           # nao deixa um passo orfao quando a barra fecha
        self.worker.stop()
        self.details.close()
        QApplication.quit()

    # ------------------------------------------------------------------------------------------------------ snapshot
    def on_snapshot(self, s: Snapshot) -> None:
        self.snap = s
        self.gear.setText("DEMO" if s.observation.source == "demonstração" else "&#9881;")
        for a in s.agents():
            self.icons[a.name].set_state(a.level, a.level is Level.BLUE)
            tip = f"{a.line()}\n({LEVEL_NAME[a.level]})"
            self.labels[a.name].setToolTip(tip)
            self.icons[a.name].setToolTip(tip)
            if self.settings.notifications and self.tray and a.level is Level.RED and self._levels.get(a.name) not in (None, Level.RED):
                self.tray.showMessage("Agent bar", a.line(), QSystemTrayIcon.MessageIcon.Warning)
            self._levels[a.name] = a.level
        self._sync_animation()
        self.pr.setText(f"PR #{s.pr}" if s.pr else "PR -")
        self.pr.setToolTip(f"checks: {s.github_checks}")
        self.gear.setToolTip(f"{s.mode_banner()}\n{s.work}\n" + (s.last_error or "sem erro observado"))
        self.details.update_snapshot(s)
        if self._refresh_pending:
            self._refresh_pending = None
            self.report(ctl.Result(True, f"atualizado (coleta {hhmm(s.at)})"))
