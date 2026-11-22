"""store.py — dados locais do cockpit em %LOCALAPPDATA%\\agent_bar\\cockpit (R16, D8). Toda escrita e atomica.

O historico guarda so o texto da conversa e metadados (workspace, modo, agente, sessionId). Nunca credencial (R34).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    base = os.environ.get("AGENT_BAR_COCKPIT_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"), "agent_bar", "cockpit")
    return Path(base)


def atomic_write_json(path: Path, obj: Any) -> None:
    """temporario no mesmo diretorio + os.replace: quem le ve o arquivo antigo ou o novo, nunca pela metade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Chat:
    id: str
    title: str = "Nova conversa"
    workspace: str = ""
    agent: str = ""
    mode: str = ""
    session_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    messages: list[dict] = field(default_factory=list)

    def add(self, role: str, text: str = "", **extra: Any) -> dict:
        msg = {"role": role, "text": text, **extra}
        self.messages.append(msg)
        if role == "user" and self.title == "Nova conversa" and text.strip():
            self.title = re.sub(r"\s+", " ", text.strip())[:60]
        return msg

    @classmethod
    def from_dict(cls, d: dict) -> Chat:
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        chat = cls(**known)
        chat.messages = [m for m in chat.messages if isinstance(m, dict)]
        return chat


_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChatStore:
    def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.root = (root or data_dir()) / "chats"
        self.clock = clock

    def new(self, workspace: str = "", agent: str = "", mode: str = "") -> Chat:
        now = self.clock()
        return Chat(id=uuid.uuid4().hex[:12], workspace=workspace, agent=agent, mode=mode, created_at=now, updated_at=now)

    def path(self, chat_id: str) -> Path:
        if not _ID.match(chat_id):
            raise ValueError(f"id de conversa invalido: {chat_id!r}")
        return self.root / f"{chat_id}.json"

    def save(self, chat: Chat) -> Path:
        chat.updated_at = self.clock()
        p = self.path(chat.id)
        atomic_write_json(p, asdict(chat))
        return p

    def load(self, chat_id: str) -> Chat:
        return Chat.from_dict(json.loads(self.path(chat_id).read_text(encoding="utf-8")))

    def list(self) -> list[Chat]:
        """Conversas da mais recente para a mais antiga. Arquivo corrompido e pulado, nao derruba a lista."""
        out: list[Chat] = []
        if not self.root.is_dir():
            return out
        for p in self.root.glob("*.json"):
            try:
                out.append(Chat.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(out, key=lambda c: (-c.updated_at, c.id))
