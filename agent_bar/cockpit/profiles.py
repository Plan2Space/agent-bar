"""profiles.py — agentes ACP disponiveis e workspaces do Plan2Space (R17, D4).

O agente real e o adaptador ACP do Claude Code (`claude-agent-acp`, pacote npm oficial `@agentclientprotocol/claude-agent-acp`;
os nomes `@zed-industries/*` estao depreciados e o `claude-agent-acp` sem escopo e um fork de terceiro).
Enquanto ele nao estiver instalado, o perfil aparece como indisponivel com o motivo.

Seletor do Chat (pedido do Felipe, 2026-11-28): so agentes reais e utilizaveis, com nome simples. "Claude" sempre (se
faltar, aparece indisponivel com o motivo), "Ollama · <modelo>" um por modelo instalado e "Codex" se o `codex-acp`
estiver no PATH. Se o modelo anuncia ferramentas e o motor do Studio esta pronto, a entrada do Ollama usa o executor;
senao conversa. Isso e decidido por baixo (adaptador "ollama-auto"), nao pelo usuario. O executor de amostra e a
demonstracao sem LLM so aparecem no modo avancado (AGENT_BAR_COCKPIT_ADVANCED_AGENTS=1).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APPS = Path(r"E:\Claude\apps")
ADVANCED_ENV = "AGENT_BAR_COCKPIT_ADVANCED_AGENTS"
OLLAMA_TAGS_TIMEOUT_S = 1.0                                                  # localhost: desligado recusa na hora


@dataclass(frozen=True)
class AgentProfile:
    key: str
    label: str
    argv: tuple[str, ...]
    demo: bool = False
    adapter: str = "acp"                                                     # "acp" ou um adaptador da plataforma (ex.: "ollama")
    model: str = ""                                                          # Ollama: o modelo desta entrada
    advanced: bool = False                                                   # so no modo avancado (rotulo tecnico)

    def available(self) -> tuple[bool, str]:
        if self.adapter == "ollama-executor":                                # P2S-LOCAL-EXECUTOR: so com o motor pronto
            from agent_bar.platform.sketchup_sample import preflight_from_env
            ok, why = preflight_from_env()
            return ok, ("" if ok else f"executor local indisponível: {why}")
        if self.adapter == "ollama-auto":
            return True, ""                                                  # so entra na lista se o modelo esta instalado
        if self.adapter == "ollama" and os.environ.get("OLLAMA_HOST", "").strip():
            return True, ""                                                  # servidor configurado; o connect confirma
        exe = self.argv[0]
        if Path(exe).is_file() or shutil.which(exe):
            return True, ""
        return False, f"'{exe}' não encontrado no PATH"


def _installed_ollama_models() -> list[str]:
    """Modelos instalados no Ollama (GET /api/tags, timeout curto). Servidor desligado = lista vazia."""
    from agent_bar.platform.discovery import discover
    try:
        endpoint = next(p["endpoint"] for p in discover(which=lambda _n: None) if p["provider_id"] == "ollama")
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=OLLAMA_TAGS_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, StopIteration):
        return []
    names = [str(m.get("name") or m.get("model") or "") for m in data.get("models") or [] if isinstance(m, dict)]
    return sorted({n for n in names if n})


def advanced_agents() -> bool:
    return os.environ.get(ADVANCED_ENV, "").strip() == "1"


def profiles() -> list[AgentProfile]:
    custom = os.environ.get("AGENT_BAR_COCKPIT_AGENT", "").strip()
    real = tuple(custom.split()) if custom else ("claude-agent-acp",)
    out = [AgentProfile("claude", "Claude", real)]
    out += [AgentProfile(f"ollama:{m}", f"Ollama · {m}", ("ollama",), adapter="ollama-auto", model=m)
            for m in _installed_ollama_models()]
    if shutil.which("codex-acp"):
        out.append(AgentProfile("codex", "Codex", ("codex-acp",)))
    if advanced_agents():
        out += [AgentProfile("ollama-executor", "Executor local (amostra)", ("ollama",), adapter="ollama-executor",
                             advanced=True),
                AgentProfile("demo", "Demonstração (sem LLM)", (sys.executable, "-m", "agent_bar.cockpit.demo_agent"),
                             demo=True, advanced=True)]
    return out


def workspaces(apps_root: Path | None = None) -> list[Path]:
    """`AGENT_BAR_COCKPIT_WORKSPACES` (separado por ';') ou as pastas de primeiro nivel de E:\\Claude\\apps com .git.

    FR-02: `DEFAULT_APPS` e so uma SUGESTAO quando existe. Sem ela (maquina de outra pessoa), a lista vem vazia e o Chat
    mostra "Escolher pasta…"; a escolha fica lembrada em `project_folders`."""
    env = os.environ.get("AGENT_BAR_COCKPIT_WORKSPACES", "").strip()
    if env:
        return [Path(p).resolve() for p in env.split(";") if p.strip() and Path(p.strip()).is_dir()]
    root = apps_root or DEFAULT_APPS
    if not root.is_dir():
        return []
    return sorted((p.resolve() for p in root.iterdir() if p.is_dir() and (p / ".git").exists()), key=lambda p: p.name.lower())
