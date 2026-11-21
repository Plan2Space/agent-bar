"""single_instance.py — uma barra por usuario. A segunda abertura pede para a primeira vir para a frente e sai.

QLockFile decide quem e a primeira (lock velho de processo morto e recuperado pelo proprio Qt); QLocalServer recebe o
pedido de "venha para a frente" da segunda. Nao mata processo nenhum.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

DEFAULT_KEY = "plan2space-agent-bar"


class SingleInstance:
    def __init__(self, key: str = DEFAULT_KEY, lock_dir: Path | None = None):
        self.key = key
        self.lock_dir = lock_dir or Path.home()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(self.lock_dir / f"{key}.lock"))
        self._lock.setStaleLockTime(0)                                      # so o PID decide se o lock e velho, nao a idade
        self._server: QLocalServer | None = None

    def acquire(self, on_activate: Callable[[], None]) -> bool:
        """True = esta e a instancia unica. False = ja existe uma; ela foi avisada para vir para a frente."""
        if not self._lock.tryLock(100):
            self._lock.removeStaleLockFile()
            if not self._lock.tryLock(100):
                self._notify_running()
                return False
        QLocalServer.removeServer(self.key)                                  # servidor orfao de uma instancia que morreu
        self._server = QLocalServer()
        self._server.newConnection.connect(lambda: self._on_connection(on_activate))
        self._server.listen(self.key)
        return True

    def _on_connection(self, on_activate: Callable[[], None]) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.disconnectFromServer()
            on_activate()

    def _notify_running(self) -> None:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if sock.waitForConnected(1000):
            sock.write(b"activate\n")
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()
