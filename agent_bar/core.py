"""core.py — o núcleo do P2S Studio sem janela (UX-01 do P2S-STUDIO-UX-CONSOLIDATION-F0).

Até aqui a coleta, a bandeja, o aviso "PR precisa de você", o status do runtime e o encerramento eram donos da barra
flutuante (`window.Bar`): esconder ou desligar a barra desligaria o aviso de PR. O `AppCore` passa a ser o dono disso
tudo, sem nenhuma janela; a barra e o Cockpit são só vistas sobre ele.

O que o núcleo faz, com ou sem barra:
  - coleta periódica fora da thread de UI (`Worker`);
  - ícone na bandeja e o aviso do Windows quando um PR passa a precisar do Felipe (uma vez por PR@HEAD);
  - o mesmo aviso como item ACTION_REQUIRED em Notificações (o mesmo SQLite que a aba Notificações do Cockpit lê);
  - status do pin do agent-runtime;
  - abrir o Cockpit e encerrar em ordem (agentes do Cockpit, depois a coleta, depois o app).
"""
from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from agent_bar import collectors as col
from agent_bar import controls as ctl
from agent_bar import settings as cfg
from agent_bar.inbox import Inbox, InboxNotifier, human_inbox

ICON = Path(__file__).resolve().parents[1] / "launchers" / "agent_bar.ico"
HUMAN_SOURCE = "agent-bar:precisa-de-voce"


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
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.snapshot.emit(col.collect_snapshot(cache=self._cache, backlog_cache=self._backlog, queue_cache=self._queue,
                                                   approval_cache=self._approvals, claude_login=col.claude_login_state))
            except Exception:                                                  # noqa: BLE001 — coleta nunca derruba a UI
                traceback.print_exc()
            self._now.wait(self.settings.local_interval_s)
            self._now.clear()


class AppCore(QObject):
    """Dono da coleta, da bandeja, do aviso de PR, do runtime e do encerramento. Nenhuma janela.

    `center`: o NotificationCenter onde o aviso vira item (o app passa o do SQLite do Cockpit; sem ele, só a bandeja).
    `quit_fn`: como encerrar o processo no fim (QApplication.quit no app)."""
    snapshot = Signal(object)                                                  # cada coleta, depois de o núcleo tratar

    def __init__(self, settings: cfg.Settings, *, center=None, quit_fn: Callable[[], None] | None = None,
                 tray_available: Callable[[], bool] = QSystemTrayIcon.isSystemTrayAvailable,
                 cockpit_factory: Callable[[], object] | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.center = center
        self.quit_fn = quit_fn or QApplication.quit
        self.cockpit_factory = cockpit_factory
        self.cockpit = None
        self.snap = None
        self.inbox: Inbox | None = None
        self._notice_url: str | None = None
        self.tray = QSystemTrayIcon(self) if tray_available() else None
        if self.tray is not None:                                              # sem icone visivel o Windows nao mostra o aviso
            from PySide6.QtGui import QIcon
            self.tray.setIcon(QIcon(str(ICON)) if ICON.is_file() else QApplication.windowIcon())
            self.tray.setToolTip("P2S Studio")
            self.tray.messageClicked.connect(self._open_notice)
            self.tray.show()
        self.notifier = InboxNotifier(show=self.notify_human, enabled=lambda: self.settings.notify_human)
        from agent_bar import runtime_pin
        self.runtime_pin = runtime_pin.check()
        self.worker = Worker(settings)
        self.worker.snapshot.connect(self.on_snapshot)
        self._closed = False

    def start(self) -> None:
        self.worker.start()

    # ------------------------------------------------------------------------------------------------ coleta
    def on_snapshot(self, s) -> None:
        """Toda coleta passa por aqui, com ou sem barra: o "Precisa de você" e seus avisos não dependem de janela."""
        self.snap = s
        self.inbox = human_inbox(s.approvals)
        for item in self.notifier.update(self.inbox):                          # novo PR@HEAD que precisa do Felipe
            self.publish_human(item)
        self.snapshot.emit(s)

    def publish_human(self, item) -> None:
        if self.center is None:
            return
        try:
            self.center.publish("HUMAN_APPROVAL", f"PR precisa de você: {item.pr_ref}",
                                body=f"{item.risk} · {item.title} · {item.reason} · HEAD {item.head_sha[:7]}",
                                state="ACTION_REQUIRED", source=HUMAN_SOURCE, link=item.url,
                                dedupe_key=f"human:{item.pr_ref}@{item.head_sha}")
        except Exception:                                                     # noqa: BLE001 - aviso nunca derruba a coleta
            traceback.print_exc()

    def notify_human(self, title: str, text: str, url: str) -> None:
        if self.tray is None:
            raise RuntimeError("bandeja do sistema indisponível")
        self._notice_url = url
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 15000)

    def _open_notice(self) -> None:
        if self._notice_url:
            ctl.open_url(self._notice_url)

    # ------------------------------------------------------------------------------------------------ janelas
    def open_cockpit(self):
        """Abre (ou traz para a frente) o Cockpit: janela própria, mesmo processo (SPEC AGENT-BAR-COCKPIT-ACP, D2)."""
        win = self.cockpit
        if win is None or win._closed:                                         # noqa: SLF001
            if self.cockpit_factory is not None:
                win = self.cockpit_factory()
            else:
                from agent_bar.cockpit.ui.window import CockpitWindow
                win = CockpitWindow()
            self.cockpit = win
        win.show()
        win.raise_()
        win.activateWindow()
        win.chat.ensure_client()
        return win

    # ------------------------------------------------------------------------------------------------ encerrar
    def shutdown(self) -> None:
        """Encerra em ordem: os agentes que o Cockpit iniciou, a coleta, a bandeja; depois o processo."""
        if self._closed:
            return
        self._closed = True
        if self.cockpit is not None:                                           # encerra so os agentes ACP que o cockpit iniciou
            self.cockpit.shutdown()
        self.worker.stop()
        if self.tray is not None:
            self.tray.hide()
        if self.center is not None:
            self.center.close()
        self.quit_fn()


__all__ = ["HUMAN_SOURCE", "AppCore", "Worker"]
