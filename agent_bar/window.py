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
    QMenu,
    QMessageBox,
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
from agent_bar.inbox import Inbox, InboxNotifier, human_inbox, waited
from agent_bar.models import Level, Snapshot
from agent_bar.operations_view import OperationsView

COLORS = {Level.GREEN: "#3ddc84", Level.BLUE: "#4aa8ff", Level.YELLOW: "#ffc233", Level.RED: "#ff4d4f", Level.GRAY: "#8a8f98"}
LEVEL_NAME = {Level.GREEN: "conectado", Level.BLUE: "trabalhando", Level.YELLOW: "atencao", Level.RED: "erro", Level.GRAY: "desconectado / sem integracao"}
BTN_CSS = ("QPushButton{background:#2a2a30;color:#ddd;border:0;border-radius:6px;padding:4px 6px;font-size:11px}"
           "QPushButton:hover{background:#3a3a44}QPushButton:disabled{color:#666}")


AGENT_COLOR = {"Claude": "#d97757", "GitHub": "#e6e6e6"}
BAR_W = 640
_CUSTOM: dict[str, QPixmap | None] = {}


def icons_dir() -> Path:
    return Path(os.environ.get("AGENT_BAR_ICONS") or Path(os.environ.get("APPDATA", ".")) / "agent_bar" / "icons")


def custom_logo(name: str) -> QPixmap | None:
    """Logo oficial que o dono colocar em `%APPDATA%\\agent_bar\\icons\\<agente>.png` (claude.png, github.png) substitui o desenho padrao."""
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


def _logo_orbit(p: QPainter, size: float, phase: int, color: str) -> None:
    """Anel com um ponto em orbita: consultando o GitHub."""
    p.setPen(_pen("#4a4f57", size * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(size * 0.12, size * 0.12, size * 0.76, size * 0.76))
    a = math.radians(phase * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(size / 2 + math.cos(a) * size * 0.38, size / 2 + math.sin(a) * size * 0.38), size * 0.11, size * 0.11)


LOGO_PAINTERS = {"Claude": _logo_claude, "GitHub": _logo_orbit}


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
    """Coleta periodica fora da thread de UI. Local a cada `local_interval_s`; GitHub e Project #2 com cache/TTL proprios."""
    snapshot = Signal(object)

    def __init__(self, settings: cfg.Settings):
        super().__init__()
        self.settings = settings
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
                self.snapshot.emit(col.collect_snapshot(cache=self._cache, backlog_cache=self._backlog, queue_cache=self._queue,
                                                   approval_cache=self._approvals, claude_login=col.claude_login_state))
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
    url = b.url or f"https://github.com/orgs/{b.org}/projects/{b.number}"
    name = esc(b.title or f"{b.org} Project #{b.number}")
    head = (f"<b>Board</b> <a href=\"{esc(url)}\" style='color:#8fb4ff'>{name}</a> "
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


def inbox_html(inbox: Inbox) -> str:
    """Topo do painel: o que espera o Felipe (HIGH aguardando no HEAD atual), do mais antigo para o mais novo."""
    head = "<b style='color:#ffc233'>Precisa de você</b>"
    if inbox.state != "lido":
        return f"{head} <b style='color:#ff6b6d'>INDISPONÍVEL</b> <span style='color:#ff9a9b'>{esc(inbox.reason)}</span>"
    extra = f" <span style='color:#ff9a9b'>({inbox.unreadable} PR(s) não lidos: erro de leitura)</span>" if inbox.unreadable else ""
    when = f" <span style='color:#6d6d75'>(lido {hhmm(inbox.collected_at)})</span>" if inbox.collected_at else ""
    if not inbox.items:
        return f"{head} <span style='color:#3ddc84'>nada esperando você</span>{extra}{when}"
    rows = []
    for i in inbox.items:
        name = esc(i.pr_ref.split("/")[-1])
        link = f"<a href=\"{esc(i.url)}\" style='color:#8fb4ff'>{name}</a>" if i.url.startswith("https://github.com/") else name
        if i.reviewer_verdict:
            rlink = f" <a href=\"{esc(i.reviewer_url)}\" style='color:#8fb4ff'>ver</a>" if i.reviewer_url.startswith("https://github.com/") else ""
            rec = f"revisor {esc(inbox.reviewer)}: <b>{esc(i.reviewer_verdict)}</b> {esc(i.reviewer_note[:90])}{rlink}"
        else:
            rec = f"revisor {esc(inbox.reviewer)}: sem recomendação neste HEAD"
        rows.append(f"&bull; {link} <span style='color:#9a9aa2'>{esc(i.title[:60])}</span><br>&nbsp;&nbsp;<b>{esc(i.risk)}</b> "
                    f"<span style='color:#9a9aa2'>{esc(i.reason[:90])}</span><br>&nbsp;&nbsp;HEAD {esc(i.head_sha[:7])} · CI {esc(i.ci_state)}"
                    f" · espera há {waited(i.waiting_s)}<br>&nbsp;&nbsp;<span style='color:#9a9aa2'>{rec}</span>")
    return f"{head} ({len(inbox.items)}){extra}{when}<br>" + "<br>".join(rows)


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
    def link(r) -> str:                                                           # so abre links https://github.com/
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
        self.title = QLabel("P2S Studio")
        self.title.setStyleSheet("color:#f0f0f0;font-size:12px;font-weight:600")
        lay.addWidget(self.title)
        lay.addStretch(1)
        self.btn_min = self._btn("\u2014", "Recolher o painel (so a barra de titulo); a barra flutuante continua", panel.toggle_collapsed)
        self.btn_max = self._btn("\u25a1", "Restaurar / ajustar o tamanho ao conteudo", panel.restore_fit)
        self.btn_close = self._btn("\u2715", "Fechar SOMENTE o painel (a coleta da barra continua)", panel.close_panel, TB_CLOSE_CSS)
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
        self.setWindowTitle("P2S Studio")
        self.bar = bar
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
        self.pin_warning = QLabel()                                              # agent-runtime instalado != fixado no pyproject
        self.pin_warning.setWordWrap(True)
        self.pin_warning.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)   # o comando pode ser copiado
        self.pin_warning.setStyleSheet("color:#ffd0d0;font-size:11px;background:#3a1717;border:1px solid #7a2a2a;border-radius:8px;padding:6px")
        self.pin_warning.setVisible(False)
        lay.addWidget(self.pin_warning)
        self.human = QLabel()                                                    # "Precisa de você": primeira coisa do painel
        self.human.setTextFormat(Qt.TextFormat.RichText)
        self.human.setWordWrap(True)
        self.human.setOpenExternalLinks(True)
        self.human.setStyleSheet("color:#e6e6e6;font-size:11px;background:#2a2516;border:1px solid #5a4a1f;border-radius:8px;padding:6px")
        lay.addWidget(self.human)
        self.agents, self.backlog = QLabel(), QLabel()
        for w in (self.agents, self.backlog):
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
        specs = [("repo", "Org GitHub", bar.action_repo), ("project", f"Project #{ctl._TARGETS.project}", bar.action_project),
                 ("diag", "Copiar diagnóstico", bar.action_copy_diag), ("refresh", "Atualizar", bar.action_refresh),
                 ("cockpit", "Cockpit ACP", bar.action_cockpit)]
        for i, (key, text, fn) in enumerate(specs):
            b = QPushButton(text)
            b.setStyleSheet(BTN_CSS)
            b.clicked.connect(lambda _=False, f=fn: f())
            self.buttons[key] = b
            grid.addWidget(b, i // 4, i % 4)
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
        """Fecha SOMENTE o painel. Nao toca na coleta da barra."""
        self.hide()

    def toggle_full_mode(self) -> None:
        """Expanded and full are views of this same panel and collector."""
        self.full_mode = not self.full_mode
        self.setFixedWidth(self.FULL_WIDTH if self.full_mode else self.WIDTH)
        self.operations.setVisible(self.full_mode)
        self.backlog.setVisible(not self.full_mode)
        self.mode_button.setText("Painel expandido  ↙" if self.full_mode else "Central completa  ↗")
        title = "P2S Studio · Central completa" if self.full_mode else "P2S Studio"
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

    def update_snapshot(self, s: Snapshot) -> None:
        self.human.setText(inbox_html(getattr(self.bar, "inbox", None) or human_inbox(s.approvals)))
        rows = []
        for a in s.agents():
            pid = f" <span style='color:#8a8f98'>pid {a.pid}</span>" if a.pid else ""
            rows.append(f"{dot(a.level, a.name)} <b>{a.name}</b> {esc(a.label)}{pid}<br><span style='color:#8f8f98'>&nbsp;&nbsp;&nbsp;{esc(a.detail)}</span>")
        self.agents.setText("<br>".join(rows))
        self.queue.setText(queue_html(s.pr_queue))
        self.backlog.setText(board_html(s.board))
        obs = s.observation
        if obs.source == "demonstração":
            self.titlebar.title.setText(("P2S Studio · Central completa" if self.full_mode else "P2S Studio") + " · DEMONSTRAÇÃO")
        else:
            self.titlebar.title.setText("P2S Studio · Central completa" if self.full_mode else "P2S Studio")
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
        self.operations.update_runs(s.runs)
        self.operations.update_benchmark(s.benchmark)
        self.operations.update_approvals(s.approvals, observe_only=os.environ.get("AGENT_BAR_OBSERVE_ONLY") == "1")
        self.fit()


class Bar(QWidget):
    result = Signal(bool, str)

    def __init__(self, settings: cfg.Settings):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("P2S Studio")
        self.settings = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(settings.opacity)
        self.resize(BAR_W, 65)
        self.labels: dict[str, QLabel] = {}
        self.icons: dict[str, StatusIcon] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 6, 12, 6)
        lay.setSpacing(14)
        for name in ("Claude", "GitHub"):
            icon = StatusIcon(name)
            lb = QLabel(name)
            lb.setStyleSheet("color:#f0f0f0;font-size:13px;font-weight:600")
            self.icons[name], self.labels[name] = icon, lb
            chip = QHBoxLayout()
            chip.setSpacing(5)
            chip.addWidget(icon)
            chip.addWidget(lb)
            lay.addLayout(chip)
        self.pr = QLabel("PRs -")
        self.pr.setStyleSheet("color:#bdbdc4;font-size:12px")
        lay.addWidget(self.pr)
        self.human_badge = QLabel()                                              # contador "para voce" (decisao humana pendente)
        self.human_badge.setVisible(False)
        lay.addWidget(self.human_badge)
        self.inbox: Inbox | None = None
        lay.addStretch(1)
        self.cockpit_btn = QPushButton("Cockpit")                                 # abre o cockpit ACP (chat, fluxos, revisao visual)
        self.cockpit_btn.setFixedHeight(28)
        self.cockpit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cockpit_btn.setStyleSheet("QPushButton{background:#2a2a30;color:#e6e6e6;border:0;border-radius:14px;font-size:12px;padding:0 12px}"
                                       "QPushButton:hover{background:#3a3a44}")
        self.cockpit_btn.setToolTip("Abrir o cockpit ACP")
        self.cockpit_btn.clicked.connect(lambda _=False: self.action_cockpit())
        lay.addWidget(self.cockpit_btn)
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
        self._refresh_pending: float | None = None
        self.tray = QSystemTrayIcon(self) if QSystemTrayIcon.isSystemTrayAvailable() else None
        self._notice_url: str | None = None
        if self.tray is not None:                                                     # sem icone visivel o Windows nao mostra o aviso
            from PySide6.QtGui import QIcon
            ico = Path(__file__).resolve().parents[1] / "launchers" / "agent_bar.ico"
            self.tray.setIcon(QIcon(str(ico)) if ico.is_file() else QApplication.windowIcon())
            self.tray.setToolTip("P2S Studio")
            self.tray.messageClicked.connect(self._open_notice)
            self.tray.show()
        self.notifier = InboxNotifier(show=self._notify_human, enabled=lambda: self.settings.notify_human)
        self.worker = Worker(settings)
        self.details = Details(self)
        from agent_bar import runtime_pin
        self.set_runtime_pin(runtime_pin.check())
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
        h = QAction("Avisar quando um PR precisar de mim", m, checkable=True, checked=self.settings.notify_human)
        h.toggled.connect(self.set_notify_human)
        m.addAction(h)
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
        for w in (*self.labels.values(), *self.icons.values(), self.pr, self.cockpit_btn, self.gear):
            w.setVisible(not mini)
        self._sync_badge()
        self.resize(44 if mini else BAR_W, 44 if mini else 65)
        if mini:
            self.details.hide()

    def _notify_human(self, title: str, text: str, url: str) -> None:
        if self.tray is None:
            raise RuntimeError("bandeja do sistema indisponível")
        self._notice_url = url
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 15000)

    def set_runtime_pin(self, st) -> None:
        """So divergencia comprovada vira o aviso vermelho; 'nao deu para saber' fica no log e no tooltip da engrenagem."""
        self.runtime_pin = st
        w = self.details.pin_warning
        w.setText(st.message if st.ok is False else "")
        w.setVisible(st.ok is False)
        self.gear.setToolTip(f"runtime: {st.message}")

    def _open_notice(self) -> None:
        if self._notice_url:
            self.report(ctl.open_url(self._notice_url))

    def set_notify_human(self, on: bool) -> None:
        self.settings.notify_human = on
        cfg.save(self.settings)

    def _sync_badge(self) -> None:
        inbox = self.inbox
        if inbox is None or self.settings.minimized:
            self.human_badge.setVisible(False)
            return
        if inbox.state != "lido":
            self.human_badge.setText("<span style='color:#8a8f98;font-size:12px'>&#9873; ? para você</span>")
            self.human_badge.setToolTip(f"Precisa de você: indisponível ({inbox.reason})")
            self.human_badge.setVisible(True)
            return
        n = len(inbox.items)
        self.human_badge.setText(f"<b style='color:#ffc233;font-size:12px'>&#9873; {n} para você</b>")
        self.human_badge.setToolTip("\n".join(f"{i.pr_ref} {i.risk} HEAD {i.head_sha[:7]} · espera há {waited(i.waiting_s)}" for i in inbox.items))
        self.human_badge.setVisible(n > 0)

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

    def confirm(self, text: str) -> bool:
        return QMessageBox.question(self, "Confirmar", text) == QMessageBox.StandardButton.Yes

    def action_cockpit(self) -> None:
        """Abre (ou traz para a frente) o cockpit ACP: janela propria, mesmo processo (SPEC AGENT-BAR-COCKPIT-ACP, D2)."""
        win = getattr(self, "cockpit", None)
        if win is None or win._closed:                                             # noqa: SLF001
            from agent_bar.cockpit.ui.window import CockpitWindow
            win = CockpitWindow()
            self.cockpit = win
        win.show()
        win.raise_()
        win.activateWindow()
        win.chat.ensure_client()

    def close_app(self) -> None:
        if getattr(self, "cockpit", None) is not None:                             # encerra so os agentes ACP que o cockpit iniciou
            self.cockpit.shutdown()
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
                self.tray.showMessage("P2S Studio", a.line(), QSystemTrayIcon.MessageIcon.Warning)
            self._levels[a.name] = a.level
        self._sync_animation()
        self.inbox = human_inbox(s.approvals)
        self._sync_badge()
        self.notifier.update(self.inbox)
        q = s.pr_queue
        self.pr.setText(f"PRs {len(q.rows)}" if not (q.error and not q.rows) else "PRs ?")
        self.pr.setToolTip("fila de PRs da org: " + (", ".join(f"{k} {v}" for k, v in q.counts().items()) or "nenhum aberto")
                           + (f" (erro: {q.error})" if q.error else ""))
        self.gear.setToolTip(f"runtime: {self.runtime_pin.message}" if getattr(self, "runtime_pin", None) else "P2S Studio")
        self.details.update_snapshot(s)
        if self._refresh_pending:
            self._refresh_pending = None
            self.report(ctl.Result(True, f"atualizado (coleta {hhmm(s.at)})"))
