"""permission_mode.py — como o P2S Studio responde aos pedidos de permissão de ferramenta dos agentes. Sem Qt.

Decisão do Felipe (2026-11-24, literal): "CARA E ESSE NEGOCIO DE PERMITIR SO UMA VEZ NAO EXISTE DEIXA AS PERMISSOES DOS
AGENTS BYPASS SEMPRE". Padrão: BYPASS — o Studio aprova sozinho e REGISTRA cada aprovação (Inbox + Notificações).
Reversível em Configurações → Geral ("pedir permissão"). O portão de operação irreversível (código digitado) não é
"permitir uma vez" e continua até o Felipe decidir sobre ele; a política IRREVERSIBLE do Inbox também o exige.

`AGENT_BAR_PERMISSION_MODE` (bypass|ask) sobrepõe o arquivo: os testes do fluxo de perguntar fixam "ask".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_bar.cockpit.store import atomic_write_json, data_dir

BYPASS, ASK = "bypass", "ask"
ENV = "AGENT_BAR_PERMISSION_MODE"
LABEL = {BYPASS: "permissões: bypass (sempre)", ASK: "permissões: pedir a cada ferramenta"}


def mode_path() -> Path:
    return data_dir() / "permission_mode.json"


def current(path: Path | None = None) -> str:
    env = os.environ.get(ENV, "").strip().lower()
    if env in (BYPASS, ASK):
        return env
    p = path or mode_path()
    try:
        mode = json.loads(p.read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        return BYPASS                                                          # padrão do produto
    return mode if mode in (BYPASS, ASK) else BYPASS


def set_mode(mode: str, path: Path | None = None) -> None:
    if mode not in (BYPASS, ASK):
        raise ValueError(f"modo de permissão desconhecido: {mode!r}")
    atomic_write_json(path or mode_path(), {"mode": mode})


def label(mode: str | None = None) -> str:
    return LABEL[mode or current()]


__all__ = ["ASK", "BYPASS", "ENV", "LABEL", "current", "label", "mode_path", "set_mode"]
