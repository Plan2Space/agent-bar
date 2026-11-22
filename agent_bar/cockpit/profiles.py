"""profiles.py — agentes ACP disponiveis e workspaces do Plan2Space (R17, D4).

O agente real e o adaptador ACP do Claude Code (`claude-code-acp`, pacote npm `@zed-industries/claude-code-acp`).
Enquanto ele nao estiver instalado, o perfil aparece como indisponivel com o motivo, e o agente de demonstracao (sem
LLM, rotulado) permite exercitar o cockpit.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APPS = Path(r"E:\Claude\apps")


@dataclass(frozen=True)
class AgentProfile:
    key: str
    label: str
    argv: tuple[str, ...]
    demo: bool = False

    def available(self) -> tuple[bool, str]:
        exe = self.argv[0]
        if Path(exe).is_file() or shutil.which(exe):
            return True, ""
        return False, f"'{exe}' não encontrado no PATH"


def profiles() -> list[AgentProfile]:
    custom = os.environ.get("AGENT_BAR_COCKPIT_AGENT", "").strip()
    real = tuple(custom.split()) if custom else ("claude-code-acp",)
    return [
        AgentProfile("claude", "Claude Code (ACP)", real),
        AgentProfile("demo", "Demonstração (sem LLM)", (sys.executable, "-m", "agent_bar.cockpit.demo_agent"), demo=True),
    ]


def workspaces(apps_root: Path | None = None) -> list[Path]:
    """`AGENT_BAR_COCKPIT_WORKSPACES` (separado por ';') ou as pastas de primeiro nivel de E:\\Claude\\apps com .git."""
    env = os.environ.get("AGENT_BAR_COCKPIT_WORKSPACES", "").strip()
    if env:
        return [Path(p).resolve() for p in env.split(";") if p.strip() and Path(p.strip()).is_dir()]
    root = apps_root or DEFAULT_APPS
    if not root.is_dir():
        return []
    return sorted((p.resolve() for p in root.iterdir() if p.is_dir() and (p / ".git").exists()), key=lambda p: p.name.lower())
