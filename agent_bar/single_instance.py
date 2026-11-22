"""single_instance.py — um P2S Studio por usuario. A segunda abertura pede para a primeira vir para a frente e sai.

Quem esta rodando e quem RESPONDE no canal local `plan2space-agent-bar` (QLocalServer): o named pipe so existe enquanto o
processo vive, entao a sondagem nao erra por PID reaproveitado nem por nome de executavel. A chave nao mudou com o rename
para "P2S Studio": a versao antiga escuta no mesmo nome e tambem e encontrada. Entre duas aberturas novas simultaneas,
quem decide e um lock de arquivo do sistema operacional (solto pelo proprio SO se o processo morrer).

Historico: ate 2026-11-22 a decisao era do QLockFile, mas no Windows o Qt considera "velho" o lock de um processo vivo
quando o nome do executavel nao bate com o applicationName (e, no venv, o PID gravado e o do interpretador, nao o do
lancador) e o apagava: duas instancias chegavam a rodar juntas. O QLockFile continua sendo criado so para a versao antiga.

Mensagens: "activate" (venha para a frente, padrao) ou "quit" (feche-se: ha uma versao nova querendo abrir). A instancia
viva grava `<chave>.json` (commit do checkout, pid, inicio) para quem chega depois oferecer "versao nova disponivel".
Nao mata processo nenhum: o "quit" e um pedido que a instancia antiga atende fechando-se normalmente.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

DEFAULT_KEY = "plan2space-agent-bar"                                       # NAO renomear: a versao antiga usa esta chave


class _OsLock:
    """Lock exclusivo de 1 byte num arquivo, sem espera; o SO solta quando o processo termina."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        try:
            fh = open(self.path, "a+b")                                      # noqa: SIM115 - fica aberto enquanto o lock vale
        except OSError:
            return False
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


class SingleInstance:
    def __init__(self, key: str = DEFAULT_KEY, lock_dir: Path | None = None, version: str = ""):
        self.key = key
        self.version = version
        self.lock_dir = lock_dir or Path.home()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.info_path = self.lock_dir / f"{key}.json"
        self._os_lock = _OsLock(self.lock_dir / f"{key}.instance")
        self._qt_lock = QLockFile(str(self.lock_dir / f"{key}.lock"))       # so para a versao antiga enxergar algo
        self._qt_lock.setStaleLockTime(0)
        self._server: QLocalServer | None = None
        self._outgoing: list[QLocalSocket] = []

    def running(self) -> bool:
        """Alguma instancia (nova ou antiga) responde no canal local agora?"""
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        alive = sock.waitForConnected(300)
        if alive:                                                            # "ping" e ignorado pelo servidor (nao traz a janela a frente)
            sock.write(b"ping\n")
            sock.flush()
            sock.waitForBytesWritten(200)
            self._outgoing.append(sock)
        return alive

    def acquire(self, on_activate: Callable[[], None], on_quit: Callable[[], None] | None = None, notify: bool = True) -> bool:
        """True = esta e a instancia unica. False = ja existe uma; com `notify`, ela foi avisada para vir para a frente."""
        if self._server is not None:
            return True
        if self.running() or not self._os_lock.acquire():
            if notify:
                self.send("activate")
            return False
        if self.running():                                                   # alguem antigo subiu entre a sondagem e o lock
            self._os_lock.release()
            if notify:
                self.send("activate")
            return False
        self._qt_lock.tryLock(0)
        QLocalServer.removeServer(self.key)                                  # servidor orfao de uma instancia que morreu
        self._server = QLocalServer()
        self._server.newConnection.connect(lambda: self._on_connection(on_activate, on_quit))
        if not self._server.listen(self.key):
            self._server = None
            self._qt_lock.unlock()
            self._os_lock.release()
            return False
        self._write_info()
        return True

    def _write_info(self) -> None:
        tmp = self.info_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"version": self.version, "pid": os.getpid(), "started": time.time()}), encoding="utf-8")
            os.replace(tmp, self.info_path)
        except OSError:
            pass                                                             # sem o arquivo so nao ha aviso de versao nova

    def running_info(self) -> dict | None:
        try:
            data = json.loads(self.info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def newer_available(self) -> bool:
        """Esta abertura e de um commit diferente do da instancia que esta rodando? Sem dado de algum lado = nao sabe = False."""
        info = self.running_info()
        running = str((info or {}).get("version") or "")
        return bool(self.version and running and running != self.version)

    def _on_connection(self, on_activate: Callable[[], None], on_quit: Callable[[], None] | None) -> None:
        """Sem leitura bloqueante: acumula o que chegar e decide na primeira linha ou quando o cliente fecha.
        "ping" (sondagem de `running()`) e ignorado; mensagem desconhecida ou vazia = "activate" (comportamento antigo)."""
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            buf: list[bytes] = []
            done = [False]

            def finish(s=sock, b=buf, d=done) -> None:
                if d[0]:
                    return
                d[0] = True
                b.append(bytes(s.readAll()))
                msg = b"".join(b).strip()
                s.disconnectFromServer()
                s.deleteLater()
                if msg == b"ping":
                    return                                                   # sondagem de `running()`
                if msg == b"quit" and on_quit is not None:
                    on_quit()
                else:                                                        # vazio = cliente fechou antes de entregar: activate
                    on_activate()

            def read(s=sock, b=buf) -> None:
                b.append(bytes(s.readAll()))
                if b"\n" in b"".join(b):
                    finish()

            sock.readyRead.connect(read)
            sock.disconnected.connect(finish)
            if sock.bytesAvailable():
                read()

    def send(self, message: str) -> bool:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if not sock.waitForConnected(1000):
            return False
        sock.write(message.encode("ascii") + b"\n")
        sock.flush()
        sock.waitForBytesWritten(500)
        self._outgoing.append(sock)                                          # NAO desconecta: quem fecha e o servidor depois de ler
        return True

    def release(self) -> None:
        for sock in self._outgoing:
            sock.abort()
        self._outgoing.clear()
        if self._server is not None:
            self._server.close()
            self._server = None
            info = self.running_info()
            if info and info.get("pid") == os.getpid():
                try:
                    self.info_path.unlink()
                except OSError:
                    pass
            self._qt_lock.unlock()
            self._os_lock.release()
